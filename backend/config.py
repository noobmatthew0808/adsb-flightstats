"""Runtime settings. Aerodrome settings are never opened or modified."""
from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()

@dataclass(frozen=True)
class Config:
    db: Path
    state: Path
    install_root: Path | None
    password_hash: str
    demo: bool = False
    secure_cookie: bool = False

    @classmethod
    def from_env(cls):
        state = Path(os.environ.get('AIRSPACE_STATE_DIR', str(Path.home() / '.local/state/airspace-analytics'))).expanduser()
        return cls(
            db=Path(os.environ.get('AIRSPACE_DB', '/opt/aerodrome/aircraft_history.db')).expanduser(),
            state=state,
            install_root=Path(os.environ['AIRSPACE_INSTALL_ROOT']).expanduser() if os.environ.get('AIRSPACE_INSTALL_ROOT') else None,
            password_hash=os.environ.get('AIRSPACE_ADMIN_PASSWORD_HASH', ''),
            demo=os.environ.get('AIRSPACE_DEMO') == '1',
            secure_cookie=os.environ.get('AIRSPACE_SECURE_COOKIE') == '1',
        )
