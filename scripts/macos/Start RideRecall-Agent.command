#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if ! bash "$ROOT/scripts/dev/one-click.sh" start; then
  read -r -p "Press Return to close."
  exit 1
fi
