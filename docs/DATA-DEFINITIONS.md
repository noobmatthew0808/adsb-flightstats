# What the numbers mean

Airspace Analytics reads the existing Aerodrome SQLite database using `mode=ro` and `PRAGMA query_only=ON`. It never migrates, vacuums, checkpoints or changes that database, its configuration, its retention, or the receiver. Settings live in a separate small JSON file. There is no second historical observation collector.

## Three separate measurements

- **Unique aircraft** counts distinct ICAO addresses in the selected half-open interval `[start, end)`. A daily total is recalculated for the day; it is never the sum of hourly distinct counts. One aircraft can appear in several breakdowns if its recorded type or callsign changes; breakdown totals need not sum to the overall distinct total.
- **Estimated observed sessions** counts inferred session starts. A new session starts after a gap longer than the selected idle threshold (default 30 minutes), or a change between two non-empty callsigns. Carry-in observations before the requested start are considered, so an already-active aircraft does not become a new session just because the chart window starts. These are reception sessions, not proven flight legs, takeoffs, or landings. Missing history before retention begins limits the first session estimate.
- **Recorded observations** counts rows in `all_sightings`. These are snapshots collected at Aerodrome's configured 10-second cadence, not individual radio messages. A collector poll may miss a target or fail entirely.

All query instants use UTC Unix seconds. Calendar grouping, weekday patterns and boundaries use `Europe/London`. Spring and autumn transitions produce 23- and 25-hour days. Repeated autumn hours retain their UTC offsets in timeline labels.

## Coverage, denominators and comparisons

Recorded `concurrent_minute` rows are evidence that a minute was recorded, including an explicit zero count. Missing rows are not assumed to be zero traffic. Minute-level coverage is an estimate, not a guarantee that every 10-second poll was successful. Without that table, sighting-bearing minutes provide a conservative lower bound; zero-traffic time cannot then be distinguished from offline time.

Coverage is measured against the requested interval, including time before collection began. Buckets with no coverage are null gaps. Partial buckets and missing history are flagged. Pattern averages use constituent calendar periods with observed coverage, and expose their sample count; they do not extrapolate unobserved hours or fill historical gaps with zeros. The interface offers exclusion of partial periods. Read the coverage warnings alongside every comparison. Percent change is undefined when the baseline is zero or absent.

The same elapsed-time comparison uses elapsed UTC seconds from the London calendar boundary, avoiding a partial current day against an entire previous day. Custom A/B dates are explicit; equal length alone does not establish comparable collection coverage.

## Categories and attribution

Aircraft category and operator come from Aerodrome's `seen_aircraft` metadata, which describes the latest known aircraft, not necessarily its historical operator at the observation instant. Unknown values remain **Unknown**. Operator is not automatically relabelled as a verified airline. Sparse cached route information is not a complete airline registry. There are no invented registrations, routes or live receiver counts.

## Query limits

Connections and queries are bounded; narrow the interval when a query reaches its time or aggregation budget. Exact unique and session calculations can require scanning indexed observations. No source indexes are added. The application keeps short-lived derived result caches, never duplicate raw history. For multi-year data, use coarser groups and filters; source growth and hardware determine query cost. CSV raw exports are capped at 10,000 rows and escape spreadsheet formula cells.

The Data screen exposes allowlisted source tables, schema information, parameterised search and pagination. It offers no freeform SQL execution and no write endpoint.
