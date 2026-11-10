"""Exercise real installer preparation without a host systemd or package network."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import sqlite3
import socket
import subprocess


PROJECT = Path(__file__).resolve().parents[1]


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def fixture_source(tmp_path):
    source = tmp_path / 'download'
    (source / 'scripts').mkdir(parents=True)
    (source / 'frontend/dist').mkdir(parents=True)
    (source / 'frontend/dist/index.html').write_text('<h1>test release</h1>')
    (source / 'requirements.txt').write_text('')
    (source / 'VERSION').write_text('1.2.3')
    shutil.copy2(PROJECT / 'scripts/install.sh', source / 'scripts/install.sh')
    return source


def test_installer_prepares_isolated_release_and_preserves_database(tmp_path):
    source = fixture_source(tmp_path)
    dbpath = tmp_path / 'aircraft_history.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE all_sightings(icao TEXT,seen_at INTEGER)')
        db.execute('INSERT INTO all_sightings VALUES (?,?)', ('abc123', 100))
    original = hashlib.sha256(dbpath.read_bytes()).hexdigest()
    root = tmp_path / 'my analytics installation'
    command = ['bash', str(source / 'scripts/install.sh'), '--no-systemd', '--port', unused_port(), '--prefix', str(root), '--db', str(dbpath)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert root.joinpath('current').resolve() == root / 'releases/1.2.3'
    assert root.joinpath('current/.venv/bin/python').exists()
    assert root.joinpath('current/frontend/dist/index.html').read_text() == '<h1>test release</h1>'
    assert hashlib.sha256(dbpath.read_bytes()).hexdigest() == original
    env = (root / 'environment').read_text()
    assert 'AIRSPACE_DB="' + str(dbpath) + '"' in env
    assert 'AIRSPACE_ADMIN_PASSWORD_HASH="scrypt$' in env
    assert (root / 'environment').stat().st_mode & 0o077 == 0
    assert 'No services were stopped' not in result.stdout
    password = re.search(r'password manager\):\n([^\n]+)', result.stdout).group(1)
    salt, digest = re.search(r'scrypt\$([0-9a-f]+)\$([0-9a-f]+)', env).groups()
    assert hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex() == digest
    assert password not in env
    second = subprocess.run(command, text=True, capture_output=True, timeout=10)
    assert second.returncode != 0
    assert 'already exists' in second.stderr
    assert (root / 'environment').read_text() == env


def test_missing_database_does_not_create_install_directory(tmp_path):
    source = fixture_source(tmp_path)
    root = tmp_path / 'installation'
    result = subprocess.run(['bash', str(source / 'scripts/install.sh'), '--no-systemd', '--port', unused_port(), '--prefix', str(root), '--db', str(tmp_path / 'missing.db')], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'Cannot read Aerodrome database' in result.stderr
    assert not root.exists()
    assert not (tmp_path / 'missing.db').exists()


def test_rendered_user_units_pass_systemd_verification(tmp_path):
    """Verify the real templates, including installation paths containing spaces/%s."""
    import importlib.util
    import pytest
    analyzer = shutil.which('systemd-analyze')
    if not analyzer:
        pytest.skip('systemd-analyze is not installed on this platform')
    module_path = PROJECT / 'deploy/render_units.py'
    spec = importlib.util.spec_from_file_location('render_units', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'my analytics %s installation'
    (root / 'current/.venv/bin').mkdir(parents=True)
    (root / 'current/.venv/bin/python').symlink_to(shutil.which('python3'))
    (root / 'state').mkdir()
    (root / 'environment').write_text('')
    output = tmp_path / 'units'
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    module.render(PROJECT / 'deploy', output, root, 8090)
    for name in module.NAMES:
        text = (output / name).read_text()
        assert 'WorkingDirectory="' not in text
        assert f'WorkingDirectory={str(root).replace("%", "%%")}/current' in text
        assert '@ROOT@' not in text and '@WORKING_DIRECTORY@' not in text
    result = subprocess.run([analyzer, '--user', 'verify', *(str(output / name) for name in module.NAMES)], env={**os.environ, 'XDG_RUNTIME_DIR': str(runtime)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
