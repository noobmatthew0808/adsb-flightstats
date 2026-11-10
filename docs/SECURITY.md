# Security and operating boundaries

This is a private-LAN application, not a public hosted service. Do not port-forward 8088 to the Internet. For off-LAN use, configure a trusted VPN such as Tailscale separately. HTTP on a private LAN is not encrypted; administrator passwords and history can be observed by someone able to intercept that network. Use a trusted network, or configure a local TLS reverse proxy and `AIRSPACE_SECURE_COOKIE=1`. The installer does not change networking or firewalls.

Analytics is read-only at both SQLite URI and connection levels. Production systemd additionally mounts `/opt/aerodrome` read-only in the application unit's filesystem view. Neither service restarts receiver services, touches Docker, changes database ownership, or uses a general sudo rule.

Read-only analytics is available to devices that can reach the site. Settings changes, update checks, update installation and rollback require the installer-generated administrator password. Password hashes use salted scrypt; passwords are not saved in browser storage. Expiring HttpOnly SameSite=Strict session cookies, CSRF headers, Origin validation and Host validation protect mutation routes. Sessions expire after eight hours and are intentionally invalidated on application restart. Sign in again after an update. Do not share the administrator password with untrusted LAN users.

The updater is a separate unprivileged process on a private Unix socket. It accepts only fixed actions, uses the configured GitHub repository and release assets, checks SHA-256 before extraction, rejects unsafe archives, prepares a separate release, and controls only the analytics user service. A release digest protects integrity in transit and accidental corruption, but is not a publisher signature: a compromised GitHub publisher can release malicious application code. Protect the repository and release-publishing account. See [UPDATES.md](UPDATES.md).

Service shortcut URLs are validated HTTP(S) links and are opened by your browser, never probed by the server; configuring an arbitrary shortcut cannot make the server issue internal-network requests. Database paths and updater origins cannot be changed by browser request parameters. CSV exports neutralise spreadsheet formulas. CSP blocks external script loading and framing. No third-party analytics or telemetry is included.

Keep `environment` and the `state` directory private. Do not commit passwords, `.env`, SQLite data, or personal receiver files. Report issues with version and redacted logs; never upload Aerodrome's config.yaml.
