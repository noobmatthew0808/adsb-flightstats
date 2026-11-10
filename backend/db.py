"""Short-lived, strictly read-only access to the existing Aerodrome database."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import threading
from typing import Iterator


class DatabaseError(RuntimeError):
    """An actionable source error, safe to display to the owner."""


# Explicit application tables. No arbitrary SQL or filesystem path enters a query.
TABLES = {
    'all_sightings', 'sightings_hourly', 'seen_aircraft', 'aircraft_track_daily',
    'concurrent_minute', 'military_sightings', 'military_hourly',
    'watchlist_sightings', 'watchlist_hourly', 'flight_routes', 'route_cache',
    'hexdb_cache', 'hexdb_events', 'photo_cache', 'stats_records',
    'schema_version', '_aerodrome_meta',
}


class Database:
    def __init__(self, path: str | Path, timeout: float = 8.0):
        self.path = Path(path).expanduser().resolve()
        self.timeout = min(max(float(timeout), 0.05), 30.0)
        self._slots = threading.BoundedSemaphore(2)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.is_file():
            raise DatabaseError('Aerodrome database was not found. Check AIRSPACE_DB; demo mode is never enabled automatically.')
        if not self._slots.acquire(timeout=self.timeout):
            raise DatabaseError('Two read-only history queries are already running. Wait briefly and retry.')
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                         timeout=1.0, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA busy_timeout=1000')
            deadline = time.monotonic() + self.timeout
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
            yield connection
        except sqlite3.OperationalError as error:
            if 'interrupted' in str(error):
                raise DatabaseError('This query exceeded the read-only time budget. Select a shorter period or coarser grouping.') from error
            if 'unable to open' in str(error) or 'readonly' in str(error):
                raise DatabaseError('Aerodrome database cannot be read. Check read permissions for the analytics service, including WAL/SHM access; do not change receiver ownership.') from error
            raise DatabaseError('Aerodrome query failed: ' + str(error)) from error
        except sqlite3.DatabaseError as error:
            raise DatabaseError('Aerodrome database error: ' + str(error)) from error
        finally:
            if connection is not None:
                connection.close()
            self._slots.release()

    @staticmethod
    def available(connection: sqlite3.Connection) -> set[str]:
        return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}

    @staticmethod
    def columns(connection: sqlite3.Connection, table: str) -> list[dict]:
        if table not in TABLES:
            raise ValueError('That table is not available in the read-only browser.')
        return [{'name': row['name'], 'type': row['type']} for row in connection.execute(f'PRAGMA table_info("{table}")')]
