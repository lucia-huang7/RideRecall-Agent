#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${LLM_AGENT_SYSTEM_PYTHON:-$ROOT/.venv-rides/bin/python}"
if [ ! -x "$PYTHON" ]; then
  if [ -n "${LLM_AGENT_SYSTEM_PYTHON:-}" ]; then
    echo 'LLM_AGENT_SYSTEM_PYTHON must name an existing Python 3.11/3.12 executable.' >&2
    exit 1
  fi
  for candidate in python3.11 python3.12; do
    if command -v "$candidate" >/dev/null 2>&1; then
      "$candidate" -m venv "$ROOT/.venv-rides"
      break
    fi
  done
fi
if [ ! -x "$PYTHON" ] || ! "$PYTHON" -c 'import sys; assert sys.version_info[:2] in ((3,11),(3,12))'; then
  echo 'Python 3.11 or 3.12 is required.' >&2
  exit 1
fi
"$PYTHON" -m pip install --disable-pip-version-check --require-hashes -r "$ROOT/backend/requirements.txt"
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  (cd "$ROOT/frontend" && npm ci)
fi
mkdir -p "$ROOT/.runtime"
export LLM_AGENT_SYSTEM_RIDE_DB="${LLM_AGENT_SYSTEM_RIDE_DB:-$ROOT/.runtime/rides.sqlite3}"
cd "$ROOT/backend"
"$PYTHON" -m uvicorn ride_agent.api.app:app --host 127.0.0.1 --port 8010 --limit-concurrency 64 --no-proxy-headers --no-access-log --reload &
API_PID=$!
trap 'kill "$API_PID" "${UI_PID:-}" 2>/dev/null || true' EXIT INT TERM
cd "$ROOT/frontend"
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort &
UI_PID=$!
echo "Ride demo: http://127.0.0.1:5175"
echo "API docs: http://127.0.0.1:8010/docs"
echo "Both services are local-only. Press Ctrl+C to stop."
wait "$API_PID" "$UI_PID"
