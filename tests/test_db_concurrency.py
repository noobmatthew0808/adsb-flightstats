"""A normal burst waits for a slot, while the source stays bounded to two readers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import sqlite3
import threading
import pytest
from backend.db import Database, DatabaseError


def test_reader_burst_waits_for_available_slot(tmp_path):
    path = tmp_path / 'history.db'
    sqlite3.connect(path).close()
    db = Database(path, timeout=1)
    started = threading.Event()
    def third():
        started.set()
        with db.connect() as connection:
            return connection.execute('SELECT 1').fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with ExitStack() as stack:
            stack.enter_context(db.connect())
            stack.enter_context(db.connect())
            future = pool.submit(third)
            assert started.wait(1)
            assert not future.done()
        assert future.result(timeout=2) == 1


def test_reader_wait_is_bounded(tmp_path):
    path = tmp_path / 'history.db'
    sqlite3.connect(path).close()
    db = Database(path, timeout=0.05)
    with db.connect(), db.connect(), pytest.raises(DatabaseError, match='already running'):
        with db.connect():
            pytest.fail('A third simultaneous source reader was allowed')
