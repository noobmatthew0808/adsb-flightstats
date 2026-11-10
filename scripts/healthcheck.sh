#!/usr/bin/env bash
set -euo pipefail
URL=${1:-http://127.0.0.1:8088/api/health}
python3 - "$URL" <<'PY'
import json,sys,time,urllib.request
url=sys.argv[1]
for attempt in range(30):
 try:
  with urllib.request.urlopen(url,timeout=2) as response:
   body=json.load(response)
   if response.status==200 and body.get('ok') is True:
    print('Airspace Analytics health check passed.')
    break
 except Exception:
  pass
 time.sleep(1)
else:
 raise SystemExit('Airspace Analytics did not become healthy within 30 seconds. Inspect journalctl --user -u airspace-analytics.service. Existing receiver services were not touched.')
PY
