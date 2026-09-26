#!/usr/bin/env bash
# Backend (API + llama-swap + models) on :8000 and the Vite dev server on :5173 (proxies /api).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run uvicorn orchestrator.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers &
BACKEND=$!
(cd frontend && npm run dev) &
FRONTEND=$!
trap 'kill $FRONTEND $BACKEND 2>/dev/null; wait' INT TERM EXIT
echo "Open http://127.0.0.1:5173 (dev) — or run 'make build && make backend' and use http://127.0.0.1:8000"
wait
