from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.briefs.policy import CHINA
from app.briefs.service import enqueue, root_for
from app.briefs.sources import save
from app.briefs.worker import collection_plans, schedule_collection, claim
from app.db.models import BriefConfig, BriefJob, BriefIssue, BriefDataPoint, BriefSourceSnapshot


def test_china_close_and_preopening_collection_are_separate_from_publication(db_factory, seed):
    team = seed['editor'].team_id
    now = datetime(2026, 11, 24, 16, 5, tzinfo=CHINA)
    with db_factory() as db:
        db.add(BriefConfig(team_id=team, enabled=False, data_enabled=True));db.commit()
    assert schedule_collection(db_factory, now) == 3
    assert schedule_collection(db_factory, now) == 0
    assert schedule_collection(db_factory, now.replace(minute=35)) == 0  # In-flight captures are reused.
    with db_factory() as db:
        jobs = list(db.scalars(select(BriefJob)))
        assert {job.category for job in jobs} == {'volume', 'turnover', 'valuation'}
        assert all(job.action == 'reconcile' and job.issue_date == date(2026, 11, 25) for job in jobs)
        assert db.scalar(select(BriefIssue)) is None
        for job in jobs:job.status = 'succeeded';job.active_key = None
        db.commit()
    assert schedule_collection(db_factory, now.replace(hour=17, minute=5)) == 0
    preopen = datetime(2026, 11, 25, 7, 15, tzinfo=CHINA)
    assert schedule_collection(db_factory, preopen) == 7
    assert schedule_collection(db_factory, preopen) == 0
    with db_factory() as db:
        assert not any(job.category in ('token_cost', 'gpu_rental') for job in db.scalars(select(BriefJob)))
        assert db.scalar(select(BriefIssue)) is None


def test_close_failure_gets_only_due_retry_slots(db_factory, seed):
    team = seed['editor'].team_id
    now = datetime(2026, 11, 24, 16, 5, tzinfo=CHINA)
    with db_factory() as db:
        db.add(BriefConfig(team_id=team, data_enabled=True));db.commit()
    assert schedule_collection(db_factory, now) == 3
    def finish():
        with db_factory() as db:
            for job in db.scalars(select(BriefJob).where(BriefJob.status == 'queued')):
                job.status = 'failed';job.active_key = None
            db.commit()
    finish()
    assert schedule_collection(db_factory, now.replace(minute=10)) == 0
    assert schedule_collection(db_factory, now.replace(minute=35)) == 3
    finish()
    assert schedule_collection(db_factory, now.replace(hour=17)) == 3
    finish()
    # CFETS has its own 17:15 close buffer and does not share the stock cutoff.
    plans = list(collection_plans(now.replace(hour=17, minute=20)))
    assert ('liquidity', date(2026, 11, 24), 'close-0') in plans


def test_us_close_collection_handles_dst_and_friday_after_midnight_in_china():
    # 16:15 New York is 04:15 China during DST, 05:15 after the DST change.
    for now, target in [
        (datetime(2026, 10, 31, 4, 20, tzinfo=CHINA), date(2026, 10, 30)),
        (datetime(2026, 11, 21, 5, 20, tzinfo=CHINA), date(2026, 11, 20)),
    ]:
        plans = list(collection_plans(now))
        assert {(category, day) for category, day, _ in plans} == {
            (category, target) for category in ('oil_etf', 'us_barometer', 'us_structure')}


def test_expired_lease_preserves_observations_before_reclaim(db_factory, seed):
    team = seed['editor'].team_id
    now = datetime(2026, 11, 24, 1, tzinfo=timezone.utc)
    with db_factory() as db:
        job = enqueue(db, team, date(2026, 11, 24), 'volume')
        job.status = 'running';job.attempts = 1;job.next_attempt_at = now - timedelta(minutes=1)
        job.lease_until = now - timedelta(seconds=1);db.commit();ident = job.id
    folder = root_for(team) / 'runs' / str(ident)
    save(folder / 'volume-collected.json', [{'date': '2026-11-23', 'turn': '15528.8017283684',
        'vol': '1119.564804', 'source_kind': 'eastmoney_daily_index'}])
    assert claim(db_factory, now) == ident
    with db_factory() as db:
        point = db.scalar(select(BriefDataPoint))
        assert point.values['turn'] == '15528.8017283684'
        assert point.source_snapshot_id is not None
        assert db.scalar(select(BriefSourceSnapshot)) is not None
        assert db.get(BriefJob, ident).attempts == 2
    # A second reclaim of the same interrupted evidence is idempotent.
    with db_factory() as db:
        job = db.get(BriefJob, ident);job.lease_until = now - timedelta(seconds=1);db.commit()
    assert claim(db_factory, now) == ident
    with db_factory() as db:assert len(list(db.scalars(select(BriefDataPoint)))) == 1


def test_afternoon_collection_failure_is_visible_before_next_morning(db_factory,seed):
    from app.services.production_health import production_status
    team=seed['editor'].team_id
    with db_factory() as db:
        db.add(BriefConfig(team_id=team,data_enabled=True))
        job=enqueue(db,team,date(2026,11,25),'volume',action='reconcile',cycle='collection-test')
        job.status='failed';job.active_key=None;job.error='行情 HTTP 503';db.commit()
        report=production_status(db,team,datetime(2026,11,24,18,tzinfo=CHINA))
        alert=next(row for row in report['alerts'] if row.get('job_id')==str(job.id))
        assert alert['target_date']=='2026-11-24'
