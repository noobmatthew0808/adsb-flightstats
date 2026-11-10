"""HTTP acceptance checks for the real read-only and administrator boundaries."""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Config
from backend.security import hash_password, verify_password

START = 1794220800  # Synthetic timestamps; no user receiver data is included.
PASSWORD = 'correct horse battery airspace'


def source_file(path: Path):
    with sqlite3.connect(path) as con:
        con.executescript('''
            CREATE TABLE all_sightings (
                id INTEGER PRIMARY KEY, icao TEXT, callsign TEXT, speed REAL,
                lat REAL, lon REAL, altitude REAL, aircraft_type TEXT,
                type_desc TEXT, seen_at INTEGER, squawk TEXT);
            CREATE INDEX idx_all_seen_icao ON all_sightings(seen_at, icao);
            CREATE TABLE seen_aircraft (
                icao TEXT, registration TEXT, aircraft_type TEXT,
                aircraft_type_desc TEXT, operator TEXT, category TEXT,
                country TEXT, first_seen_at INTEGER, last_seen_at INTEGER);
            CREATE TABLE concurrent_minute (minute_bucket INTEGER, count INTEGER);
        ''')
        con.executemany('INSERT INTO all_sightings VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
            (1, 'ABC123', 'BAW123', 300, 51.5, -0.2, 20000, 'A320', 'Airbus', START + 10, '7000'),
            (2, 'ABC123', 'BAW123', 305, 51.6, -0.3, 22000, 'A320', 'Airbus', START + 20, '7000'),
            (3, 'DEF456', '=1+1', 90, None, None, 1200, 'C172', 'Cessna', START + 70, '7000'),
        ])
        con.executemany('INSERT INTO seen_aircraft VALUES (?,?,?,?,?,?,?,?,?)', [
            ('ABC123', None, 'A320', 'Airbus A320', 'British Airways', 'commercial', 'United Kingdom', START+10, START+20),
            ('DEF456', None, 'C172', 'Cessna 172', None, 'general_aviation', 'United Kingdom', START+70, START+70),
        ])
        con.executemany('INSERT INTO concurrent_minute VALUES (?,?)', [(START, 1), (START+60, 1), (START+120, 0)])


@pytest.fixture
def config(tmp_path):
    db = tmp_path / 'history.db'
    source_file(db)
    return Config(db=db, state=tmp_path/'state', install_root=None,
                  password_hash=hash_password(PASSWORD))


@pytest.fixture
def client(config):
    with TestClient(create_app(config), base_url='http://localhost') as client:
        yield client


def administrator(client):
    result = client.post('/api/auth/login', json={'password': PASSWORD}, headers={'Origin': 'http://localhost'})
    assert result.status_code == 200
    return {'X-CSRF-Token': result.json()['csrf_token'], 'Origin': 'http://localhost'}


def params(**overrides):
    return {'start': START, 'end': START+180, 'group':'hour', **overrides}


def test_source_counts_and_readonly_functional_requests(client, config):
    before = hashlib.sha256(config.db.read_bytes()).hexdigest()
    status = client.get('/api/status')
    assert status.status_code == 200
    assert status.json()['demo'] is False
    assert status.json()['source']['observations'] == 3
    assert status.json()['source']['aircraft'] == 2
    assert status.json()['timezone'] == 'Europe/London'
    totals = {}
    for metric in ('aircraft', 'sessions', 'observations'):
        response = client.get('/api/analytics', params=params(metric=metric))
        assert response.status_code == 200, response.text
        totals[metric] = response.json()['total']
    assert totals == {'aircraft':2, 'sessions':2, 'observations':3}
    aircraft = client.get('/api/aircraft/abc123', params=params())
    assert aircraft.status_code == 200
    assert aircraft.json()['total'] == 2
    assert aircraft.json()['metadata']['icao'] == 'ABC123'
    assert client.get('/api/data/tables').status_code == 200
    assert client.get('/api/data/rows').json()['total'] == 3
    assert hashlib.sha256(config.db.read_bytes()).hexdigest() == before
    assert not Path(str(config.db)+'-journal').exists()


