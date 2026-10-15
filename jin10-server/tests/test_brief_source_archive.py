"""Fetched evidence must survive in the database, including failed runs."""
import hashlib
from datetime import date

import pytest
from sqlalchemy import select

from app.briefs.source_archive import archive_run
from app.db.models import BriefJob, BriefSourceSnapshot


def test_raw_normalized_and_history_are_archived_exactly(db_factory, seed, tmp_path):
    folder = tmp_path / 'run'
    history = tmp_path / 'history'
    source = tmp_path / 'previous-run'
    (folder / 'raw').mkdir(parents=True)
    history.mkdir()
    source.mkdir()
    raw = b'{"data":[1,2,3]}'
    (folder / 'raw' / 'source.json').write_bytes(raw)
    (folder / 'normalized.csv').write_bytes(b'date,value\n2026-10-14,1\n')
    (history / 'daily.json').write_bytes(b'{"2026-10-14":1}')
    (source / 'original.json').write_bytes(b'{"original":true}')
    (folder / 'chart.png').write_bytes(b'not source data')
    with db_factory() as db:
        job = BriefJob(team_id=seed['editor'].team_id, issue_date=date(2026, 10, 15),
                       category='valuation', dedupe_key='archive-test', status='failed', attempts=1)
        db.add(job)
        db.flush()
        manifest = archive_run(db, job, folder, history_dir=history, source_dir=source)
        assert manifest['file_count'] == 4
        assert archive_run(db, job, folder, history_dir=history, source_dir=source)['file_count'] == 4
        db.commit()
        rows = {r.relative_path: r for r in db.scalars(select(BriefSourceSnapshot))}
        assert len(rows) == 4
        assert rows['raw/source.json'].content == raw
        assert rows['raw/source.json'].sha256 == hashlib.sha256(raw).hexdigest()
        assert rows['history/daily.json'].content == b'{"2026-10-14":1}'
        assert rows['origin/original.json'].content == b'{"original":true}'
        assert {r.data_date.isoformat() for r in rows.values()} == {'2026-10-14'}
        assert all(r.job_id == job.id and r.attempt == 1 for r in rows.values())
        (folder / 'raw' / 'source.json').write_bytes(b'changed')
        with pytest.raises(ValueError, match='发生变化'):
            archive_run(db, job, folder, history_dir=history, source_dir=source)
