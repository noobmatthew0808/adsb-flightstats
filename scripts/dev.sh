#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export AIRSPACE_STATE_DIR="${AIRSPACE_STATE_DIR:-$PWD/state}"
export AIRSPACE_DEMO="${AIRSPACE_DEMO:-1}"
if [[ -z "${AIRSPACE_ADMIN_PASSWORD_HASH:-}" ]]; then
  printf 'Administrator changes are disabled. Set AIRSPACE_ADMIN_PASSWORD_HASH to enable them.\n'
fi
exec .venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port "${AIRSPACE_PORT:-8088}"
