#!/usr/bin/env bash
# Install only Airspace Analytics. Does not alter Aerodrome or any receiver service.
set -euo pipefail
umask 077
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PREFIX="$HOME/airspace-analytics"
DB=/opt/aerodrome/aircraft_history.db
PORT=8088
SYSTEMD=1
CHECK_DB=1
LINGER=1
while (($#)); do
  case "$1" in
    --prefix) PREFIX=${2:?Missing --prefix value}; shift 2 ;;
    --db) DB=${2:?Missing --db value}; shift 2 ;;
    --port) PORT=${2:?Missing --port value}; shift 2 ;;
    --no-systemd) SYSTEMD=0; LINGER=0; shift ;;
    --skip-db-check) CHECK_DB=0; shift ;;
    --no-linger) LINGER=0; shift ;;
    --help) printf '%s\n' 'Usage: bash scripts/install.sh [--prefix PATH] [--db PATH] [--port 8088] [--no-systemd] [--skip-db-check] [--no-linger]'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if (( EUID == 0 )); then
  printf '%s\n' 'Run this installer as your normal Ubuntu user, not with sudo.' >&2; exit 1
fi
command -v python3 >/dev/null
python3 - <<'PY'
import sys
if sys.version_info < (3,11):
 raise SystemExit('Python 3.11 or newer is required.')
PY
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1024 || PORT > 65535)); then
  printf '%s\n' 'Port must be between 1024 and 65535.' >&2; exit 2
fi
PREFIX=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$PREFIX")
DB=$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$DB")
case "$PREFIX$DB" in *$'\n'*|*$'\r'*) printf '%s\n' 'Paths cannot contain newlines.' >&2; exit 2 ;; esac
if [[ "$PREFIX" == "$SOURCE" || "$PREFIX" == / || "$PREFIX" == "$HOME" ]]; then
  printf '%s\n' 'The installation directory must be separate from the extracted source and your home directory.' >&2; exit 2
fi
python3 - "$SOURCE" "$PREFIX" "$DB" <<'PATH_CHECK'
from pathlib import Path
import sys
source,prefix,db=map(Path,sys.argv[1:])
if source in prefix.parents or prefix in source.parents:
 raise SystemExit('Source and installation directories must not contain one another.')
if prefix == db or prefix in db.parents:
 raise SystemExit('Installation must not contain the Aerodrome database.')
PATH_CHECK
if [[ -e "$PREFIX/current" || -L "$PREFIX/current" ]]; then
  printf '%s\n' 'An installation already exists. Use Settings → Updates rather than reinstalling over it.' >&2; exit 1
fi
if ((SYSTEMD)); then
  command -v systemctl >/dev/null
  systemctl --user show-environment >/dev/null
  if systemctl --user is-active --quiet airspace-analytics.service; then
    printf '%s\n' 'Airspace Analytics is already running. Use its Updates page.' >&2; exit 1
  fi
fi
if command -v ss >/dev/null && ss -H -ltn "sport = :$PORT" | python3 -c 'import sys; sys.exit(not bool(sys.stdin.read().strip()))'; then
  printf 'Port %s is already in use. No services were stopped.\n' "$PORT" >&2; exit 1
fi
if ((CHECK_DB)); then
  python3 - "$DB" <<'PY'
import pathlib,sqlite3,sys
path=pathlib.Path(sys.argv[1])
try:
 with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2) as db:
  db.execute('PRAGMA query_only=ON')
  columns={row[1] for row in db.execute('PRAGMA table_info(all_sightings)')}
  if not {'icao','seen_at'}.issubset(columns):
   raise RuntimeError('all_sightings does not have the expected schema')
  db.execute('SELECT seen_at FROM all_sightings LIMIT 1').fetchone()
except Exception as exc:
 raise SystemExit(f'Cannot read Aerodrome database: {exc}. No permissions or receiver settings were changed.')
