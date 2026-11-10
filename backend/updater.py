"""Constrained release manager. Never accepts shell text, paths, URLs or unit names from clients."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "noobmatthew0808/adsb-flightstats"
UNIT = "airspace-analytics.service"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 180 * 1024 * 1024
VERSION_RE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
RELEASE_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class UpdateError(RuntimeError):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in RELEASE_HOSTS or u.username or u.password or u.port not in (None, 443):
        raise UpdateError("Release download destination is not approved")


def download(url: str, limit: int = MAX_ARCHIVE) -> bytes:
    validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Airspace-Analytics-Updater/1", "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/octet-stream"})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(req, timeout=30) as response:
            validate_url(response.geturl())
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published release is available for the configured repository") from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code}; no installed files were changed") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError("Cannot reach GitHub securely; check network access and retry") from exc
    if len(data) > limit:
        raise UpdateError("Release response exceeds the size limit")
    return data


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            os.chmod(tmp, 0o600)
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def safe_extract(data: bytes, target: Path) -> None:
    """Only a single normal project root; never links, special files or absolute/traversal paths."""
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 20000:
                raise UpdateError("Invalid release entry count")
            total = 0
            roots = set()
            seen = set()
            for info in entries:
                name = info.filename
                p = PurePosixPath(name)
                mode = info.external_attr >> 16
                if not name or "\\" in name or "\x00" in name or p.is_absolute() or any(x in ("..", ".") for x in name.split("/")) or (p.parts and ":" in p.parts[0]):
                    raise UpdateError("Unsafe release archive path")
                if not p.parts:
                    raise UpdateError("Empty release archive path")
                roots.add(p.parts[0])
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise UpdateError("Release archive contains a link or special file")
                if info.flag_bits & 1:
                    raise UpdateError("Encrypted release archives are not supported")
                if name in seen:
                    raise UpdateError("Duplicate release archive path")
                seen.add(name)
                total += info.file_size
                if total > MAX_EXPANDED or info.file_size > MAX_ARCHIVE:
                    raise UpdateError("Expanded release exceeds the size limit")
            if len(roots) != 1:
                raise UpdateError("Release archive must contain a single project directory")
            for info in entries:
                parts = PurePosixPath(info.filename).parts[1:]
                if not parts:
                    continue
                out = target.joinpath(*parts)
                if info.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, out.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
                os.chmod(out, 0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Release archive could not be safely extracted") from exc
    for required in ("backend/app.py", "backend/updater.py", "backend/updater_service.py", "requirements.txt", "frontend/dist/index.html", "VERSION"):
        if not (target / required).is_file():
            raise UpdateError(f"Release is missing {required}")


class UpdateManager:
    def __init__(self, root: str | Path, repo: str = REPOSITORY, restart=None, health=None, fetch=None, prepare=None):
        self.root = Path(root).expanduser().resolve()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.releases = self.root / "releases"
        self.releases.mkdir(exist_ok=True)
        if repo != REPOSITORY:
            raise UpdateError("Only the installation's approved GitHub repository is allowed")
        self.repo = repo
        self.restart = restart or self._restart
        self.health = health or self._health
        self.fetch = fetch or download
        self.prepare = prepare or self._prepare
        self.thread_lock = threading.Lock()
        self.state_file = self.state_dir / "updates.json"
        if self.state_file.exists():
            old = self._read()
            if old.get("status") in ("downloading", "installing", "restarting", "rolling_back"):
                self._recover_interrupted(old)

    def _recover_interrupted(self, state):
        transaction = state.get("transaction")
        message = "An update was interrupted before switching releases; the current release was retained"
        if isinstance(transaction, dict):
            original = transaction.get("original", "")
            if not isinstance(original, str) or not VERSION_RE.fullmatch(original):
                raise UpdateError("Interrupted update journal has an invalid original version")
            target = self.releases / original
            if not target.is_dir():
                raise UpdateError("Interrupted update original release is missing")
            self._link("current", target)
            try:
                self.restart()
                healthy = self.health(original.removeprefix("v"))
            except Exception:
                healthy = False
            message = "Interrupted update rolled back to its original release" + ("; health verified" if healthy else "; health could not be confirmed")
        self._write(status="failed", progress=0, transaction=None, message=message)

    def _read(self):
        try:
            data = json.loads(self.state_file.read_text())
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"status": "idle", "progress": 0, "message": "Check for a published release to begin", "history": [], "available_version": None, "release_notes": ""}

    def _write(self, **changes):
        data = self._read()
        data.update(changes)
        atomic_json(self.state_file, data)
        return data

    def _target(self, name):
        link = self.root / name
        if not link.is_symlink():
            return None
        target = link.resolve()
        if target.parent != self.releases or not target.is_dir() or target.name.startswith("."):
            raise UpdateError("Installation release link is outside the releases directory")
        return target

    def status(self):
        data = self._read()
        current = self._target("current")
        data["installed_version"] = (current / "VERSION").read_text().strip() if current and (current / "VERSION").exists() else "development"
        data["configured"] = bool(current)
        previous = self._target("previous")
        data["can_rollback"] = previous is not None and previous != current
        return {k: v for k, v in data.items() if k not in ("release", "transaction")}

    @contextlib.contextmanager
    def _exclusive(self):
        if not self.thread_lock.acquire(blocking=False):
            raise UpdateError("Another update operation is already running")
        handle = (self.state_dir / "update.lock").open("a+")
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateError("Another updater process is already running") from exc
            yield
        finally:
            handle.close()
            self.thread_lock.release()

    def _release(self):
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            release = json.loads(self.fetch(url, 2 * 1024 * 1024))
        except (ValueError, TypeError) as exc:
            raise UpdateError("GitHub returned invalid release metadata") from exc
        tag = release.get("tag_name", "")
        if not isinstance(tag, str) or not VERSION_RE.fullmatch(tag) or release.get("draft") or release.get("prerelease"):
            raise UpdateError("Latest release is not a stable semantic version")
        version = tag.removeprefix("v")
        expected_name = f"airspace-analytics-{version}.zip"
        assets = [a for a in release.get("assets", []) if a.get("name") == expected_name]
        if len(assets) != 1:
            raise UpdateError(f"Release must contain exactly one {expected_name} asset")
        asset = assets[0]
        asset_url = asset.get("browser_download_url", "")
        expected_prefix = f"https://github.com/{self.repo}/releases/download/{tag}/"
        if asset_url != expected_prefix + expected_name:
            raise UpdateError("Release asset does not belong to the approved repository and tag")
        digest = asset.get("digest", "")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            # GitHub provides SHA-256 digests for uploaded release assets. Do not fall back to unverified source ZIPs.
            raise UpdateError("Release asset has no verified SHA-256 digest in GitHub metadata")
        size = asset.get("size", 0)
        if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE:
            raise UpdateError("Release asset size is outside the allowed limit")
        return {"version": version, "tag": tag, "url": asset_url, "digest": digest[7:].lower(), "size": size, "notes": str(release.get("body") or "")[:30000]}

    def check(self):
        with self._exclusive():
            try:
                release = self._release()
                self._write(last_check=int(time.time()), available_version=release["version"], release_notes=release["notes"], release=release, status="available" if release["version"] != self.status()["installed_version"] else "up_to_date", progress=0, message="Release information checked securely with GitHub")
            except UpdateError as exc:
                self._write(last_check=int(time.time()), status="check_failed", message=str(exc), progress=0)
                raise
        return self.status()

    def _link(self, name, target):
        if target.parent != self.releases:
            raise UpdateError("Release path is outside installation")
        tmp = self.root / ("." + name + "-" + uuid.uuid4().hex)
        tmp.symlink_to(target.relative_to(self.root))
        os.replace(tmp, self.root / name)

    def _prepare(self, path):
        """Only verified project code/dependencies are installed, never executing an installer as root."""
        python = path / ".venv" / "bin" / "python"
        log = self.state_dir / "update-build.log"
        with log.open("w") as out:
            os.chmod(log, 0o600)
            subprocess.run([sys.executable, "-m", "venv", str(path / ".venv")], cwd=path, check=True, stdout=out, stderr=subprocess.STDOUT, timeout=120)
            subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", "requirements.txt"], cwd=path, check=True, stdout=out, stderr=subprocess.STDOUT, timeout=600)
            subprocess.run([str(python), "-c", "from backend.app import app; assert app"], cwd=path, check=True, stdout=out, stderr=subprocess.STDOUT, timeout=30)

    @staticmethod
    def _restart():
        subprocess.run(["systemctl", "--user", "restart", UNIT], check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _health(self, version):
        port = int(os.environ.get("AIRSPACE_PORT", "8088"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                    result = json.loads(response.read(8192))
                if result.get("ok") is True and result.get("version") == version:
                    with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=3) as response:
                        source_status = json.loads(response.read(65536))
                    if source_status.get("source", {}).get("connected") is True:
                        return True
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(1)
        return False

    def _history(self, action, version, success, message):
        history = self._read().get("history", [])[-49:]
        history.append({"timestamp": int(time.time()), "action": action, "version": version, "success": success, "message": message})
        self._write(history=history)

    def install(self):
        with self._exclusive():
            current = self._target("current")
            if current is None:
                raise UpdateError("Run the initial installer before updating")
            release = self._release()  # Re-check trusted metadata instead of trusting cached client input.
            version = release["version"]
            installed = self.status()["installed_version"]
            if version == installed:
                self._write(status="up_to_date", progress=100, message="This release is already installed")
                return self.status()
            if VERSION_RE.fullmatch(installed) and tuple(map(int, version.split("."))) < tuple(map(int, installed.removeprefix("v").split("."))):
                raise UpdateError("Automatic installation refuses a downgrade; use the explicit rollback control")
            target = self.releases / version
            staging = self.releases / (".staging-" + uuid.uuid4().hex)
            switched = False
            created_target = False
            try:
                self._write(status="downloading", progress=10, available_version=version, message="Downloading release over verified HTTPS")
                data = self.fetch(release["url"], MAX_ARCHIVE)
                if len(data) != release["size"] or hashlib.sha256(data).hexdigest() != release["digest"]:
                    raise UpdateError("Release SHA-256 or byte length verification failed")
                self._write(status="installing", progress=30, message="Checksum verified; preparing isolated release")
                staging.mkdir(mode=0o700)
                safe_extract(data, staging)
                if (staging / "VERSION").read_text().strip() != version:
                    raise UpdateError("Release version does not match its GitHub tag")
                if target.exists():
                    raise UpdateError("Release directory already exists; refusing to overwrite a retained release")
                # Prepare under a stable absolute path: virtualenv launchers embed that path.
                staging.rename(target)
                created_target = True
                self.prepare(target)
                self._write(status="restarting", progress=80, message="Switching only Airspace Analytics to the new release")
                self._write(transaction={"original": current.name, "target": target.name})
                self._link("previous", current)
                self._link("current", target)
                switched = True
                self.restart()
                if not self.health(version):
                    raise UpdateError("New release did not pass its version and health check")
                self._write(status="complete", progress=100, transaction=None, message="Update installed and health verified")
                self._history("install", version, True, "Health verified")
            except Exception as exc:
                message = str(exc) if isinstance(exc, UpdateError) else "Release preparation or service restart failed; see the local update-build.log"
                if switched:
                    self._write(status="rolling_back", progress=90, message="Restoring previous release after failed health check")
                    self._link("current", current)
                    try:
                        self.restart()
                        restored = self.health((current / "VERSION").read_text().strip())
                    except Exception:
                        restored = False
                    message += "; previous release restored" if restored else "; rollback health could not be confirmed"
                self._write(status="failed", progress=0, transaction=None, message=message)
                self._history("install", version, False, message)
                if created_target and self._target("current") != target:
                    shutil.rmtree(target)
                raise UpdateError(message) from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return self.status()

    def rollback(self):
        with self._exclusive():
            current, previous = self._target("current"), self._target("previous")
            if not current or not previous or current == previous:
                raise UpdateError("No distinct previous release is available")
            version = (previous / "VERSION").read_text().strip()
            self._write(status="rolling_back", progress=40, message="Switching only Airspace Analytics to the retained release")
            try:
                self._write(transaction={"original": current.name, "target": previous.name})
                self._link("current", previous)
                self.restart()
                if not self.health(version):
                    raise UpdateError("Retained release did not pass its health check")
                self._link("previous", current)
                self._write(status="complete", progress=100, transaction=None, message="Rollback complete and health verified")
                self._history("rollback", version, True, "Health verified")
            except Exception as exc:
                self._link("current", current)
                try:
                    self.restart()
                except Exception:
                    pass
                self._write(status="failed", progress=0, transaction=None, message="Rollback failed; original current release restored")
                self._history("rollback", version, False, "Health or restart failed")
                raise UpdateError("Rollback failed; original current release restored") from exc
        return self.status()
