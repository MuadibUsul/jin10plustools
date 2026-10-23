"""Persist only normalized visible facts, never source webpage HTML."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.models import AIComputeObservation, utcnow


def store_visible(db, team_id, capture: dict) -> dict:
    category = capture.get('category')
    kind = {'token_cost': 'token', 'gpu_rental': 'gpu'}.get(category)
    if kind is None:
        raise ValueError('公开页面数据仅用于两个AI算力栏目')
    readings = capture.get('readings') or []
    wanted = ('token',) if kind == 'token' else ('a100', 'h100', 'b200')
    if [row.get('metric') for row in readings] != list(wanted):
        raise ValueError('公开页面指标集合不完整')
    stamp = capture['data_date']
    if any(row['date'] != stamp for row in readings):
        raise ValueError('公开页面数据日不一致')
    source_day = date.fromisoformat(stamp)
    existing = db.scalar(select(AIComputeObservation).where(
        AIComputeObservation.team_id == team_id,
        AIComputeObservation.kind == kind,
        AIComputeObservation.date == source_day).with_for_update())
    latest = db.scalar(select(AIComputeObservation).where(
        AIComputeObservation.team_id == team_id,
        AIComputeObservation.kind == kind,
    ).order_by(AIComputeObservation.date.desc()).limit(1))
    if latest is not None and latest.date > source_day:
        raise ValueError(f'公开页面截至{stamp}，晚于它的{latest.date}数据已入库，本次停止生成')
    values = {row['metric']: Decimal(row['price']) for row in readings}
    display = {'captured_at': capture['captured_at'], 'readings': readings}
    if existing is not None and existing.source_status == 'manual_entry':
        return {'status': 'preserved_manual', 'revision': existing.revision}
    # Keep a more precise workbook/verified price while attaching the public
    # card's displayed 7D change when its rounded price agrees exactly.
    if existing is not None and existing.source_status != 'public_visible_summary':
        for row in readings:
            old = getattr(existing, 'token_price' if row['metric'] == 'token' else row['metric'])
            if old is None or old != values[row['metric']]:
                raise ValueError(f'{stamp}已有不同来源数据，请人工核对后再生成')
        if (existing.public_display or {}).get('readings') != readings:
            existing.public_display = display
            existing.revision += 1
            existing.updated_at = utcnow()
            db.flush()
        return {'status': 'preserved_existing', 'revision': existing.revision}
    if existing is None:
        existing = AIComputeObservation(team_id=team_id, kind=kind, date=source_day,
                                        revision=1)
        db.add(existing)
        state = 'inserted'
    else:
        same_values = all(getattr(existing, 'token_price' if metric == 'token' else metric) == value
                          for metric, value in values.items())
        old_readings = (existing.public_display or {}).get('readings')
        if same_values and old_readings == readings:
            return {'status': 'unchanged', 'revision': existing.revision}
        existing.revision += 1
        state = 'revised'
    for metric, value in values.items():
        setattr(existing, 'token_price' if metric == 'token' else metric, value)
    existing.source = 'Silicon Data public product pages'
    existing.source_status = 'public_visible_summary'
    existing.public_display = display
    existing.updated_at = utcnow()
    db.flush()
    return {'status': state, 'revision': existing.revision}
