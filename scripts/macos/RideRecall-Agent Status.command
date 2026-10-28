#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if ! bash "$ROOT/scripts/dev/one-click.sh" status; then
  read -r -p "Press Return to close."
  exit 1
fi
read -r -p "Press Return to close."
