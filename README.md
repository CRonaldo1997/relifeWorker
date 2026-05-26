# relifeWorker

Lightweight web UI for orchestrating digital workers / AI agents.

## Quick Start

```bash
# 1. (Optional) Copy and edit environment variables
cp .env.example .env

# 2. One-shot bootstrap launcher (creates venv, installs deps, starts server)
python3 bootstrap.py
```

The bootstrap script will:
- Load `.env` into the process environment
- Create / reuse a local virtualenv
- Install dependencies from `requirements.txt`
- Launch `server.py`

Then open the printed URL in your browser.

## Project Layout

```
.
├── bootstrap.py        # One-shot launcher (entry point)
├── server.py           # HTTP server
├── mcp_server.py       # Optional MCP (Model Context Protocol) entry
├── requirements.txt    # Python dependencies
├── start.sh / ctl.sh   # Convenience shell wrappers
├── .env.example        # Sample environment config
├── api/                # Backend API modules
└── static/             # Frontend assets (HTML/JS/CSS/images)
```

## Requirements

- Python 3.10+
- Linux / macOS / WSL recommended

## License

See [LICENSE](LICENSE).
