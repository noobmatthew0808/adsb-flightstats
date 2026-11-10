import hashlib
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from backend.updater import UpdateError, UpdateManager, safe_extract, validate_url
from backend.updater_service import UpdaterServer, request


def archive(version="1.1.0", extra=None):
    out = io.BytesIO()
    files = {"VERSION": version, "backend/app.py": "app=True", "backend/updater.py": "", "backend/updater_service.py": "", "requirements.txt": "", "frontend/dist/index.html": "<!doctype html>"}
    if extra:
        files.update(extra)
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr("airspace-analytics/" + name, data)
    return out.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = self.root / "releases/1.0.0"
        self.old.mkdir(parents=True)
        (self.old / "VERSION").write_text("1.0.0")
        (self.root / "current").symlink_to("releases/1.0.0")
        self.data = archive()
        self.restarts = []
        self.metadata = {"tag_name": "v1.1.0", "draft": False, "prerelease": False, "body": "A useful release", "assets": [{"name": "airspace-analytics-1.1.0.zip", "browser_download_url": "https://github.com/noobmatthew0808/adsb-flightstats/releases/download/v1.1.0/airspace-analytics-1.1.0.zip", "digest": "sha256:" + hashlib.sha256(self.data).hexdigest(), "size": len(self.data)}]}
        def fetch(url, limit):
            return json.dumps(self.metadata).encode() if "api.github.com" in url else self.data
        self.manager = UpdateManager(self.root, fetch=fetch, prepare=lambda p: None, restart=lambda: self.restarts.append("app"), health=lambda v: True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_and_install_verified_release(self):
        self.assertEqual(self.manager.check()["available_version"], "1.1.0")
        result = self.manager.install()
        self.assertEqual(result["installed_version"], "1.1.0")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["can_rollback"])
        self.assertEqual(self.restarts, ["app"])
        self.assertEqual((self.root / "previous").resolve(), self.old)

    def test_digest_mismatch_retains_current(self):
        self.data += b"tampered"
        with self.assertRaisesRegex(UpdateError, "verification"):
            self.manager.install()
        self.assertEqual((self.root / "current").resolve(), self.old)
        self.assertFalse(self.restarts)

    def test_no_digest_no_install(self):
        self.metadata["assets"][0].pop("digest")
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            self.manager.check()

    def test_wrong_repository_asset_rejected(self):
        self.metadata["assets"][0]["browser_download_url"] = "https://github.com/evil/project/releases/download/v1.1.0/airspace-analytics-1.1.0.zip"
        with self.assertRaisesRegex(UpdateError, "approved repository"):
            self.manager.install()

    def test_semver_injection_rejected(self):
        self.metadata["tag_name"] = "../../tmp/pwned"
        with self.assertRaises(UpdateError):
            self.manager.check()

    def test_missing_release_asset(self):
        self.metadata["assets"] = []
        with self.assertRaises(UpdateError):
            self.manager.install()

    def test_version_mismatch_rejected(self):
        self.data = archive("9.9.9")
        self.metadata["assets"][0].update(digest="sha256:" + hashlib.sha256(self.data).hexdigest(), size=len(self.data))
        with self.assertRaisesRegex(UpdateError, "version"):
            self.manager.install()
        self.assertFalse(self.restarts)

    def test_failed_health_automatically_rolls_back(self):
        self.manager.health = lambda version: version == "1.0.0"
        with self.assertRaisesRegex(UpdateError, "previous release restored"):
            self.manager.install()
        self.assertEqual((self.root / "current").resolve(), self.old)
        self.assertEqual(len(self.restarts), 2)
        self.assertFalse(self.manager.status()["history"][-1]["success"])

    def test_prepare_failure_allows_retry(self):
        def fail(path):
            raise RuntimeError("pip failed")
        self.manager.prepare = fail
        with self.assertRaises(UpdateError):
            self.manager.install()
        self.assertFalse((self.root / "releases/1.1.0").exists())
        self.manager.prepare = lambda p: None
        self.manager.install()
        self.assertEqual(self.manager.status()["installed_version"], "1.1.0")

    def test_manual_rollback(self):
        self.manager.install()
        self.assertEqual(self.manager.rollback()["installed_version"], "1.0.0")
        self.assertEqual((self.root / "previous").resolve().name, "1.1.0")

    def test_failed_rollback_preserves_original(self):
        self.manager.install()
        self.manager.health = lambda v: False
        with self.assertRaisesRegex(UpdateError, "original current"):
            self.manager.rollback()
        self.assertEqual(self.manager.status()["installed_version"], "1.1.0")

    def test_concurrent_operation_rejected(self):
        with self.manager._exclusive():
            with self.assertRaisesRegex(UpdateError, "already running"):
                self.manager.check()

    def test_external_release_link_rejected(self):
        (self.root / "current").unlink()
        (self.root / "current").symlink_to("/tmp")
        with self.assertRaisesRegex(UpdateError, "outside"):
            self.manager.status()

    def test_interrupted_switch_restores_original(self):
        new = self.root / "releases/1.1.0"
        new.mkdir()
        (new / "VERSION").write_text("1.1.0")
        self.manager._write(status="restarting", transaction={"original": "1.0.0", "target": "1.1.0"})
        self.manager._link("current", new)
        recovered = UpdateManager(self.root, restart=lambda: self.restarts.append("recovery"), health=lambda v: v == "1.0.0")
        self.assertEqual(recovered.status()["installed_version"], "1.0.0")
        self.assertEqual(recovered.status()["status"], "failed")
        self.assertIn("health verified", recovered.status()["message"])
        self.assertEqual(self.restarts, ["recovery"])

    def test_failed_update_has_no_self_rollback(self):
        self.manager.health = lambda version: version == "1.0.0"
        with self.assertRaises(UpdateError):
            self.manager.install()
        self.assertFalse(self.manager.status()["can_rollback"])

    def test_downgrade_requires_rollback(self):
        (self.old / "VERSION").write_text("2.0.0")
        with self.assertRaisesRegex(UpdateError, "downgrade"):
            self.manager.install()
        self.assertFalse(self.restarts)

    def test_private_state_file(self):
        self.manager.check()
        self.assertEqual(stat.S_IMODE(self.manager.state_file.stat().st_mode), 0o600)

    def test_fixed_repository_only(self):
        with self.assertRaises(UpdateError):
            UpdateManager(self.root, repo="attacker/repo")

    def test_extract_traversal_rejected(self):
        for name in ("../pwned", "/tmp/pwned", "foo/../../pwned", "x\\..\\pwned"):
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                z.writestr(name, "oops")
            with self.assertRaises(UpdateError):
                safe_extract(out.getvalue(), self.root / "target")

    def test_extract_link_rejected(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            info = zipfile.ZipInfo("project/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "../../etc/passwd")
        with self.assertRaisesRegex(UpdateError, "link"):
            safe_extract(out.getvalue(), self.root / "target")

    def test_only_secure_approved_hosts(self):
        for url in ("http://github.com/foo", "https://evil.example/foo", "https://github.com.evil.example/foo", "https://user:pass@github.com/foo", "https://github.com:8080/foo"):
            with self.assertRaises(UpdateError):
                validate_url(url)
        validate_url("https://release-assets.githubusercontent.com/path")

    def test_periodic_check_discovers_but_never_installs(self):
        path = self.root / "state/updater.sock"
        with UpdaterServer(path, self.manager) as server:
            self.assertTrue(server.check_if_due())
            self.assertTrue(server.operation.acquire(timeout=2))
            server.operation.release()
            self.assertEqual(self.manager.status()["available_version"], "1.1.0")
            self.assertEqual(self.manager.status()["installed_version"], "1.0.0")
            self.assertFalse(server.check_if_due())
            self.assertFalse(self.restarts)

    def test_unix_socket_protocol_rejects_commands(self):
        path = self.root / "state/updater.sock"
        with UpdaterServer(path, self.manager) as server:
            os.chmod(path, 0o600)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertEqual(request(self.root, "status")["installed_version"], "1.0.0")
                with self.assertRaises(UpdateError):
                    request(self.root, "rm -rf /")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                    c.connect(str(path))
                    c.sendall(b'{"action":"install","shell":"uname"}\n')
                    self.assertIn("error", json.loads(c.recv(8192)))
            finally:
                server.shutdown()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
