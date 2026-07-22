#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${KINDER_UBER_PYTHON:-$ROOT/.venv-rides/bin/python}"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$ROOT/.venv-rides"
fi
if ! "$PYTHON" -c "import fastapi, uvicorn, anthropic, httpx, jsonschema, multipart, chromadb, fakeredis, redis, lupa, mcp, prometheus_client" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r "$ROOT/backend/requirements-demo.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  (cd "$ROOT/frontend" && npm ci)
fi
mkdir -p "$ROOT/.runtime"
export KINDER_UBER_RIDE_DB="${KINDER_UBER_RIDE_DB:-$ROOT/.runtime/rides.sqlite3}"
cd "$ROOT/backend"
"$PYTHON" -m uvicorn mobility.app:app --host 127.0.0.1 --port 8010 --reload &
API_PID=$!
trap 'kill "$API_PID" "${UI_PID:-}" 2>/dev/null || true' EXIT INT TERM
cd "$ROOT/frontend"
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort &
UI_PID=$!
echo "Ride demo: http://127.0.0.1:5175"
echo "API docs: http://127.0.0.1:8010/docs"
echo "Both services are local-only. Press Ctrl+C to stop."
wait "$API_PID" "$UI_PID"
