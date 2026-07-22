#!/bin/bash
# Run the existing mobile app against an opt-in synthetic catalog and isolated DB.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CATALOG="${1:-$ROOT/datasets/ride_support_v1/catalog-preview.json}"
PORT="${PORT:-8011}"
RIDE_PYTHON="${RIDE_PYTHON:-}"
if [ -z "$RIDE_PYTHON" ]; then
  for candidate in "$ROOT/.venv-rides/bin/python" "$HOME/Library/Application Support/kinder-uber/"*/venv/bin/python python3.11 python3; do
    if "$candidate" -c 'import fastapi, chromadb, mcp' >/dev/null 2>&1; then
      RIDE_PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$RIDE_PYTHON" ]; then
  echo 'Set RIDE_PYTHON to a Python environment with requirements.txt installed.' >&2
  exit 1
fi
CATALOG="$("$RIDE_PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$CATALOG")"
if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  (cd "$ROOT/frontend" && npm ci && npm run build)
fi
export KINDER_UBER_RIDE_MODEL_MODE=demo
export KINDER_UBER_RIDE_CATALOG_PATH="$CATALOG"
CATALOG_KEY="$("$RIDE_PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:12])' "$CATALOG")"
export KINDER_UBER_RIDE_DB="${KINDER_UBER_RIDE_DB:-$ROOT/.runtime/synthetic-preview/$CATALOG_KEY/rides.sqlite3}"
mkdir -p "$(dirname "$KINDER_UBER_RIDE_DB")"
echo "Mobile preview: http://127.0.0.1:$PORT/#/trips"
echo "Catalog: $CATALOG"
echo 'Keep this terminal open; press Ctrl+C to stop. Default demo data is separate.'
cd "$ROOT/backend"
exec "$RIDE_PYTHON" -m uvicorn mobility.app:app --host 127.0.0.1 --port "$PORT"
