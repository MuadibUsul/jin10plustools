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


def test_database_disconnect_is_fenced_and_reported(postgres,tmp_path):
    from threading import Event
    from app.briefs.worker_lock import WorkerLock
    engine,_,_=postgres;lost=Event()
    with WorkerLock(engine,tmp_path/'worker.lock',lost.set,interval=.05) as guard:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text('SELECT pg_terminate_backend(:pid)'),{'pid':guard.backend_pid})
        assert lost.wait(5)
        with pytest.raises(SystemExit,match='lock was lost'):guard.check()
        with pytest.raises(SystemExit,match='storage volume'):
            with WorkerLock(engine,tmp_path/'worker.lock',lambda:None):pass
    with WorkerLock(engine,tmp_path/'worker.lock',lambda:None,interval=.05) as new:
        new.check()


@pytest.mark.parametrize('postgres',['0019'],indirect=True)
def test_auth_migration_preserves_accounts_and_existing_session_version(postgres):
    engine,_,team_id=postgres
    users=Table('users',MetaData(),autoload_with=engine);ident=uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(users.insert().values(id=ident,team_id=team_id,email='migration@example.test',name='migration',
            password_hash='fixture-existing-hash',role='editor',is_active=True,created_at=datetime.now(timezone.utc)))
    command.upgrade(Config('alembic.ini'),'head')
    users=Table('users',MetaData(),autoload_with=engine)
    with engine.connect() as connection:
        row=connection.execute(select(users).where(users.c.id==ident)).mappings().one()
        assert row['auth_version']==0 and row['password_hash']=='fixture-existing-hash'


@pytest.mark.parametrize('postgres', ['0020'], indirect=True)
def test_feedback_migration_preserves_pending_items_and_serializes_claims(postgres):
    from app.api.feedback_mcp import call_tool
    from app.db.models import Feedback

    engine, factory, team_id = postgres
    ident = uuid.uuid4()
    old_feedback = Table('feedback', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(old_feedback.insert().values(
            id=ident, team_id=team_id, name='原提交人', title='已有反馈', content='待处理的反馈',
            image_bytes=0, created_at=datetime.now(timezone.utc),
        ))
    command.upgrade(Config('alembic.ini'), 'head')
    with factory() as db:
        assert db.get(Feedback, ident).claim_id is None
        assert call_tool(db, team_id, 'feedback_list_pending', {})['items'][0]['status'] == 'pending'

    barrier = Barrier(2)

    def claim_feedback(_):
        with factory() as db:
            barrier.wait()
            try:
                return call_tool(db, team_id, 'feedback_claim', {'feedback_id': str(ident)})
            except ValueError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [item for item in pool.map(claim_feedback, range(2)) if item is not None]
    assert len(claims) == 1 and claims[0]['title'] == '已有反馈'


def test_concurrent_asset_upload_uses_database_lock_across_request_threads(postgres,tmp_path,monkeypatch):
    import hashlib,io
    from contextlib import nullcontext
    from fastapi.testclient import TestClient
    from PIL import Image
    from app.api import assets
    from app.db.models import Asset,User
    from app.db.session import get_db
    from app.main import app
    from app.security import create_access_token
    from app.services import asset_upload
    from app.services.storage import asset_path
    _,factory,team_id=postgres
    monkeypatch.setattr(get_settings(),'storage_dir',tmp_path/'storage')
    with factory() as db:
        user=User(team_id=team_id,email='asset-lock@example.test',name='fixture',password_hash='fixture-hash',role='editor')
        db.add(user);db.commit();user_id=user.id
    def session():
        with factory() as db:
            yield db
            db.commit()
    previous=dict(app.dependency_overrides);app.dependency_overrides[get_db]=session
    # Remove only the in-process optimization: the real PG lock must do the fencing.
    monkeypatch.setattr(asset_upload,'_locks',[nullcontext() for _ in range(64)])
    barrier=Barrier(2);lock=assets.upload_lock
    from contextlib import contextmanager
    @contextmanager
    def synchronize(*args):
        barrier.wait(10)
        with lock(*args):yield
    monkeypatch.setattr(assets,'upload_lock',synchronize)
    url='https://example.test/concurrent-source.png';aid=assets.expected_asset_id(url)
    headers={'Authorization':'Bearer '+create_access_token(user_id,team_id)[0],
             'Content-Type':'image/png','X-Source-Url':url}
    def upload(color):
        buffer=io.BytesIO();Image.new('RGB',(3,3),color).save(buffer,format='PNG')
        return TestClient(app).put('/api/assets/'+aid,headers=headers,content=buffer.getvalue()).status_code
    try:
        with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(upload,['red','blue']))==[200,201]
        with factory() as db:
            asset=db.get(Asset,(team_id,aid))
            assert hashlib.sha256(asset_path(team_id,aid).read_bytes()).hexdigest()==asset.sha256
    finally:
        app.dependency_overrides.clear();app.dependency_overrides.update(previous)
