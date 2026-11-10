# Release validation — 10 November 2026

## Completed in the cloud

- Hash-pinned production and development dependencies installed successfully. Backend suites pass on Python **3.12.14** and **3.14.9**. The laptop's exact 3.14.4 patch release has not been executed here.
- **69 backend, analytics, API, security, installer and updater tests passed** on each Python version. Tests cover distinct metrics, session carry-in/callsign changes, source immutability, missing sources, London DST, coverage/averages/month filters, reader concurrency, SQL/CSV injection, authentication, CSRF, host/origin restrictions, settings persistence, installer paths and update recovery.
- **7 frontend time tests passed**, including 23/25-hour days, both autumn repeated-hour occurrences, rejected spring-gap times, and exact 46-hour rolling durations.
- TypeScript checks and the Vite production build passed. Dependencies were installed using `npm ci` with the lockfile. The cloud has Node **24.22.1**; CI is configured to build/test with Node 22, matching the laptop's major version. That CI run is not claimed as completed here.
- A Playwright/Chromium acceptance suite passed with **1440px desktop** and **390px touch/mobile** viewports: all six sections, actual analytics responses, 46-hour hourly view, metric changes, chart/table/export, matched-elapsed comparison, heatmap, raw search/schema/aircraft details, administrator login, CSRF-protected settings save/reload, and custom shortcut add→save→home→remove. The final isolated run recorded **zero JavaScript errors and zero failed API requests**. This is mobile Chromium emulation, not a physical iPhone Safari or Windows browser test.
- Both real systemd unit templates pass `systemd-analyze --user verify`, including paths with spaces and percent signs. Shell scripts pass Bash syntax checks.
- The real installer ran into a separate temporary install root with the synthetic source and **without systemd registration**. Its installed Python web service and separate Unix-socket updater started directly. Health, source, analytics, database schema, updater status, administrator login and settings mutation succeeded. The source database SHA-256 remained unchanged.
- Update tests use real ZIP validation with simulated release metadata/service control. They cover checksums, unsafe archives, lock contention, staged preparation, health failures, automatic/manual rollback, interrupted transactions, retained-release retry, downgrade refusal and periodic release checks. They do not publish a GitHub release or restart the user's services.

The synthetic dataset contains **164,876 observations and 180 aircraft** across approximately 14 days. A representative 46-hour query took about **0.12 seconds**; representative 14-day cold queries took about **0.8 seconds** in this cloud environment. These are smoke-scale measurements, not a benchmark of the user's laptop.

## Important limits and checks still required

1. The user's actual Aerodrome database was not uploaded or accessed. Schema compatibility is based on the supplied inspection. Installation validates source access without changing it; initial real-data results must then be checked against Aerodrome.
2. systemd process startup, namespace availability, boot persistence, LAN connectivity, iPhone Safari and Windows browsers require verification on the real Ubuntu laptop. No receiver services were changed or restarted during this work.
3. Exact long-range queries currently use indexed raw observations, including session calculation for summary cards. **Coarser grouping does not eliminate the raw-history scan.** A future 60-million-row/year dataset has not been benchmarked and can exceed the eight-second query budget. The app reports the limit instead of inventing results. No multi-year pre-aggregation or second observation store is shipped.
4. Coverage is inferred from recorded concurrent minutes, not a verified per-poll uptime ledger. Missing time cannot safely be treated as zero traffic. Operator/category metadata is current, and historical attribution may be incomplete.
5. Cloud access to `api.github.com` was blocked by the current outbound policy. Required GitHub release API/asset domain additions were saved in the environment draft; they are not claimed as applied. Published-release discovery and real release activation still need an allowed HTTPS route and a real published release.
6. During development, parallel frontend rebuilds temporarily removed `dist/index.html`; final browser checks were rerun against the frozen compiled build. Production updates prepare an inactive release and avoid rebuilding active frontend files.

Run the optional browser suite against an explicit demo instance using a separate Python environment with Playwright and Chromium installed:

```bash
AIRSPACE_TEST_PASSWORD='<demo-admin-password>' python scripts/browser-smoke.py
```

It intentionally changes and restores demo settings; never run this mutation test against a live receiver workspace. Screenshots and browser output go under ignored `artifacts/`.
