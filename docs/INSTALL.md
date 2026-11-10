# Install on your Ubuntu laptop

The six receiver checks in your handover are already complete. This installer does not repeat setup of Node, Python, SDR, Docker, Aerodrome or your feeders. It creates a separate application in `/home/ads-b/airspace-analytics`, opens Aerodrome's existing database read-only, and creates two **user** systemd services. Its default HTTP port is **8088**.

## First installation

Use the complete release ZIP (which includes the compiled frontend), extract it to a temporary folder such as Downloads, and open a Terminal in that extracted project folder. Run:

```bash
bash scripts/install.sh
```

The script validates access to `/opt/aerodrome/aircraft_history.db` without modifying it, creates release and settings directories, creates a Python virtual environment, installs checksum-pinned Python dependencies with TLS verification, and enables automatic startup. It prints a new administrator password once. Save that password in your password manager; the configuration stores a scrypt hash rather than the password.

One narrowly scoped `sudo loginctl enable-linger ads-b` operation may ask for your Ubuntu password. It enables the two new user services at boot before you sign in. It does not restart or reconfigure another service. To omit that operation, pass `--no-linger`; the services then depend on an active login unless linger was already enabled.

Open the LAN address printed by the installer, usually `http://192.168.1.187:8088`. The same address works in Safari on your iPhone and a browser on your Windows PC when they are on the same network. Sign into Settings with the generated administrator password to change links or install future updates. Keep this site on your trusted LAN or an independently configured VPN; do not forward it onto the public internet.

The compiled frontend runs as part of the Python service; Node does not run continuously. For a source-only checkout with no `frontend/dist`, the installer runs the repository's locked npm build first. Python 3.11+ is supported; your Python 3.14 satisfies that requirement.

## Options

`--prefix PATH`, `--db PATH` and `--port NUMBER` override the install directory, database location and port. `--no-systemd` prepares files without registering services and is intended for development/testing. `--skip-db-check` is only useful for a development machine with no Aerodrome database; production installation should keep the check enabled. No synthetic data is substituted for an unavailable real database.

An existing installation is preserved. Re-running installation over `current` is refused: use **Settings → Updates** instead. If an initial install failed before service setup, the partial release and environment are preserved for diagnosis; do not delete a directory containing useful settings blindly.

## What changes

- `~/airspace-analytics/releases/<version>/`: immutable release contents and a release-local `.venv`.
- `~/airspace-analytics/current`: atomic symlink to the active release.
- `~/airspace-analytics/state/`: settings, sessions, update history and updater Unix socket. No copied aircraft history.
- `~/airspace-analytics/environment`: private environment file containing the database path and administrator password hash.
- `~/.config/systemd/user/airspace-analytics.service` and `airspace-analytics-updater.service`: only these two units are created.

The application service can write only to its state directory under systemd's filesystem restrictions. Both units see `/opt/aerodrome` read-only. Database permissions and retention remain as they are. If access fails, the installer reports it rather than changing permissions.

## Troubleshooting

To inspect this website's logs only:

```bash
journalctl --user -u airspace-analytics.service -n 60 --no-pager
```

If your IP changes, use your new laptop LAN address with port 8088, then edit the service tiles in Settings. Receiver service links are user-editable. Dozzle starts unconfigured because its port was not supplied.

To stop and disable only this website and its updater, run `bash ~/airspace-analytics/current/scripts/uninstall.sh`. Settings and releases are retained. `--purge` additionally removes the installer's own files; it never removes Aerodrome's database or receiver files. Linger is intentionally left enabled because other user services may rely on it.

On the user's real laptop, installation, automatic startup after reboot, live Aerodrome schema compatibility, and future checksum-verified GitHub releases still require their own verification. Cloud tests do not claim those host checks have happened.

The filesystem restrictions in these user services require kernel support for unprivileged user namespaces (`PrivateUsers=yes`). The installer does not silently remove those protections if the host refuses them; report the service error for a supported deployment adjustment.
