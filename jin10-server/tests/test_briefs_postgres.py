"""Real PostgreSQL checks in a disposable schema; opt in with PLUS_TEST_POSTGRES_URL."""
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text,Table,MetaData
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.briefs.service import enqueue
from app.briefs.worker import claim
from app.config import get_settings
from app.db.models import BriefIssue, BriefJob, Team


@pytest.fixture
def postgres(monkeypatch, request):
    url = os.getenv('PLUS_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set PLUS_TEST_POSTGRES_URL to an isolated test database')
    admin = create_engine(url)
    schema = 'brief_test_' + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    scoped_url = make_url(url).update_query_dict({'options': f'-csearch_path={schema}'})
    database_url = scoped_url.render_as_string(hide_password=False)
    monkeypatch.setenv('PLUS_DATABASE_URL', database_url)
    get_settings.cache_clear()
    engine = create_engine(scoped_url)
    try:
        command.upgrade(Config('alembic.ini'), getattr(request, 'param', 'head'))
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as db:
            team = Team(name='Isolated PostgreSQL verification')
            db.add(team)
            db.commit()
            team_id = team.id
        yield engine, factory, team_id
    finally:
        engine.dispose()
        get_settings.cache_clear()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


@pytest.mark.parametrize('postgres', ['0014'], indirect=True)
def test_history_migration_preserves_existing_values_without_secrets(postgres):
    from app.db.models import AIComputeObservation, AIComputeRevision, TeamBusinessRevision
    engine, factory, team_id = postgres
    with factory() as db:
        team = db.get(Team, team_id)
        team.settings = {'brief_templates': {'volume': {'template': '{date}'}},
                         'ai': {'deepseek': {'model': 'deepseek-chat', 'key': 'secret-do-not-export'}}}
        # Insert the actual pre-upgrade schema, not a current ORM with columns
        # which have deliberately not been migrated yet.
        observations=Table('ai_compute_observations',MetaData(),autoload_with=db.connection())
        stamp=datetime.now(timezone.utc)
        db.execute(observations.insert().values(id=uuid.uuid4(),team_id=team_id,kind='token',date=date(2026,10,20),
            token_price='0.125000',source='Silicon Data',revision=3,created_at=stamp,updated_at=stamp))
        db.commit()
    command.upgrade(Config('alembic.ini'), 'head')
    with factory() as db:
        row = db.scalar(select(AIComputeRevision))
        assert row.revision == 3 and row.values['token_price'] == '0.125000'
        history = db.scalar(select(TeamBusinessRevision))
        assert history.snapshot['brief_templates']['volume']['template'] == '{date}'
        assert 'secret-do-not-export' not in str(history.snapshot)
    assert 'team_business_revisions' in inspect(engine).get_table_names()


def test_migration_preserves_existing_daily_cover(postgres):
    engine, _, _ = postgres
    assert 'cover_ids' in {column['name'] for column in inspect(engine).get_columns('boards')}
    # Business-data migrations are intentionally additive and retain data on downgrade.
    assert {'brief_configs', 'brief_issues', 'brief_versions', 'brief_jobs', 'brief_audits',
            'brief_source_snapshots'} <= set(inspect(engine).get_table_names())
    assert 'notes' in {column['name'] for column in inspect(engine).get_columns('brief_configs')}


@pytest.mark.parametrize('postgres',['0018'],indirect=True)
def test_production_boundary_migration_preserves_legacy_evidence_and_retry(postgres):
    import hashlib
    from app.db.models import BriefSourceSnapshot
    from app.briefs.evidence_compaction import compact
    engine,factory,team_id=postgres;stamp=datetime.now(timezone.utc);job_id=uuid.uuid4();snapshot_id=uuid.uuid4()
    raw=b'original evidence before migration'
    with engine.begin() as connection:
        jobs=Table('brief_jobs',MetaData(),autoload_with=connection)
        snapshots=Table('brief_source_snapshots',MetaData(),autoload_with=connection)
        connection.execute(jobs.insert().values(id=job_id,team_id=team_id,issue_date=date(2026,11,3),
            category='oil_etf',action='produce',preview=False,status='waiting',attempts=1,dedupe_key='before-0019',
            next_attempt_at=stamp,created_at=stamp,updated_at=stamp))
        connection.execute(snapshots.insert().values(id=snapshot_id,team_id=team_id,job_id=job_id,
            attempt=1,category='oil_etf',data_date=date(2026,11,2),relative_path='raw.html',
            sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw),content=raw,captured_at=stamp))
    command.upgrade(Config('alembic.ini'),'head')
    with factory() as db:
        assert db.get(BriefJob,job_id).schedule_origin is True
        row=db.get(BriefSourceSnapshot,snapshot_id);assert row.content==raw and row.blob_id is None
        compact(db,team_id);db.commit();db.expire_all()
        assert row.content==raw and row.blob_id is not None and row._content==b''


def test_concurrent_enqueue_creates_one_active_job(postgres):
    _, factory, team_id = postgres
    barrier = Barrier(4)

    def submit(_):
        with factory() as db:
            barrier.wait()
            job = enqueue(db, team_id, date(2026, 10, 15), 'volume', preview=True, unique=True)
            db.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(submit, range(4)))
    assert len(set(ids)) == 1
    with factory() as db:
        assert len(db.scalars(select(BriefIssue)).all()) == 1
        assert len(db.scalars(select(BriefJob)).all()) == 1


def test_concurrent_claim_and_expired_lease_recovery(postgres):
    _, factory, team_id = postgres
    now = datetime.now(timezone.utc)
    with factory() as db:
        for category in ('volume', 'oil_etf'):
            job = enqueue(db, team_id, date(2026, 10, 15), category, preview=True)
            job.next_attempt_at = now - timedelta(minutes=10)
        db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: claim(factory, now), range(2)))
    assert None not in ids and len(set(ids)) == 2
    with factory() as db:
        job = db.get(BriefJob, ids[0])
        job.lease_until = now - timedelta(seconds=1)
        db.commit()
    assert claim(factory, now) == ids[0]
    with factory() as db:
        assert db.get(BriefJob, ids[0]).attempts == 2


def test_second_worker_refuses_advisory_lock(postgres):
    engine, _, _ = postgres
    with engine.connect() as connection:
        assert connection.execute(text('SELECT pg_try_advisory_lock(71010501)')).scalar()
        result = subprocess.run([sys.executable, '-m', 'app.briefs.worker'],
                                capture_output=True, text=True, timeout=20)
        assert result.returncode != 0
        assert 'Another brief worker is active' in result.stderr
        connection.execute(text('SELECT pg_advisory_unlock(71010501)'))
