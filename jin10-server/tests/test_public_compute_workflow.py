"""The editor action, persisted facts and rule product use the same visible data."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.briefs import ai_compute, worker
from app.briefs.ai_compute_import import import_workbooks
from app.briefs.public_compute_store import store_visible
from app.briefs.service import enqueue
from app.db.models import AIComputeObservation, BriefJob

DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'ai-compute' / 'sources'


def capture(category):
    card = lambda metric, price, change, direction: {
        'metric': metric, 'date': '2026-10-20' if category == 'token_cost' else '2026-10-21',
        'price': price, 'week_change_pct': change, 'week_direction': direction,
        'precision': 2, 'source_url': 'https://www.silicondata.com/products/silicon-index/' + metric,
        'source_status': 'public_summary'}
    readings = ([card('token', '0.96', '-4.1', 'down')] if category == 'token_cost' else [
        card('a100', '1.55', '-0.6', 'down'), card('h100', '2.86', '10.0', 'up'),
        card('b200', '6.14', '6.6', 'up')])
    return {'category': category, 'data_date': readings[0]['date'],
            'captured_at': '2026-10-22T00:00:00+00:00', 'readings': readings}


def test_scope_is_only_editor_generate_for_two_compute_briefs():
    def wants(category, action='produce', user=1):
        return worker.public_read_requested(SimpleNamespace(
            category=category, action=action, created_by=user))
    assert wants('token_cost') and wants('gpu_rental')
    assert not wants('valuation')
    assert not wants('token_cost', 'render')
    assert not wants('gpu_rental', 'ai')
    assert not wants('gpu_rental', user=None)


def test_click_capture_persists_visible_precision_and_generates_both_briefs(
        db_factory, seed, monkeypatch, tmp_path):
    monkeypatch.setattr(worker, 'datetime',
                        SimpleNamespace(now=lambda zone: datetime(2026, 10, 22, tzinfo=zone)))
    team_id = seed['editor'].team_id
    with db_factory() as db:
        import_workbooks(db, team_id, DOCS/'【定时定点】Token支出价格指数.xlsx',
                         DOCS/'【定时定点】AI芯片租赁价格指数.xlsx')
        jobs = [enqueue(db, team_id, date(2026, 10, 22), category,
                        user_id=seed['editor'].id, unique=True)
                for category in ('token_cost', 'gpu_rental')]
        db.commit()
        ids = {job.category: job.id for job in jobs}
    calls = []
    def mocked_fetch(category, **kwargs):
        calls.append(category)
        return capture(category)
    monkeypatch.setattr('app.briefs.public_compute.fetch_visible', mocked_fetch)
    for category in ('token_cost', 'gpu_rental'):
        path = tmp_path/category
        path.mkdir()
        worker.capture_public_for_job(db_factory, ids[category], path)
        assert (path/'public-compute-read.json').is_file()
    assert calls == ['token_cost', 'gpu_rental']
    with db_factory() as db:
        token = db.scalar(select(AIComputeObservation).where(
            AIComputeObservation.team_id == team_id, AIComputeObservation.kind == 'token')
            .order_by(AIComputeObservation.date.desc()).limit(1))
        gpu = db.scalar(select(AIComputeObservation).where(
            AIComputeObservation.team_id == team_id, AIComputeObservation.kind == 'gpu')
            .order_by(AIComputeObservation.date.desc()).limit(1))
        assert (token.date, token.token_price, token.public_display['readings'][0]['precision']) == (
            date(2026, 10, 20), Decimal('0.96'), 2)
        assert (gpu.date, gpu.a100, gpu.h100, gpu.b200) == (
            date(2026, 10, 21), Decimal('1.55'), Decimal('2.86'), Decimal('6.14'))
        assert not db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == seed['outsider'].team_id)).all()

    def rows(kind):
        with db_factory() as db:
            found = db.scalars(select(AIComputeObservation).where(
                AIComputeObservation.team_id == team_id, AIComputeObservation.kind == kind)
                .order_by(AIComputeObservation.date)).all()
            return [{'date': row.date.isoformat(), 'token_price': row.token_price,
                     'a100': row.a100, 'h100': row.h100, 'b200': row.b200,
                     'status': row.source_status, 'public_display': row.public_display}
                    for row in found]
    monkeypatch.setattr(ai_compute, '_rows', rows)
    monkeypatch.setenv('MANUAL_GENERATION', '1')
    token = ai_compute.token_cost('2026-10-21', tmp_path/'token_cost', None)
    gpu = ai_compute.gpu_rental('2026-10-21', tmp_path/'gpu_rental', None)
    assert '0.96美元/百万Token' in token['dynamic_body']
    assert '0.9600' not in token['dynamic_body']
    assert '下跌4.1%' in token['dynamic_body']
    assert token['facts']['source_precision'] == 2
    assert '1.55' in gpu['dynamic_body'] and '2.86' in gpu['dynamic_body'] and '6.14' in gpu['dynamic_body']
    assert 'A100官网展示的七日变化为下跌0.6%' in gpu['dynamic_body']
    assert 'B200官网展示的七日变化为上涨6.6%' in gpu['dynamic_body']
    assert '已收录数据新高' in gpu['dynamic_body']
    assert (tmp_path/'token_cost'/token['images'][0]).is_file()
    assert (tmp_path/'gpu_rental'/gpu['images'][0]).is_file()


def test_fetch_failure_stops_generation_instead_of_reusing_old_data(db_factory, seed, monkeypatch):
    with db_factory() as db:
        job = enqueue(db, seed['editor'].team_id, date(2026, 10, 22), 'token_cost',
                      user_id=seed['editor'].id, unique=True)
        job.status = 'running'
        db.commit()
        job_id = job.id
    monkeypatch.setattr('app.briefs.public_compute.fetch_visible',
                        lambda *a, **k: (_ for _ in ()).throw(ValueError('HTTP 429')))
    monkeypatch.setattr('app.briefs.worker.subprocess.Popen',
                        lambda *a, **k: pytest.fail('fetch failure must stop before subprocess'))
    worker.execute(db_factory, job_id)
    with db_factory() as db:
        job = db.get(BriefJob, job_id)
        assert job.status == 'failed' and job.active_key is None
        assert '429' in job.error


def test_matching_manual_price_retains_provenance_and_uses_visible_week(db_factory, seed):
    team_id = seed['editor'].team_id
    visible = capture('token_cost')
    with db_factory() as db:
        existing = AIComputeObservation(
            team_id=team_id, kind='token', date=date(2026, 10, 20),
            token_price=Decimal('0.9600'), source='editor workbook',
            source_status='manual_verified', revision=1)
        db.add(existing)
        db.flush()
        result = store_visible(db, team_id, visible)
        db.commit()
        assert result['status'] == 'preserved_existing'
        assert existing.token_price == Decimal('0.9600')
        assert existing.source_status == 'manual_verified'
        assert existing.public_display['readings'][0]['week_change_pct'] == '-4.1'


def test_stale_public_page_stops_before_storage(db_factory, seed, monkeypatch, tmp_path):
    with db_factory() as db:
        job = enqueue(db, seed['editor'].team_id, date(2026, 10, 22), 'token_cost',
                      user_id=seed['editor'].id, unique=True)
        db.commit()
        job_id = job.id
    old = capture('token_cost')
    old['data_date'] = '2026-10-01'
    old['readings'][0]['date'] = '2026-10-01'
    monkeypatch.setattr('app.briefs.public_compute.fetch_visible', lambda *a, **k: old)
    monkeypatch.setattr('app.briefs.worker.datetime',
                        SimpleNamespace(now=lambda zone: datetime(2026, 10, 22, tzinfo=zone)))
    with pytest.raises(ValueError, match='超过七日'):
        worker.capture_public_for_job(db_factory, job_id, tmp_path)
    with db_factory() as db:
        assert not db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == seed['editor'].team_id)).all()