print('Aerodrome database is readable in read-only mode.')
PY
fi
if [[ ! -f "$SOURCE/frontend/dist/index.html" ]]; then
  printf '%s\n' 'Building the frontend once. Node.js 22 or newer and npm are required for source-only installs.'
  command -v node >/dev/null
  command -v npm >/dev/null
  (cd "$SOURCE/frontend" && npm ci --no-audit --no-fund && npm run build)
fi
VERSION=$(python3 - "$SOURCE" <<'PY'
import pathlib,re,sys
p=pathlib.Path(sys.argv[1])/'VERSION'
v=p.read_text().strip() if p.exists() else '1.0.0'
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.-]+)?',v):
 raise SystemExit('Invalid VERSION')
print(v)
PY
)
RELEASE="$PREFIX/releases/$VERSION"
if [[ -e "$RELEASE" ]]; then
  printf 'Release directory already exists: %s. Inspect it before retrying.\n' "$RELEASE" >&2; exit 1
fi
mkdir -p "$PREFIX/releases" "$PREFIX/state" "$RELEASE"
# tar preserves all prebuilt assets, while excluding developer caches and secrets.
tar -C "$SOURCE" --exclude='./.git' --exclude='./.venv' --exclude='./node_modules' --exclude='./frontend/node_modules' --exclude='./.pytest_cache' --exclude='./__pycache__' --exclude='./state' --exclude='./dist' --exclude='./artifacts' --exclude='./*.zip' --exclude='./*.db' --exclude='./.env*' --exclude='./environment' -cf - . | tar -C "$RELEASE" -xf -
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install --disable-pip-version-check --require-hashes -r "$RELEASE/requirements.txt"
"$RELEASE/.venv/bin/python" - "$PREFIX" "$DB" "$PORT" <<'PY'
import hashlib,json,os,pathlib,secrets,sys
root=pathlib.Path(sys.argv[1])
env=root/'environment'
if env.exists():
 raise SystemExit('An existing environment file was preserved. Inspect the partial installation before retrying.')
password=secrets.token_urlsafe(18)
salt=secrets.token_hex(16)
# Hash format shared with backend.auth: scrypt$<salt-hex>$<digest-hex>.
digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()
values={
 'AIRSPACE_DB':sys.argv[2],
 'AIRSPACE_STATE_DIR':str(root/'state'),
 'AIRSPACE_INSTALL_ROOT':str(root),
 'AIRSPACE_ADMIN_PASSWORD_HASH':f'scrypt${salt}${digest}',
 'AIRSPACE_HOST':'0.0.0.0',
 'AIRSPACE_SESSION_GAP':'30',
 'AIRSPACE_PORT':sys.argv[3],
}
def quoted(value):
 return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'
env.write_text(''.join(k+'='+quoted(v)+'\n' for k,v in values.items()))
os.chmod(env,0o600)
print('\nYour administrator password (save it in your password manager):\n'+password+'\n')
PY
ln -s "$RELEASE" "$PREFIX/current"
if ((SYSTEMD)); then
  mkdir -p "$HOME/.config/systemd/user"
  python3 "$RELEASE/deploy/render_units.py" "$RELEASE/deploy" "$HOME/.config/systemd/user" "$PREFIX" "$PORT"
  systemctl --user daemon-reload
  systemctl --user enable --now airspace-analytics-updater.service airspace-analytics.service
  if ((LINGER)); then
    if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)" != yes ]]; then
      printf '%s\n' 'Enabling startup before login for this user. This may ask for your Ubuntu password.'
      sudo loginctl enable-linger "$USER"
    fi
  fi
  "$RELEASE/scripts/healthcheck.sh" "http://127.0.0.1:$PORT/api/health"
fi
printf '\nAirspace Analytics installed in %s\n' "$PREFIX"
if ((SYSTEMD)); then
  LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  printf 'Open http://%s:%s on your LAN. Routine updates are in Settings → Updates.\n' "${LAN_IP:-localhost}" "$PORT"
else
  printf '%s\n' 'Service startup was skipped. The release and environment file are prepared.'
fi
