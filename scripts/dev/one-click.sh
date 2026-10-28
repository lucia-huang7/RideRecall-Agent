#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.pyenv/shims:$PATH"
PYTHON=""
for candidate in python3.11 python3.12 python python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info[:2] in ((3,11),(3,12))' >/dev/null 2>&1; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo 'Python 3.11 or 3.12 is required. Install it, then run Start RideRecall-Agent.command again.' >&2
  exit 1
fi
exec "$PYTHON" "$ROOT/scripts/ops/rides-control.py" "$@"
