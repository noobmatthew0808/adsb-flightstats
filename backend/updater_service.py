"""User-owned Unix socket updater daemon and a fixed-action CLI/client."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import socketserver
import stat
import threading
import time
from pathlib import Path
from .updater import UpdateError, UpdateManager

ACTIONS = frozenset({"status", "check", "install", "rollback"})


def request(root: str | Path, action: str = "status") -> dict:
    if action not in ACTIONS:
        raise UpdateError("Unsupported updater action")
    path = Path(root).expanduser().resolve() / "state" / "updater.sock"
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UpdateError("Updater socket ownership or permissions are unsafe")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(path))
            client.sendall(json.dumps({"action": action}).encode() + b"\n")
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                part = client.recv(65536)
                if not part:
                    break
                chunks.extend(part)
                if len(chunks) > 128 * 1024:
                    raise UpdateError("Updater response exceeds the limit")
            result = json.loads(chunks)
    except (OSError, ValueError) as exc:
        raise UpdateError("The local updater is not available; check airspace-analytics-updater.service") from exc
    if result.get("error"):
        raise UpdateError(result["error"])
    return result


class UpdaterServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, manager, refresh=None):
        self.manager = manager
        self.refresh = refresh
        self.operation = threading.Lock()
        super().__init__(str(path), Handler)

    def check_if_due(self, now=None):
        now = time.time() if now is None else now
        last = self.manager._read().get("last_check", 0)
        if now - last >= 24 * 3600:
            self.start_action("check")
            return True
        return False

    def start_action(self, action):
        if not self.operation.acquire(blocking=False):
            raise UpdateError("Another update operation is already running")
        def run():
            successful = False
            try:
                getattr(self.manager, action)()
                successful = True
            except Exception as exc:
                # The manager persists detailed safe errors and audit outcomes.
                if self.manager.status().get("status") not in ("failed", "check_failed"):
                    self.manager._write(status="failed", progress=0, message=str(exc) if isinstance(exc, UpdateError) else "Updater operation failed")
            finally:
                self.operation.release()
            if successful and action in ("install", "rollback") and self.refresh:
                try:
                    self.refresh()
                except OSError:
                    self.manager._write(message="Website release is active; updater process refresh failed and will retry at next service start")
        self.manager._write(status="checking" if action == "check" else "installing" if action == "install" else "rolling_back", progress=0, message="Checking GitHub releases" if action == "check" else "Starting " + action)
        threading.Thread(target=run, name="airspace-update", daemon=False).start()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            raw = self.rfile.readline(1025)
            if len(raw) > 1024 or not raw.endswith(b"\n"):
                raise UpdateError("Invalid updater request size")
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"action"} or data.get("action") not in ACTIONS:
                raise UpdateError("Only fixed updater actions are accepted")
            if data["action"] != "status":
                self.server.start_action(data["action"])
            result = self.server.manager.status()
        except Exception as exc:
            result = {"error": str(exc) if isinstance(exc, UpdateError) else "Invalid updater request"}
        self.wfile.write(json.dumps(result).encode() + b"\n")


def serve(root):
    root = Path(root).expanduser().resolve()
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    manager = UpdateManager(root)
    with (state / "updater-daemon.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("Updater daemon is already running") from exc
        path = state / "updater.sock"
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise UpdateError("Refusing to replace a foreign updater socket path")
            path.unlink()
        old_umask = os.umask(0o177)
        try:
            def refresh():
                # Replace this daemon with the active release after work completes. No other service is restarted.
                current = manager._target("current")
                if not current:
                    return
                python = current / ".venv/bin/python"
                os.chdir(current)
                os.execv(str(python), [str(python), "-m", "backend.updater_service", "--root", str(root), "serve"])
            with UpdaterServer(path, manager, refresh=refresh) as server:
                os.chmod(path, 0o600)
                stop = threading.Event()
                def periodic_check():
                    # Start after the app has settled; never overlap a user-triggered operation.
                    if stop.wait(60):
                        return
                    while not stop.is_set():
                        try:
                            server.check_if_due()
                        except UpdateError:
                            pass
                        if stop.wait(3600):
                            return
                threading.Thread(target=periodic_check, name="airspace-release-check", daemon=True).start()
                try:
                    server.serve_forever(poll_interval=0.25)
                finally:
                    stop.set()
        finally:
            os.umask(old_umask)
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Airspace Analytics fixed-action updater")
    parser.add_argument("--root", default=os.environ.get("AIRSPACE_INSTALL_ROOT", str(Path.home() / "airspace-analytics")))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve")
    cli = sub.add_parser("client")
    cli.add_argument("action", choices=sorted(ACTIONS))
    args = parser.parse_args()
    try:
        if args.command == "client":
            print(json.dumps(request(args.root, args.action), indent=2))
        else:
            serve(args.root)
    except UpdateError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
