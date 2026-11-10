#!/usr/bin/env python3
"""Package a prebuilt, hash-verifiable project ZIP. Never includes local data or credentials."""
import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "state", "releases", "artifacts", ".DS_Store", ".env", "environment", ".coverage", "playwright-report", "test-results"}


def build(output):
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("VERSION must be a stable semantic version")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build frontend first: cd frontend && npm ci && npm run build")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"airspace-analytics-{version}.zip"
    files = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if path.is_symlink():
            raise SystemExit(f"Refusing symlink: {relative}")
        if not path.is_file() or output == path or output in path.parents:
            continue
        if path.suffix in {".db", ".sqlite", ".sqlite3", ".pyc", ".zip", ".log", ".tsbuildinfo"} or path.name.startswith(".env"):
            continue
        files.append((path, relative))
    manifest = {str(relative): hashlib.sha256(path.read_bytes()).hexdigest() for path, relative in files}
    with zipfile.ZipFile(asset, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, relative in files:
            archive.write(path, "airspace-analytics/" + relative.as_posix())
        archive.writestr("airspace-analytics/BUILD-MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_suffix(".zip.sha256").write_text(f"{digest}  {asset.name}\n")
    print(json.dumps({"archive": str(asset), "sha256": digest, "files": len(files), "bytes": asset.stat().st_size}, indent=2))
    return asset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts"))
    build(parser.parse_args().output_dir)
