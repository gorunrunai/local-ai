#!/bin/bash
# Start the backend (API + web app + models) on http://127.0.0.1:8000.
# Used by the LaunchAgent that the Mac app starts; `make backend` does the same in a terminal.
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p data/logs
# --no-proxy-headers: the access check must see the real peer (tailscale serve connects from
# 127.0.0.1), not the X-Forwarded-For address uvicorn would otherwise substitute.
exec .venv/bin/python -m uvicorn orchestrator.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers
