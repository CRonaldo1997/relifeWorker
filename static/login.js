/**
 * login.js — handles the username + password login form.
 *
 * Calls /api/auth/login with {username, password}. On success, redirects to
 * the path supplied via ?next=… (when safe) or `/` otherwise. If no users
 * exist yet (fresh install), automatically redirects to /register so the
 * first user can create an account.
 */
(function () {
  'use strict';

  var form = document.getElementById('login-form');
  if (!form) return;

  var errEl = document.getElementById('err');
  var connFailed = (form.dataset && form.dataset.connFailed) || '连接失败，请重试';
  var btn = form.querySelector('button[type=submit]');
  var btnDefaultText = btn ? btn.textContent : '由此开启';

  // ── Safe redirect helper ────────────────────────────────────────────────
  function safeNextPath() {
    try {
      var raw = new URL(window.location.href).searchParams.get('next');
      if (!raw) return '/';
      if (raw.charAt(0) !== '/') return '/';
      // Block protocol-relative URLs like //evil.com or /\evil.com
      if (raw.charAt(1) === '/' || raw.charAt(1) === '\\') return '/';
      if (/[\x00-\x1f\x7f\s]/.test(raw)) return '/';
      return raw;
    } catch (_) { return '/'; }
  }

  function showErr(msg) {
    errEl.textContent = msg;
    errEl.style.display = 'block';
  }

  function clearErr() {
    errEl.style.display = 'none';
    errEl.textContent = '';
  }

  function resetButton() {
    if (!btn) return;
    btn.disabled = false;
    btn.textContent = btnDefaultText;
  }

  // ── If no users exist, jump straight to /register ───────────────────────
  fetch('/api/auth/user_count', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data && data.count === 0) {
        var next = new URL(window.location.href).searchParams.get('next');
        var dest = '/register';
        if (next) dest += '?next=' + encodeURIComponent(next);
        window.location.replace(dest);
      }
    })
    .catch(function () { /* ignore — fall back to login form */ });

  // ── Login submit ────────────────────────────────────────────────────────
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErr();

    var usernameEl = document.getElementById('username');
    var pwEl = document.getElementById('pw');
    var username = usernameEl ? usernameEl.value.trim() : '';
    var password = pwEl ? pwEl.value : '';

    if (!username) { showErr('请输入用户名'); return; }
    if (!password) { showErr('请输入密码'); return; }

    if (btn) {
      btn.disabled = true;
      btn.textContent = '登录中…';
    }

    fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, password: password }),
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (d) { return { status: r.status, data: d }; });
      })
      .then(function (res) {
        if (res.data && res.data.ok) {
          if (res.data.username) {
            try { localStorage.setItem('hermes-username', res.data.username); } catch (_) { /* ignore */ }
          }
          window.location.href = safeNextPath();
        } else {
          showErr((res.data && (res.data.error || res.data.detail)) || '登录失败');
          resetButton();
        }
      })
      .catch(function () {
        showErr(connFailed);
        resetButton();
      });
  });
})();
