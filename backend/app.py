"""HTTP API and compiled frontend. The updater is a separate local process."""
import csv
import io
import ipaddress
import os
import socket
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import Config, ROOT, VERSION
from .security import COOKIE, Sessions, same_origin
from .settings import Settings, SettingsStore
from .db import Database, DatabaseError
from .analytics import Analytics

class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def csv_response(columns, rows, filename):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        values = [row.get(c) for c in columns] if isinstance(row, dict) else row
        # Spreadsheet formulas are never executable in downloaded text cells.
        writer.writerow([("'" + str(v)) if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
    return Response(output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':f'attachment; filename="{filename}"'})


def create_app(config: Config | None = None):
    config = config or Config.from_env()
    app = FastAPI(title='Airspace Analytics', version=VERSION, docs_url=None, redoc_url=None)
    app.state.config = config
    sessions, settings = Sessions(), SettingsStore(config.state)
    # Demo requires explicit selection. A missing real database never becomes fake data.
    if config.demo:
        from .demo import create_demo
        config.state.mkdir(parents=True, exist_ok=True)
        demo_db = config.state / 'demo.db'
        if not demo_db.exists():
            create_demo(demo_db)
        db_path = demo_db
    else:
        db_path = config.db
    analytics = Analytics(Database(db_path))
    app.state.analytics = analytics

    def params(request):
        p = dict(request.query_params)
        if 'session_gap' not in p:
            p['session_gap'] = settings.get().session_gap_minutes
        return p

    @app.middleware('http')
    async def protections(request: Request, call_next):
        host = request.url.hostname or ''
        allowed = {'localhost', socket.gethostname().lower(), *filter(None, os.environ.get('AIRSPACE_ALLOWED_HOSTS', '').lower().split(','))}
        try:
            addr = ipaddress.ip_address(host)
            valid = addr.is_private or addr.is_loopback or addr in ipaddress.ip_network('100.64.0.0/10')
        except ValueError:
            valid = host.lower() in allowed
        if not valid:
            return JSONResponse({'detail':'Unrecognised Host header.'}, status_code=400)
        if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 65536:
            return JSONResponse({'detail':'Request body too large.'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail':str(error)}, status_code=422)

    @app.exception_handler(DatabaseError)
    async def source_error(request, error):
        return JSONResponse({'detail':str(error)}, status_code=503)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        message = 'The read-only history query failed. Check the data source in Settings.'
        if 'interrupt' in str(error):
            message = 'Query exceeded its time budget. Choose a shorter period or narrower filters.'
        return JSONResponse({'detail':message}, status_code=503)

    @app.exception_handler(OSError)
    async def io_error(request, error):
        return JSONResponse({'detail':'A required local file or service is unavailable. Check Settings and the service logs.'},status_code=503)

    @app.get('/api/health')
    def health():
        return {'ok':True,'version':VERSION}

    @app.get('/api/status')
    def status():
        return {'version':VERSION,'demo':config.demo,'source':analytics.status(),'timezone':'Europe/London','session_gap_minutes':settings.get().session_gap_minutes}

    @app.get('/api/analytics')
    def query(request: Request):
        return analytics.query(params(request))

    @app.get('/api/compare')
    def compare(request: Request):
        return analytics.compare(params(request))

    @app.get('/api/analytics/export')
    def export_analytics(request: Request):
        result = analytics.query(params(request))
        return csv_response(['label','start','end','value','count','coverage_pct','samples'],result['series'],'airspace-analysis.csv')

    @app.get('/api/data/tables')
    def tables():
        return analytics.tables()

    @app.get('/api/data/rows')
    def rows(request: Request):
        return analytics.rows(params(request))

    @app.get('/api/data/export')
    def export_data(request: Request):
        p = params(request)
        p['limit'] = 10000
        result = analytics.rows(p)
        return csv_response(result['columns'],result['rows'],'airspace-records.csv')

    @app.get('/api/aircraft/{icao}')
    def aircraft(icao: str, request: Request):
        p = params(request)
        return analytics.aircraft(icao, p.get('start'), p.get('end'))

    @app.get('/api/settings')
    def read_settings():
        return settings.get()

    @app.put('/api/settings')
    def write_settings(request: Request, data: Settings):
        sessions.require(request)
        return settings.save(data)

    @app.get('/api/auth/session')
    def session(request: Request):
        csrf = sessions.get(request)
        return {'authenticated':csrf is not None,'csrf_token':csrf}

    @app.post('/api/auth/login')
    def login(request: Request, data: Login):
        same_origin(request)
        token, csrf = sessions.login(request, data.password, config.password_hash)
        response = JSONResponse({'csrf_token':csrf,'authenticated':True})
        response.set_cookie(COOKIE,token,max_age=28800,httponly=True,secure=config.secure_cookie,samesite='strict',path='/')
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        sessions.require(request)
        sessions.logout(request)
        response = JSONResponse({'authenticated':False})
        response.delete_cookie(COOKIE)
        return response

    def update_request(action):
        if not config.install_root:
            if action == 'status':
                return {'installed_version':VERSION,'available_version':None,'status':'idle','progress':0,'message':'Updates become available after installation as a service.','history':[],'release_notes':'','configured':False,'can_rollback':False}
            raise HTTPException(409,'The local updater is not configured. Use the installer first.')
        from .updater_service import request as updater_request
        from .updater import UpdateError
        try:
            return updater_request(config.install_root, action)
        except (OSError, TimeoutError, ConnectionError, UpdateError) as error:
            raise HTTPException(503,'The local updater is unavailable. Check the airspace-analytics-updater service.') from error

    @app.get('/api/updates/status')
    def update_status():
        return update_request('status')

    @app.post('/api/updates/{action}')
    def update_action(action: str, request: Request):
        sessions.require(request)
        if action not in {'check','install','rollback'}:
            raise HTTPException(404,'Unknown update action.')
        return update_request(action)

    dist = ROOT / 'frontend' / 'dist'
    if (dist / 'assets').is_dir():
        app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404,'Unknown API endpoint.')
        if path in {'favicon.svg','manifest.webmanifest','icon.svg'} and (dist/path).is_file():
            return FileResponse(dist/path)
        if not (dist/'index.html').is_file():
            raise HTTPException(503,'Frontend has not been built. Run npm ci and npm run build in frontend/.')
        return FileResponse(dist/'index.html',headers={'Cache-Control':'no-cache'})

    return app

app = create_app()
