"""Store the ERP series as exportable, team-scoped database rows."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ValuationObservation
from .engines.valuation.calculate import calculate_row


def save_history(db: Session, team_id, job_id, path: Path) -> int:
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list):
        raise ValueError('ERP 历史数据格式错误')
    existing = {row.date: row for row in db.scalars(
        select(ValuationObservation).where(ValuationObservation.team_id == team_id))}
    seen = set()
    for source in rows:
        row = calculate_row(source)
        day = date.fromisoformat(row['date'])
        if day in seen:
            raise ValueError(f'ERP 历史日期重复：{day}')
        seen.add(day)
        values = {
            'close': Decimal(row['close']),
            'pe_ttm_mcw': Decimal(row['pe_ttm_mcw']),
            'bond_yield_number': Decimal(row['bond_yield_number']),
            'erp': Decimal(row['erp']),
        }
        record = existing.get(day)
        if record is None:
            record = ValuationObservation(team_id=team_id, date=day)
            db.add(record)
        for key, value in values.items():
            setattr(record, key, value)
        record.source_status = source.get('source_status')
        record.source_job_id = job_id
    return len(seen)
