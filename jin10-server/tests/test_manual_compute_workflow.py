"""Manual replacement uses the same observation, chart and brief pipeline."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.briefs import ai_compute, worker
from app.briefs.ai_compute_import import import_workbooks
from app.briefs.manual_compute import normalize, store_manual
from app.briefs.public_compute_store import store_visible
from app.db.models import AIComputeObservation, BriefJob
from tests.conftest import login


def entered(category, day, editor, *, token='0.9734', gpu=('1.52', '2.811', '6.01')):
    prices = {'token': token} if category == 'token_cost' else dict(zip(('a100', 'h100', 'b200'), gpu))
    changes = {'token': '-1.82'} if category == 'token_cost' else {
        'a100': '-0.65', 'h100': '0.36', 'b200': '2.21'}
    return normalize(category, day, day, day, prices, changes, '编辑核对的原始来源', editor.id)


def test_manual_api_is_editor_only_and_job_never_requests_public_page(client, db_factory, seed, monkeypatch, tmp_path):
    today = datetime.now().date()
    payload = {'category': 'token_cost', 'issue_date': today.isoformat(),
               'data_date': today.isoformat(), 'token_price': '0.9734',
               'token_change_pct': '-1.82', 'source_note': '编辑核对的原始来源'}
    assert client.post('/api/briefs/manual-compute', json=payload,
                       headers=login(client, 'viewer@plus.test')).status_code == 403
    response = client.post('/api/briefs/manual-compute', json=payload,
                           headers=login(client, 'editor@plus.test'))
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()['id'])
    with db_factory() as db:
        job = db.get(BriefJob, job_id)
        assert job.manual_capture['readings'][0]['price'] == '0.9734'
        assert not worker.public_read_requested(job)
    monkeypatch.setattr('app.briefs.public_compute.fetch_visible',
                        lambda *a, **k: pytest.fail('manual replacement must not fetch website'))
    result = worker.capture_manual_for_job(db_factory, job_id, tmp_path)
    assert result['data_date'] == today.isoformat()
    assert (tmp_path/'manual-compute-read.json').is_file()
    with db_factory() as db:
        rows = db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == seed['editor'].team_id,
            AIComputeObservation.kind == 'token')).all()
        assert len(rows) == 1
        assert rows[0].token_price == Decimal('0.9734')
        assert rows[0].manual_display['readings'][0]['week_change_pct'] == '-1.82'
        assert rows[0].public_display is None


def test_same_day_manual_replacement_updates_one_row_and_keeps_precision(db_factory, seed, monkeypatch, tmp_path):
    team_id = seed['editor'].team_id
    day = date(2026, 10, 22)
    with db_factory() as db:
        first = entered('token_cost', day, seed['editor'])
        assert store_manual(db, team_id, first)['status'] == 'inserted'
        same = store_manual(db, team_id, first)
        assert same == {'status': 'unchanged', 'revision': 1}
        second = entered('token_cost', day, seed['editor'], token='0.973456')
        assert store_manual(db, team_id, second)['status'] == 'updated_same_day'
        db.commit()
        rows = db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == team_id,
            AIComputeObservation.kind == 'token', AIComputeObservation.date == day)).all()
        assert len(rows) == 1 and rows[0].revision == 2
        assert rows[0].token_price == Decimal('0.973456')

    def rows(kind):
        with db_factory() as db:
            return [{'date': row.date.isoformat(), 'token_price': row.token_price,
                     'status': row.source_status, 'public_display': row.public_display,
                     'manual_display': row.manual_display}
                    for row in db.scalars(select(AIComputeObservation).where(
                        AIComputeObservation.team_id == team_id,
                        AIComputeObservation.kind == kind).order_by(AIComputeObservation.date))]
    monkeypatch.setattr(ai_compute, '_rows', rows)
    monkeypatch.setenv('MANUAL_GENERATION', '1')
    result = ai_compute.token_cost(day.isoformat(), tmp_path, None)
    assert '0.973456美元/百万Token' in result['dynamic_body']
    assert '下跌1.82%' in result['dynamic_body']
    assert result['facts']['source_precision'] == 6
    assert (tmp_path/result['images'][0]).is_file()


def test_gpu_manual_three_prices_share_one_row_and_partial_week_rejected(db_factory, seed, monkeypatch, tmp_path):
    day = date(2026, 10, 22)
    with pytest.raises(ValueError, match='全部填写'):
        normalize('gpu_rental', day, day, day,
                  {'a100': '1.52', 'h100': '2.811', 'b200': '6.01'},
                  {'a100': '1.0'}, '', seed['editor'].id)
    team_id = seed['editor'].team_id
    with db_factory() as db:
        docs = Path(__file__).resolve().parents[2] / 'docs' / 'ai-compute' / 'sources'
        import_workbooks(db, team_id, docs/'【定时定点】Token支出价格指数.xlsx',
                         docs/'【定时定点】AI芯片租赁价格指数.xlsx')
        store_manual(db, team_id, entered('gpu_rental', day, seed['editor']))
        db.commit()
        rows = db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == team_id,
            AIComputeObservation.kind == 'gpu', AIComputeObservation.date == day)).all()
        assert len(rows) == 1
        assert (rows[0].a100, rows[0].h100, rows[0].b200) == (
            Decimal('1.52'), Decimal('2.811'), Decimal('6.01'))
    def rows(kind):
        with db_factory() as db:
            return [{'date': row.date.isoformat(), 'a100': row.a100, 'h100': row.h100,
                     'b200': row.b200, 'status': row.source_status,
                     'public_display': row.public_display, 'manual_display': row.manual_display}
                    for row in db.scalars(select(AIComputeObservation).where(
                        AIComputeObservation.team_id == team_id,
                        AIComputeObservation.kind == kind).order_by(AIComputeObservation.date))]
    monkeypatch.setattr(ai_compute, '_rows', rows)
    monkeypatch.setenv('MANUAL_GENERATION', '1')
    result = ai_compute.gpu_rental(day.isoformat(), tmp_path, None)
    assert 'H100报2.811美元' in result['dynamic_body']
    assert 'B200人工录入的七日变化为上涨2.21%' in result['dynamic_body']
    assert (tmp_path/result['images'][0]).is_file()


def test_manual_entry_replaces_public_values_on_same_day(db_factory, seed):
    team_id = seed['editor'].team_id
    day = date(2026, 10, 22)
    with db_factory() as db:
        db.add(AIComputeObservation(team_id=team_id, kind='token', date=day,
                                    token_price=Decimal('0.96'), source='public',
                                    source_status='public_visible_summary', revision=1,
                                    public_display={'readings': [{'metric': 'token', 'date': day.isoformat(),
                                                                    'price': '0.96', 'week_change_pct': '-4.1'}]}))
        db.flush()
        store_manual(db, team_id, entered('token_cost', day, seed['editor']))
        db.commit()
        rows = db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == team_id,
            AIComputeObservation.kind == 'token', AIComputeObservation.date == day)).all()
        assert len(rows) == 1 and rows[0].revision == 2
        assert rows[0].token_price == Decimal('0.9734')
        assert rows[0].public_display is None
        assert rows[0].source_status == 'manual_entry'
        public = {'category': 'token_cost', 'data_date': day.isoformat(),
                  'captured_at': '2026-10-22T12:00:00Z',
                  'readings': [{'metric': 'token', 'date': day.isoformat(),
                                'price': '0.96', 'precision': 2,
                                'week_change_pct': '-4.1', 'source_url': 'https://www.silicondata.com'}]}
        assert store_visible(db, team_id, public)['status'] == 'preserved_manual'
        db.commit()
        assert rows[0].token_price == Decimal('0.9734')
        assert rows[0].public_display is None
