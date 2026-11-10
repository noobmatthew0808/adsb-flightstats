# Updates without routine terminal commands

**Settings → Updates** shows the installed version, published stable release, changelog, persisted progress and recent update history. Sign in with your administrator password, press **Check for updates**, then **Update now**. A daily background check discovers new releases; it never installs automatically. The browser reconnects while the website restarts. A previous healthy release can be restored with **Rollback**.

There must first be a real GitHub Release in the dedicated `noobmatthew0808/adsb-flightstats` repository. No release is fabricated, and local development does not claim to be installed. The app reports an unavailable updater if the initial installer has not been run. Initial installation and first release publication remain separate actions.

## Security and separation

The browser talks to the normal website API using an authenticated administrator session, same-origin checks and a CSRF token. The website forwards only four fixed actions (`status`, `check`, `install`, `rollback`) to a separate updater process through a user-owned Unix socket with mode `0600`. It accepts no shell commands, arbitrary filesystem paths, unit names, repository names or download URLs from the browser.

The updater is unprivileged. Its one service-control command is `systemctl --user restart airspace-analytics.service`. It cannot restart Docker, Aerodrome or a feeder through its protocol. The app's service sandbox makes its release and `/opt/aerodrome` read-only and allows writes only in the new app's state directory. The updater writes only inside the installation root; `/opt/aerodrome` is read-only in both units. User namespaces must be supported for the systemd filesystem sandbox; installation reports a service failure rather than silently dropping that protection.

Release metadata comes from GitHub's HTTPS API for the fixed repository. Only stable semantic-version tags and an uploaded `airspace-analytics-X.Y.Z.zip` asset are accepted. Its URL must match that repository/tag, redirects must remain on approved GitHub download hosts, and its exact byte length and SHA-256 must match GitHub's asset metadata. TLS, artifact checksum and Python package hash verification remain enabled. The GitHub repository and release maintainers are therefore the update trust boundary: protect that account with strong authentication. An integrity checksum is not an independent author signature.

Downloaded archives reject traversal, absolute paths, links, special files, duplicate entries, encrypted files and excessive compressed/expanded size. Requirements install with `pip --require-hashes`. Sources and the release-local virtual environment are prepared in an inactive release directory; no live code or shared dependencies are overwritten.

An atomic `current` symlink selects the active release. A journal records the original release before switching. The updater restarts only the website, checks `/api/health`, verifies the reported release version, and confirms the read-only data source remains connected through `/api/status`. Failed startup or health verification restores the original release automatically. An interrupted switch is rolled back when the updater starts again. Failed inactive installations are removed so a transient failure can be retried through the UI. A retained release can be rolled back manually; its own failed health check restores the original current release. The updater then replaces its own process with the active release's code so updater fixes do not wait for a host reboot.

Persisted status and the last 50 audit outcomes live in `state/updates.json`. Dependency installation output is in the private `state/update-build.log`; secrets are not sent to the frontend. Old successful releases are retained for rollback; no automatic cleanup deletes them. Available disk space should be reviewed over time.

## Publishing a release

Build and test the project, set a new stable `VERSION` such as `1.0.1`, and run:

```bash
python3 scripts/build-release.py
```

The frontend must already have been compiled with `npm ci && npm run build` in `frontend`. Upload the resulting versioned ZIP as an asset of a published stable GitHub Release with tag `v1.0.1` in the fixed repository. GitHub supplies the SHA-256 asset digest in its release API. The separate `.sha256` file is useful for manual downloads; the updater requires GitHub's API digest and will not silently fall back to an unverified source archive. `BUILD-MANIFEST.json` in the ZIP records hashes of its packaged files.

Release checks use the repository's GitHub Release API. Public releases work without an API token; a private repository needs supported authenticated HTTPS access to that API and asset destination in the environment. No GitHub credential is embedded in this app or exposed to the browser. Network access must permit `api.github.com`, `github.com` and redirected GitHub asset hosts. The updater does not weaken the network policy to make a request succeed.

## Fallbacks

If the browser cannot reconnect, the local installation includes scoped command-line fallbacks: `bash ~/airspace-analytics/current/scripts/update.sh` and `bash ~/airspace-analytics/current/scripts/rollback.sh`. They use the same fixed-action Unix protocol. They are troubleshooting tools; routine updates use Settings.

Cloud integration tests exercise verified downloads, archive rejection, failed preparation, healthy activation and automatic/manual rollback with fake service control. They do not claim a release was published or that systemd has been exercised on your laptop.
