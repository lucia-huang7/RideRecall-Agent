#!/bin/bash
# Use run-synthetic-demo.sh's environment selection and existing mobile app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROFILE="${1:-$ROOT/.runtime/auth-preview}"
PROFILE="$(cd "$PROFILE" && pwd)"
export LLM_AGENT_SYSTEM_ENV=production
export LLM_AGENT_SYSTEM_IDENTITY_FILE="$PROFILE/identities.json"
export PORT="${PORT:-8012}"
exec bash "$ROOT/scripts/dev/run-synthetic-demo.sh" "$PROFILE/catalog.json"
