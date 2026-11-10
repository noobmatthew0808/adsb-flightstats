"""Small atomic settings file; contains no aircraft observations or credentials."""
import json
import os
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator

class Service(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    label: str = Field(min_length=1, max_length=60)
    description: str = Field(default='', max_length=180)
    url: str = Field(default='', max_length=1024)
    icon: str = Field(default='link', max_length=40)

    @field_validator('url')
    @classmethod
    def safe_url(cls, value):
        if value:
            url = urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError('Use an http or https URL without credentials.')
        return value

class Settings(BaseModel):
    services: list[Service] = Field(max_length=40)
    theme: Literal['dark', 'light', 'system'] = 'dark'
    refresh_seconds: int = Field(default=60, ge=15, le=3600)
    session_gap_minutes: int = Field(default=30, ge=5, le=240)

    @field_validator('services')
    @classmethod
    def unique_ids(cls, value):
        if len({s.id for s in value}) != len(value):
            raise ValueError('Service identifiers must be unique.')
        return value

DEFAULT = Settings(services=[
    Service(id='aerodrome',label='Aerodrome',description='Aircraft history & tracking',url='http://192.168.1.187:8000',icon='plane'),
    Service(id='skystats',label='SkyStats',description='Your receiver, in numbers',url='http://192.168.1.187:5173',icon='chart'),
    Service(id='map',label='Aircraft map',description='Explore your local airspace',url='http://192.168.1.187:1099',icon='map'),
    Service(id='graphs',label='Receiver graphs',description='Signal quality & performance',url='http://192.168.1.187:1094',icon='activity'),
    Service(id='adsbim',label='ADSB.im',description='Receiver control centre',url='http://192.168.1.187',icon='radio'),
    Service(id='dozzle',label='Dozzle',description='Add your container log URL in Settings',url='',icon='terminal'),
])

class SettingsStore:
    def __init__(self, state: Path):
        self.path = state / 'settings.json'
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if self.path.exists():
                return Settings.model_validate_json(self.path.read_text())
            return DEFAULT.model_copy(deep=True)

    def save(self, settings: Settings):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(settings.model_dump_json(indent=2))
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.path)
        return settings
