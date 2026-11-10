#!/usr/bin/env bash
set -euo pipefail
ROOT=${AIRSPACE_INSTALL_ROOT:-$HOME/airspace-analytics}
if (($#)); then
  if [[ "$1" != --root || $# != 2 ]]; then
    printf '%s\n' 'Usage: rollback.sh [--root INSTALLATION]' >&2; exit 2
  fi
  ROOT=$2
fi
cd "$ROOT/current"
exec "$ROOT/current/.venv/bin/python" -m backend.updater_service --root "$ROOT" client rollback
