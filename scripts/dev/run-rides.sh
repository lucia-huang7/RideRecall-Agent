#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ "${1:-}" = '--dev' ]; then
  shift
  exec bash "$ROOT/scripts/dev/run-rides-dev.sh" "$@"
fi
exec bash "$ROOT/scripts/dev/one-click.sh" start "$@"
