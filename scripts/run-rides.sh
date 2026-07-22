#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = '--dev' ]; then
  shift
  exec bash "$ROOT/scripts/run-rides-dev.sh" "$@"
fi
exec bash "$ROOT/scripts/one-click.sh" start "$@"
