"""Editor supplied replacement observations for the two compute briefs."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.models import AIComputeObservation, utcnow

FIELDS = {'token_cost': ('token',), 'gpu_rental': ('a100', 'h100', 'b200')}
PRICE = re.compile(r'^(?:0|[1-9]\d*)(?:\.\d{1,6})?$')
CHANGE = re.compile(r'^-?(?:0|[1-9]\d*)(?:\.\d{1,3})?$')


def normalize(category: str, data_day: date, issue_day: date, today: date,
              prices: dict[str, str | None], changes: dict[str, str | None],
              source_note: str, editor_id) -> dict:
    metrics = FIELDS.get(category)
    if metrics is None:
        raise ValueError('仅两个AI算力栏目支持人工录入')
    if data_day > today or data_day > issue_day:
        raise ValueError('数据日不能晚于今天或稿件生产日')
    if (issue_day - data_day).days > 7:
        raise ValueError('数据日与生产日相差超过七天，请先核对日期')
    if category == 'gpu_rental':
        entered_changes = [(changes.get(metric) or '').strip() != '' for metric in metrics]
        if any(entered_changes) and not all(entered_changes):
            raise ValueError('GPU三项七日变化请全部填写，或全部留空由历史数据计算')
    readings = []
    for metric in metrics:
        raw = (prices.get(metric) or '').strip()
        if not PRICE.fullmatch(raw) or not 0 < Decimal(raw) < 1000000:
            raise ValueError(f'{metric.upper()}价格须为大于零、最多六位小数的数字')
        weekly = (changes.get(metric) or '').strip()
        if weekly and (not CHANGE.fullmatch(weekly) or abs(Decimal(weekly)) > 1000):
            raise ValueError(f'{metric.upper()}七日变化须为百分数，最多三位小数')
        readings.append({'metric': metric, 'date': data_day.isoformat(), 'price': raw,
                         'precision': len(raw.partition('.')[2]),
                         'week_change_pct': weekly or None,
                         'source_status': 'manual_entry'})
    note = source_note.strip()
    if len(note) > 200:
        raise ValueError('来源备注不能超过200字')
    return {'category': category, 'data_date': data_day.isoformat(),
            'captured_at': utcnow().isoformat(), 'entered_by': str(editor_id),
            'source_note': note or '编辑人工录入', 'readings': readings}


def store_manual(db, team_id, capture: dict) -> dict:
    category = capture['category']
    metrics = FIELDS.get(category)
    readings = capture['readings']
    if metrics is None or [row['metric'] for row in readings] != list(metrics):
        raise ValueError('人工录入指标不完整')
    source_day = date.fromisoformat(capture['data_date'])
    if any(row['date'] != capture['data_date'] for row in readings):
        raise ValueError('人工录入数据日不一致')
    kind = 'token' if category == 'token_cost' else 'gpu'
    row = db.scalar(select(AIComputeObservation).where(
        AIComputeObservation.team_id == team_id,
        AIComputeObservation.kind == kind,
        AIComputeObservation.date == source_day).with_for_update())
    latest = db.scalar(select(AIComputeObservation).where(
        AIComputeObservation.team_id == team_id,
        AIComputeObservation.kind == kind).order_by(AIComputeObservation.date.desc()).limit(1))
    if latest is not None and latest.date > source_day:
        raise ValueError(f'已有{latest.date}的较新数据，本次人工录入未覆盖它')
    values = {item['metric']: Decimal(item['price']) for item in readings}
    display = {'captured_at': capture['captured_at'],
               'entered_by': capture['entered_by'],
               'source_note': capture['source_note'], 'readings': readings}
    if row is None:
        row = AIComputeObservation(team_id=team_id, kind=kind, date=source_day, revision=1)
        db.add(row)
        result = 'inserted'
    else:
        same_values = all(getattr(row, 'token_price' if metric == 'token' else metric) == value
                          for metric, value in values.items())
        old = row.manual_display or {}
        if (same_values and row.source_status == 'manual_entry' and
                old.get('readings') == readings and old.get('source_note') == capture['source_note']):
            return {'status': 'unchanged', 'revision': row.revision}
        row.revision += 1
        result = 'updated_same_day'
    for metric, value in values.items():
        setattr(row, 'token_price' if metric == 'token' else metric, value)
    row.source = 'Editor manual entry'
    row.source_status = 'manual_entry'
    row.manual_display = display
    row.public_display = None
    row.updated_at = utcnow()
    db.flush()
    return {'status': result, 'revision': row.revision}
