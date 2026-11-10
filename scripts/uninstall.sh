#!/usr/bin/env bash
# Removes only our two user units. Data/releases are retained unless --purge.
set -euo pipefail
ROOT=${AIRSPACE_INSTALL_ROOT:-$HOME/airspace-analytics}
PURGE=0
while (($#)); do
  case "$1" in
    --root) ROOT=${2:?Missing root}; shift 2 ;;
    --purge) PURGE=1; shift ;;
    *) printf '%s\n' 'Usage: uninstall.sh [--root INSTALLATION] [--purge]' >&2; exit 2 ;;
  esac
done
if ((EUID==0)); then printf '%s\n' 'Run as the same normal user who installed the dashboard.' >&2; exit 1; fi
ROOT=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$ROOT")
if [[ ! -f "$ROOT/environment" || ! -L "$ROOT/current" ]]; then
  printf '%s\n' 'This does not look like an Airspace Analytics installation. Nothing removed.' >&2; exit 1
fi
systemctl --user disable --now airspace-analytics.service airspace-analytics-updater.service
rm -f -- "$HOME/.config/systemd/user/airspace-analytics.service" "$HOME/.config/systemd/user/airspace-analytics-updater.service"
systemctl --user daemon-reload
if ((PURGE)); then
  python3 - "$ROOT" <<'PY'
import json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1]).resolve()
home=pathlib.Path.home().resolve()
if root in (home,pathlib.Path('/')) or root==pathlib.Path('/opt/aerodrome') or str(root).startswith('/opt/aerodrome/'):
 raise SystemExit('Refusing unsafe removal path')
# Only known app files; preserve unrelated user content in the chosen prefix.
for name in ('releases','state'):
 p=root/name
 if p.is_symlink(): p.unlink()
 elif p.is_dir(): shutil.rmtree(p)
for name in ('current','previous','environment','updater.json'):
 p=root/name
 if p.is_file() or p.is_symlink(): p.unlink()
try: root.rmdir()
except OSError: print('Unrelated files in install directory were preserved.')
PY
fi
printf '%s\n' 'Only Airspace Analytics was stopped and disabled. Aerodrome, receiver and feeder services were not changed.'
if ((!PURGE)); then printf 'Settings and releases remain in %s.\n' "$ROOT"; fi
