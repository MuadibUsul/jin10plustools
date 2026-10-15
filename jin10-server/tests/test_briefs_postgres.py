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
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.briefs.service import enqueue
from app.briefs.worker import claim
from app.config import get_settings
from app.db.models import BriefIssue, BriefJob, Team


@pytest.fixture
def postgres(monkeypatch):
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
        command.upgrade(Config('alembic.ini'), 'head')
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


def test_migration_preserves_existing_daily_cover(postgres):
    engine, _, _ = postgres
    assert 'cover_ids' in {column['name'] for column in inspect(engine).get_columns('boards')}
    command.downgrade(Config('alembic.ini'), '0007')
    assert 'brief_jobs' not in inspect(engine).get_table_names()
    assert 'cover_ids' in {column['name'] for column in inspect(engine).get_columns('boards')}
    command.upgrade(Config('alembic.ini'), 'head')
    assert {'brief_configs', 'brief_issues', 'brief_versions', 'brief_jobs', 'brief_audits'} <= set(inspect(engine).get_table_names())


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
