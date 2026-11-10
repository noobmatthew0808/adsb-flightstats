# Airspace Analytics

A private, responsive control centre for your ADS-B receiver: your services in one place, with flexible historical analysis of the Aerodrome data you already collect.

**Port 8088 · Europe/London · Aerodrome read-only · No duplicate collector**

## What you can do

- Launch Aerodrome, SkyStats, your aircraft map, receiver graphs, ADSB.im and your own links from customisable tiles.
- Switch between **estimated observed sessions**, **unique aircraft**, and **recorded observations** with clearly different definitions.
- Explore arbitrary rolling durations (including 46 hours), custom dates, calendar periods and all available history. Group by hour/day/week/month/year or seasonal patterns; filter by type, category, operator, ICAO, callsign and calendar month.
- Use interactive line, area and bar charts, category donuts, tables and weekday/hour heatmaps. Drill into a period or aircraft; save a view and share its URL; export data and charts.
- Compare equal-elapsed current periods and custom A/B windows with collection coverage warnings.
- Search source tables and inspect their schemas in a bounded, read-only data browser. Export up to 10,000 rows as CSV.
- Change settings and check/install/roll back GitHub releases through administrator-protected **Settings → Updates**.

Incomplete history stays visible as incomplete history. Nothing silently substitutes demo data when the real database is unavailable. Operator metadata is not presented as a complete verified airline registry.

## Install on the receiver laptop

Use the complete release ZIP, which contains the compiled website. Extract it, open a terminal in the extracted project directory and run:

```bash
bash scripts/install.sh
```

The installer creates `~/airspace-analytics`, installs its Python dependencies, and registers only this application's two user services. Save the administrator password it prints. It may request your Ubuntu password once to enable automatic startup before login. It never installs or reconfigures your SDR, Docker containers, Aerodrome or feeders. Your existing 365-day retention is untouched.

Open `http://192.168.1.187:8088` from your LAN (use your laptop's current address if different). The UI works without Node running. Dozzle's URL is intentionally left unset until you enter its actual address. See [installation details](docs/INSTALL.md).

Keep the app on a trusted LAN or separately configured VPN; do not expose it to the public Internet. Read-only analytics is accessible to devices that can reach the website. Administrator changes require sign-in. Plain LAN HTTP is unencrypted; see [security](docs/SECURITY.md).

## Develop and test

Python 3.12+ and Node 22+ are the tested development targets. Work in the existing task checkout; cloud tasks are already isolated and do not require additional Git worktrees.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
(cd frontend && npm ci && npm run build)
.venv/bin/python -m pytest -q
bash scripts/dev.sh
```

Development explicitly enables a labelled synthetic demo in `state/demo.db`. Set `AIRSPACE_DEMO=0 AIRSPACE_DB=/path/to/aircraft_history.db` to use a real source read-only. No credentials are needed for public analytics. Administrator changes remain disabled unless `AIRSPACE_ADMIN_PASSWORD_HASH` is set. Create a hash without putting the password in shell history:

```bash
.venv/bin/python -c 'from getpass import getpass; from backend.security import hash_password; print(hash_password(getpass("Admin password: ")))'
```

Set the resulting hash as an environment variable, preserving literal dollar signs using single quotes. Do not commit it. The production installer handles this automatically.

For frontend hot reload run `npm run dev` in `frontend/` while the backend runs on port 8088; the development proxy forwards `/api`. The production service serves `frontend/dist` directly.

## Architecture and operating limits

React + TypeScript + ECharts is compiled into static assets. Python FastAPI serves the UI and parameterised SQLite analytics. SQLite is opened `mode=ro` with `query_only=ON`; short-lived connections, query deadlines and result limits keep queries bounded. A small separate JSON file holds UI settings. No aircraft-history copy is created.

The updater runs as a separate unprivileged user service on a private Unix socket. It installs only releases from `noobmatthew0808/adsb-flightstats`, verifies GitHub's SHA-256 release-asset digest, stages a new release, checks it, atomically switches `current`, restarts only the analytics web service, and rolls back if health fails. It does not execute commands supplied by the browser. See [updates and publishing](docs/UPDATES.md).

Exact distinct and session queries require raw history; this release does not pretend hourly summary tables can recover exact sessions or global distinct counts after raw retention expires. Years of high-frequency data can reach the query budget; shorten the interval or narrow the filters. The source database is never indexed or migrated by this app. Optional live radio diagnostics, map replay, alerts and external aircraft enrichment are not included.

- [Metric definitions and coverage](docs/DATA-DEFINITIONS.md)
- [Installation and recovery](docs/INSTALL.md)
- [Security boundaries](docs/SECURITY.md)
- [Test evidence and remaining host checks](docs/VALIDATION.md)

The project is supplied with a synthetic fixture, not your private database. Real-host installation, reboot behaviour and a future published GitHub release must be verified on the actual laptop; cloud tests cannot establish those outcomes.
