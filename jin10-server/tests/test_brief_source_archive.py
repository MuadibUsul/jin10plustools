"""Fetched evidence must survive in the database, including failed runs."""
import hashlib
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.briefs.source_archive import archive_run, restore_pe_capture
from app.briefs.engines.valuation.fetch_free_pe import _completed_cache
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


def test_dated_pe_capture_can_be_restored_from_database(db_factory, seed, tmp_path):
    original = tmp_path / 'old-run'
    source = original / 'erp' / 'raw' / 'pe'
    source.mkdir(parents=True)
    from datetime import datetime, timezone
    raw = json.dumps({'result_code': 0, 'data': {'index_eva_pe_growths': [
        {'ts': int(datetime(2026, 10, 13, tzinfo=timezone.utc).timestamp()*1000), 'pe': '13.1697'},
        {'ts': int(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()*1000), 'pe': '13.0883'},
    ]}}).encode()
    raw_path = source / 'pe-original-history.json'
    raw_path.write_bytes(raw)
    normalized = {
        'source': 'snowball_fund_danjuan_public',
        'expected_date': '2026-10-14', 'previous_date_rechecked': '2026-10-13',
        'rows': [{'date': '2026-10-13', 'pe_ttm_mcw_raw': '13.1697'},
                 {'date': '2026-10-14', 'pe_ttm_mcw_raw': '13.0883'}],
        'raw_evidence': [{'file': str(raw_path), 'sha256': hashlib.sha256(raw).hexdigest()}],
    }
    (source / 'pe-original-normalized.json').write_text(json.dumps(normalized), encoding='utf-8')
    with db_factory() as db:
        old_job = BriefJob(team_id=seed['editor'].team_id, issue_date=date(2026, 10, 15),
                           category='valuation', dedupe_key='pe-old', attempts=1)
        new_job = BriefJob(team_id=seed['editor'].team_id, issue_date=date(2026, 10, 15),
                           category='valuation', dedupe_key='pe-new', attempts=1)
        db.add_all([old_job, new_job])
        db.flush()
        archive_run(db, old_job, original)
        db.commit()
        raw_path.unlink()
        target = tmp_path / 'new-run'
        assert restore_pe_capture(db, new_job, target, '2026-10-14')
        cached = _completed_cache(target / 'erp' / 'raw' / 'pe', '2026-10-14', '2026-10-13', False)
        assert cached['restored_from_database'] is True
        assert cached['completed_date_cache_reused'] is True
        assert cached['rows'][-1]['pe_ttm_mcw_raw'] == '13.0883'
