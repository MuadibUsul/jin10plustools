"""AI compute observations and the two new fixed-brief adapters."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.briefs import ai_compute
from app.briefs.ai_compute_import import import_workbooks
from app.db.models import AIComputeObservation

DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'ai-compute' / 'sources'


def test_import_token_and_gpu_workbooks(db_factory, seed):
    team_id = seed['admin'].team_id
    with db_factory() as db:
        result = import_workbooks(db, team_id, DOCS / '【定时定点】Token支出价格指数.xlsx',
                                  DOCS / '【定时定点】AI芯片租赁价格指数.xlsx')
        db.commit()
        assert result['token']['inserted'] == 314
        assert result['gpu']['inserted'] == 768
        rows = db.scalars(select(AIComputeObservation).where(AIComputeObservation.team_id == team_id)).all()
        token = [row for row in rows if row.kind == 'token']
        gpu = [row for row in rows if row.kind == 'gpu']
        assert token[-1].date == date(2026, 10, 14) and token[-1].token_price == Decimal('0.975')
        assert gpu[-1].date == date(2026, 10, 15) and gpu[-1].b200 == Decimal('6.01')
        # Importing the same files again must not duplicate or change revisions.
        again = import_workbooks(db, team_id, DOCS / '【定时定点】Token支出价格指数.xlsx',
                                 DOCS / '【定时定点】AI芯片租赁价格指数.xlsx')
        assert again['token']['unchanged'] == 314 and again['gpu']['unchanged'] == 768


def test_token_and_gpu_adapters(monkeypatch, tmp_path):
    token_rows = [
        {'date': '2026-10-07', 'token_price': Decimal('0.9931'), 'a100': None, 'h100': None, 'b200': None},
        {'date': '2026-10-14', 'token_price': Decimal('0.9750'), 'a100': None, 'h100': None, 'b200': None},
    ]
    gpu_rows = [
        {'date': '2026-07-01', 'token_price': None, 'a100': Decimal('1.60'), 'h100': Decimal('3.00'), 'b200': Decimal('6.11')},
        {'date': '2026-10-08', 'token_price': None, 'a100': Decimal('1.53'), 'h100': Decimal('2.80'), 'b200': Decimal('5.88')},
        {'date': '2026-10-15', 'token_price': None, 'a100': Decimal('1.52'), 'h100': Decimal('2.81'), 'b200': Decimal('6.01')},
    ]
    monkeypatch.setattr(ai_compute, '_rows', lambda kind: token_rows if kind == 'token' else gpu_rows)
    token = ai_compute.token_cost('2026-10-14', tmp_path, None)
    assert token['data_date'] == '2026-10-14'
    assert '0.9750' in token['dynamic_body'] and '1.82' in token['dynamic_body']
    assert token['facts']['peak_relation'] == 'below'
    assert token['facts']['drawdown_pct'] == '1.82'
    assert token['facts']['prior_peak_price'] == '0.9931'
    assert (tmp_path / token['images'][0]).is_file()
    gpu = ai_compute.gpu_rental('2026-10-15', tmp_path, None)
    assert gpu['data_date'] == '2026-10-15'
    assert '6.01' in gpu['dynamic_body'] and '2.81' in gpu['dynamic_body'] and '1.52' in gpu['dynamic_body']
    assert '低1.64%' in gpu['dynamic_body']
    assert gpu['facts']['b200_peak_relation'] == 'below'
    assert gpu['facts']['b200_gap_pct'] == '1.64'
    assert (tmp_path / gpu['images'][0]).is_file()


def test_manual_generation_uses_latest_rows_when_production_day_is_newer(monkeypatch, tmp_path):
    token_rows = [
        {'date': '2026-10-07', 'token_price': Decimal('0.9931'), 'a100': None, 'h100': None, 'b200': None},
        {'date': '2026-10-14', 'token_price': Decimal('0.9750'), 'a100': None, 'h100': None, 'b200': None},
    ]
    gpu_rows = [
        {'date': '2026-07-01', 'token_price': None, 'a100': Decimal('1.60'), 'h100': Decimal('3.00'), 'b200': Decimal('6.11')},
        {'date': '2026-10-08', 'token_price': None, 'a100': Decimal('1.53'), 'h100': Decimal('2.80'), 'b200': Decimal('5.88')},
        {'date': '2026-10-15', 'token_price': None, 'a100': Decimal('1.52'), 'h100': Decimal('2.81'), 'b200': Decimal('6.01')},
    ]
    monkeypatch.setattr(ai_compute, '_rows', lambda kind: token_rows if kind == 'token' else gpu_rows)
    monkeypatch.setenv('MANUAL_GENERATION', '1')
    token_dir = tmp_path / 'token'; token_dir.mkdir()
    gpu_dir = tmp_path / 'gpu'; gpu_dir.mkdir()
    token = ai_compute.token_cost('2026-10-21', token_dir, None)
    assert token['data_date'] == '2026-10-14'
    assert '2026-10-14' in token['dynamic_body']
    assert any('2026-10-21' in warning for warning in token['warnings'])
    gpu = ai_compute.gpu_rental('2026-10-21', gpu_dir, None)
    assert gpu['data_date'] == '2026-10-15'
    assert '2026-10-15' in gpu['dynamic_body']
    assert any('2026-10-21' in warning for warning in gpu['warnings'])
    monkeypatch.setenv('MANUAL_GENERATION', '0')
    with pytest.raises(ValueError, match='尚未更新'):
        ai_compute.token_cost('2026-10-21', token_dir, None)
