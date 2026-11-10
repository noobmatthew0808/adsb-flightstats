#!/usr/bin/env bash
# Terminal fallback. The dashboard provides the same fixed update operations.
set -euo pipefail
ROOT=${AIRSPACE_INSTALL_ROOT:-$HOME/airspace-analytics}
ACTION=install
while (($#)); do
  case "$1" in
    --root) ROOT=${2:?Missing root}; shift 2 ;;
    check|install|status) ACTION=$1; shift ;;
    *) printf '%s\n' 'Usage: update.sh [--root INSTALLATION] [check|install|status]' >&2; exit 2 ;;
  esac
done
cd "$ROOT/current"
exec "$ROOT/current/.venv/bin/python" -m backend.updater_service --root "$ROOT" client "$ACTION"
