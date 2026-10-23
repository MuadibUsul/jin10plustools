"""The new export must include observations archived before the deployment."""
import hashlib
import json
import sqlite3
import uuid

from alembic import command
from alembic.config import Config

from app.config import get_settings


def test_migration_backfills_latest_archived_erp_series(tmp_path, monkeypatch):
    database = tmp_path / 'valuation.sqlite'
    monkeypatch.setenv('PLUS_DATABASE_URL', f'sqlite:///{database.as_posix()}')
    get_settings.cache_clear()
    config = Config('alembic.ini')
    command.upgrade(config, '0012')
    team_id, job_id = uuid.uuid4(), uuid.uuid4()
    source = json.dumps([{'date': '2026-10-20', 'close': '4500',
                          'pe_ttm_mcw': '10', 'bond_yield_number': '2',
                          'source_status': 'verified'}]).encode()
    with sqlite3.connect(database) as db:
        db.execute('''INSERT INTO brief_source_snapshots
            (id, team_id, job_id, attempt, category, data_date, relative_path,
             sha256, byte_count, content, captured_at)
            VALUES (?, ?, ?, 1, 'valuation', '2026-10-20', 'erp-plot-data.json',
                    ?, ?, ?, '2026-10-20 10:00:00')''',
                   (uuid.uuid4().hex, team_id.hex, job_id.hex,
                    hashlib.sha256(source).hexdigest(), len(source), source))
    command.upgrade(config, '0013')
    with sqlite3.connect(database) as db:
        row = db.execute('SELECT date, close, pe_ttm_mcw, bond_yield_number, erp '
                         'FROM valuation_observations WHERE team_id = ?', (team_id.hex,)).fetchone()
    assert row == ('2026-10-20', 4500, 10, 2, 0.08)
    get_settings.cache_clear()