def test_missing_source_reports_error_without_creating_database(config):
    missing = config.db.parent/'does-not-exist.db'
    replacement = Config(missing,config.state,None,config.password_hash)
    with TestClient(create_app(replacement), base_url='http://localhost') as client:
        result = client.get('/api/status').json()
        assert not result['source']['connected']
        assert result['source']['error']
        assert result['demo'] is False
        assert client.get('/api/analytics', params=params()).status_code == 503
        assert client.get('/api/data/tables').status_code == 503
    assert not missing.exists()


@pytest.mark.parametrize('query', [
    {'metric':'messages'}, {'group':'DROP TABLE all_sightings'},
    {'start':'not-a-date'}, {'start':START+500, 'end':START},
    {'session_gap':-1}, {'month':13}, {'category':'x'*101},
])
def test_analytics_rejects_invalid_queries(client, query):
    response = client.get('/api/analytics', params=params(**query))
    assert response.status_code == 422, response.text
    assert response.json()['detail']


@pytest.mark.parametrize('query', [
    {'table':'all_sightings; DROP TABLE all_sightings'},
    {'table':'sqlite_master'}, {'table':'../history.db'},
    {'sort':'seen_at; DELETE FROM all_sightings'},
    {'direction':'DESC; DELETE FROM all_sightings'},
    {'limit':10001}, {'offset':-1},
])
def test_database_identifiers_are_allowlisted(client, query):
    result = client.get('/api/data/rows', params=query)
    assert result.status_code == 422, result.text
    assert client.get('/api/status').json()['source']['observations'] == 3


def test_search_is_literal_and_pagination_bounded(client):
    assert client.get('/api/data/rows', params={'search': "' OR 1=1 --"}).json()['total'] == 0
    assert client.get('/api/data/rows', params={'search':'%'}).json()['total'] == 0
    assert client.get('/api/data/rows', params={'search':'BAW'}).json()['total'] == 2
    one = client.get('/api/data/rows', params={'limit':1,'offset':1,'sort':'id','direction':'asc'}).json()
    assert one['total'] == 3 and len(one['rows']) == 1 and one['rows'][0]['id'] == 2


def test_csv_export_escapes_spreadsheet_formulas(client):
    result = client.get('/api/data/export', params={'search':'=1+1'})
    assert result.status_code == 200
    assert result.headers['content-disposition'].startswith('attachment;')
    assert 'text/csv' in result.headers['content-type']
    rows = list(csv.DictReader(io.StringIO(result.text)))
    assert len(rows) == 1
    assert rows[0]['callsign'] == "'=1+1"
    aggregate = client.get('/api/analytics/export',params=params())
    assert aggregate.status_code == 200
    assert 'coverage_pct' in aggregate.text


def test_mutation_requires_login_and_csrf(client):
    settings = client.get('/api/settings').json()
    assert client.put('/api/settings',json=settings).status_code == 401
    assert client.post('/api/updates/install').status_code == 401
    headers = administrator(client)
    assert client.get('/api/auth/session').json()['authenticated'] is True
    assert client.put('/api/settings',json=settings).status_code == 403
    bad = {**headers,'X-CSRF-Token':'untrusted'}
    assert client.put('/api/settings',json=settings,headers=bad).status_code == 403
    assert client.put('/api/settings',json=settings,headers=headers).status_code == 200
    assert client.post('/api/updates/check',headers=headers).status_code == 409
    assert client.post('/api/updates/run-shell',headers=headers).status_code == 404
    assert client.post('/api/auth/logout',headers=headers).status_code == 200
    assert client.get('/api/auth/session').json()['authenticated'] is False
    assert client.put('/api/settings',json=settings,headers=headers).status_code == 401


