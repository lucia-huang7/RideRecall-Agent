#!/bin/bash
# Persistent authenticated fictional preview; credentials expire after 24 hours.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
docker compose build app
if ! docker compose --profile tools run --rm --no-deps init python -c 'from pathlib import Path; raise SystemExit(0 if Path("/config/profile/identities.json").is_file() else 1)'; then
  docker compose --profile tools run --rm --no-deps init
fi
docker compose up -d --wait app
echo 'Authenticated fictional preview: http://127.0.0.1:8013'
echo 'Credentials stay in the private configuration volume. See README.md for access and rotation.'
