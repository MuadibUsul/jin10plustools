"""Serialize first publication of a team's immutable URL-addressed asset."""
from contextlib import contextmanager
import hashlib
from threading import RLock
from sqlalchemy import text

_locks=[RLock() for _ in range(64)]


@contextmanager
def upload_lock(db,team_id,asset_id):
    key=int.from_bytes(hashlib.sha256(f'asset:{team_id}:{asset_id}'.encode()).digest()[:8],signed=True)
    with _locks[key%len(_locks)]:
        if db.get_bind().dialect.name=='postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
        yield
