"""
Hermes Web UI -- SQLite-based user authentication.

Provides username + password registration/login stored in STATE_DIR/users.db.
Sessions map token -> (username, expiry) so the UI can display the logged-in
username without an extra lookup.

This module replaces the single-password auth (api/auth.py) as the auth gate.
Auth is always enabled; every non-public path requires a valid session.
"""
import hashlib
import hmac
import http.cookies
import logging
import os
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path

from api.config import STATE_DIR

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
COOKIE_NAME = "hermes_session"
CSRF_HEADER_NAME = "X-Hermes-CSRF-Token"
SESSION_TTL = 86400 * 30  # 30 days
DB_PATH = STATE_DIR / "users.db"

# Rate limiter
_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW = 60  # seconds

# In-memory sessions: token -> {"username": str, "expiry": float}
_sessions: dict[str, dict] = {}

# ── Database setup ────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """Return a thread-safe connection to the users DB."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Handle schema migration if needed
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if cols and "salt" in cols:
        try:
            old_users = conn.execute("SELECT username FROM users").fetchall()
            conn.execute("DROP TABLE IF EXISTS users")
            conn.commit()
            logger.info(
                "Migrated users table schema. %d users need to re-register.",
                len(old_users),
            )
        except Exception as e:
            logger.warning("Schema migration failed: %s", e)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT  NOT NULL,
            created_at  REAL    NOT NULL DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()
    return conn


def _ensure_db():
    """Ensure DB and table exist (called at startup)."""
    try:
        conn = _get_db()
        conn.close()
    except Exception as e:
        logger.warning("Failed to initialise users.db: %s", e)


_ensure_db()


# ── Signing key ───────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    """Return or generate the per-installation signing key."""
    key_file = STATE_DIR / ".signing_key"
    try:
        if key_file.exists():
            raw = key_file.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except Exception:
        pass
    key = secrets.token_bytes(32)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
    except Exception:
        pass
    return key


# ── Password hashing ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """PBKDF2-SHA256 with 260k iterations. Salt = per-installation signing key."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _signing_key(), 260_000)
    return dk.hex()


def _verify_password(plain: str, stored_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(plain), stored_hash)


# ── User management ───────────────────────────────────────────────────────────

def register_user(username: str, password: str) -> tuple[bool, str]:
    """
    Register a new user. Returns (ok, error_message).
    ok=True means the user was created successfully.
    """
    username = username.strip()
    if not username or len(username) < 2:
        return False, "用户名至少需要2个字符"
    if len(username) > 64:
        return False, "用户名不能超过64个字符"
    if not password or len(password) < 4:
        return False, "密码至少需要4个字符"
    try:
        conn = _get_db()
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, _hash_password(password)),
        )
        conn.commit()
        conn.close()
        logger.info("User registered: %s", username)
        return True, ""
    except sqlite3.IntegrityError:
        return False, "该用户名已被注册"
    except Exception as e:
        logger.error("register_user error: %s", e)
        return False, "注册失败，请重试"


def authenticate_user(username: str, password: str) -> tuple[bool, str]:
    """
    Verify username + password. Returns (ok, stored_username_or_error).
    ok=True → stored_username (canonical case from DB)
    ok=False → error message string
    """
    username = username.strip()
    if not username or not password:
        return False, "请输入用户名和密码"
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        conn.close()
    except Exception as e:
        logger.error("authenticate_user db error: %s", e)
        return False, "服务器错误，请重试"

    if row is None:
        return False, "用户名或密码错误"
    if not _verify_password(password, row["password_hash"]):
        return False, "用户名或密码错误"
    return True, row["username"]


def get_user_count() -> int:
    """Return the number of registered users."""
    try:
        conn = _get_db()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts


# ── Session management ────────────────────────────────────────────────────────

def create_session(username: str) -> str:
    """Create a new session token for *username*. Returns signed cookie value."""
    token = secrets.token_hex(32)
    _sessions[token] = {"username": username, "expiry": time.time() + SESSION_TTL}
    sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{sig}"