def test_cross_origin_login_and_mutation_are_rejected(client):
    assert client.post('/api/auth/login',json={'password':PASSWORD},headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/auth/login',json={'password':PASSWORD},headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    headers = administrator(client)
    settings = client.get('/api/settings').json()
    assert client.put('/api/settings',json=settings,headers={**headers,'Origin':'http://localhost.evil.example'}).status_code == 403
    assert client.put('/api/settings',json=settings,headers={**headers,'Origin':'https://localhost'}).status_code == 403


def test_login_cookie_and_password_hash(client):
    result = client.post('/api/auth/login',json={'password':PASSWORD})
    assert result.status_code == 200
    cookie = result.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=28800' in cookie
    assert PASSWORD not in result.text
    encoded = hash_password(PASSWORD)
    assert verify_password(PASSWORD,encoded)
    assert not verify_password('wrong',encoded)
    assert not verify_password(PASSWORD,'scrypt$not-hex$broken')


def test_incorrect_password_attempts_are_throttled(client):
    for _ in range(8):
        assert client.post('/api/auth/login',json={'password':'wrong'}).status_code == 401
    assert client.post('/api/auth/login',json={'password':PASSWORD}).status_code == 429


def test_settings_survive_restart_and_validate_urls(client,config):
    headers = administrator(client)
    settings = client.get('/api/settings').json()
    settings['theme'] = 'light'
    settings['session_gap_minutes'] = 45
    settings['services'][0]['url'] = 'javascript:alert(1)'
    assert client.put('/api/settings',json=settings,headers=headers).status_code == 422
    settings['services'][0]['url'] = 'https://user:password@example.com'
    assert client.put('/api/settings',json=settings,headers=headers).status_code == 422
    settings['services'][0]['url'] = 'http://192.168.1.187:8000'
    assert client.put('/api/settings',json=settings,headers=headers).status_code == 200
    with TestClient(create_app(config),base_url='http://localhost') as restarted:
        current = restarted.get('/api/settings').json()
        assert current['theme'] == 'light' and current['session_gap_minutes'] == 45
        assert restarted.get('/api/auth/session').json()['authenticated'] is False
    assert (config.state/'settings.json').stat().st_mode & 0o077 == 0
    assert not (config.state/'settings.tmp').exists()


def test_host_protection_security_headers_and_unknown_api(client):
    assert client.get('/api/health',headers={'Host':'attacker.example'}).status_code == 400
    result = client.get('/api/health')
    assert result.status_code == 200 and result.json()['ok'] is True
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert result.headers['x-frame-options'] == 'DENY'
    assert result.headers['cache-control'] == 'no-store'
    assert "script-src 'self'" in result.headers['content-security-policy']
    assert client.get('/api/does-not-exist').status_code == 404
    assert client.post('/api/auth/login', content=b'x'*65537,headers={'content-type':'application/json'}).status_code == 413


def test_configured_but_offline_updater_has_actionable_response(config):
    root = config.state.parent/'installation'
    configured = Config(config.db, config.state, root, config.password_hash)
    with TestClient(create_app(configured),base_url='http://localhost') as client:
        result = client.get('/api/updates/status')
        assert result.status_code == 503
        assert 'updater' in result.json()['detail'].lower()
        headers = administrator(client)
        result = client.post('/api/updates/check',headers=headers)
        assert result.status_code == 503
        assert result.json()['detail']


def test_updater_bridge_is_fixed_action_and_handles_busy(config,monkeypatch):
    from backend.updater import UpdateError
    calls = []
    def fake_request(root, action):
        calls.append((root,action))
        if action == 'install':
            raise UpdateError('Another update operation is already running')
        return {'installed_version':'1.0.0','status':'checking'}
    monkeypatch.setattr('backend.updater_service.request',fake_request)
    root = config.state.parent/'installation'
    configured = Config(config.db, config.state, root, config.password_hash)
    with TestClient(create_app(configured),base_url='http://localhost') as client:
        headers = administrator(client)
        assert client.post('/api/updates/check',headers=headers).status_code == 200
        assert client.post('/api/updates/install',headers=headers).status_code in (409,503)
        assert client.post('/api/updates/run-shell',headers=headers).status_code == 404
    assert calls == [(root,'check'),(root,'install')]