def verify_session(cookie_value: str) -> dict | None:
    """
    Verify a signed session cookie.
    Returns {"username": str} on success, None on failure/expiry.
    """
    if not cookie_value or "." not in cookie_value:
        return None
    token, sig = cookie_value.rsplit(".", 1)
    expected_sig = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    # Accept both full (64-char) and legacy (32-char) signatures
    valid = hmac.compare_digest(sig, expected_sig) or (
        len(sig) == 32 and hmac.compare_digest(sig, expected_sig[:32])
    )
    if not valid:
        return None
    entry = _sessions.get(token)
    if not entry or time.time() > entry["expiry"]:
        _sessions.pop(token, None)
        return None
    return {"username": entry["username"]}


def invalidate_session(cookie_value: str) -> None:
    """Remove a session token."""
    if cookie_value and "." in cookie_value:
        token = cookie_value.rsplit(".", 1)[0]
        _sessions.pop(token, None)


def parse_cookie(handler) -> str | None:
    """Extract the auth cookie value from request headers."""
    cookie_header = handler.headers.get("Cookie", "")
    if not cookie_header:
        return None
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
    except http.cookies.CookieError:
        return None
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


# ── CSRF support ──────────────────────────────────────────────────────────────

def csrf_token_for_session(cookie_value: str) -> str | None:
    """Return the CSRF token bound to an authenticated session."""
    if not cookie_value or "." not in cookie_value:
        return None
    token = cookie_value.rsplit(".", 1)[0]
    if not token:
        return None
    return hmac.new(_signing_key(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()


def verify_csrf_token(cookie_value: str, csrf_token: str) -> bool:
    """Verify a submitted CSRF token against the authenticated session."""
    if not cookie_value or not csrf_token:
        return False
    session = verify_session(cookie_value)
    if not session:
        return False
    expected = csrf_token_for_session(cookie_value)
    return bool(expected and hmac.compare_digest(str(csrf_token), expected))


# ── Cookie helpers ────────────────────────────────────────────────────────────

def set_auth_cookie(handler, cookie_value: str) -> None:
    """Set the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = cookie_value
    cookie[COOKIE_NAME]["httponly"] = True
    cookie[COOKIE_NAME]["samesite"] = "Lax"
    cookie[COOKIE_NAME]["path"] = "/"
    cookie[COOKIE_NAME]["max-age"] = str(SESSION_TTL)
    # Set Secure flag when behind HTTPS
    if getattr(handler.request, "getpeercert", None) is not None or \
            handler.headers.get("X-Forwarded-Proto", "") == "https":
        cookie[COOKIE_NAME]["secure"] = True
    handler.send_header("Set-Cookie", cookie[COOKIE_NAME].OutputString())


def clear_auth_cookie(handler) -> None:
    """Clear the auth cookie on the response."""
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = ""
    cookie[COOKIE_NAME]["httponly"] = True
    cookie[COOKIE_NAME]["path"] = "/"
    cookie[COOKIE_NAME]["max-age"] = "0"
    handler.send_header("Set-Cookie", cookie[COOKIE_NAME].OutputString())


# ── Public paths (no auth required) ──────────────────────────────────────────

PUBLIC_PATHS = frozenset({
    "/login", "/register", "/health", "/favicon.ico",
    "/api/auth/login", "/api/auth/register", "/api/auth/status", "/api/auth/user_count",
    "/sw.js", "/manifest.json", "/manifest.webmanifest",
    "/session/manifest.json", "/session/manifest.webmanifest",
})


# ── Auth check ────────────────────────────────────────────────────────────────

def check_auth(handler, parsed) -> bool:
    """
    Check if request is authorized. Returns True if OK.
    If not authorized sends 401 (API) or 302 redirect (/login).
    Auth is always enabled for user-based authentication.
    """
    if parsed.path in PUBLIC_PATHS or \
            parsed.path.startswith("/static/") or \
            parsed.path.startswith("/session/static/"):
        return True
    cookie_val = parse_cookie(handler)
    if cookie_val and verify_session(cookie_val):
        return True
    # Not authorized
    if parsed.path.startswith("/api/"):
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"error":"Authentication required"}')
    else:
        import urllib.parse as _urlparse
        _path_with_query = parsed.path or "/"
        if parsed.query:
            _path_with_query += "?" + parsed.query
        _next = _urlparse.quote(_path_with_query, safe="/")
        handler.send_response(302)
        handler.send_header("Location", "login?next=" + _next)
        handler.end_headers()
    return False
