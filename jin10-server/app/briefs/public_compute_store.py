"""Persist only normalized visible facts, never source webpage HTML."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.models import AIComputeObservation, AIComputeRevision, utcnow


def preserve_revision(db,record):
    if record.id is None:return
    found=db.scalar(select(AIComputeRevision).where(AIComputeRevision.observation_id==record.id,
                                                   AIComputeRevision.revision==record.revision))
    if not found:
        db.add(AIComputeRevision(team_id=record.team_id,observation_id=record.id,date=record.date,
            kind=record.kind,revision=record.revision,source=record.source,
            values={**{key:str(getattr(record,key)) if getattr(record,key) is not None else None
                       for key in ('token_price','a100','h100','b200')},
                    'source_status':record.source_status,'public_display':record.public_display,
                    'manual_display':record.manual_display}))


def store_visible_history(db,team_id,capture):
    """Only complete three-card historical cohorts; keep conflicts for review."""
    kind={'gpu_rental':'gpu','token_cost':'token'}.get(capture.get('category'))
    if kind is None:return {'inserted':0,'revised':0,'conflicts':[]}
    metrics=('token',) if kind=='token' else ('a100','h100','b200')
    histories=capture.get('history') or {};by_metric={};conflicts=[];inserted=revised=0
    for metric in metrics:
        by_metric[metric]={}
        for row in histories.get(metric,[]):
            day=date.fromisoformat(row['date']);value=Decimal(row['price'])
            if not value.is_finite() or value<=0 or value.as_tuple().exponent < -6:
                raise ValueError('GPU历史价格或精度无法无损入库')
            if day in by_metric[metric]:raise ValueError('GPU历史日期重复')
            if day.isoformat()<capture['data_date'] or not capture.get('readings') and day.isoformat()==capture['data_date']:
                by_metric[metric][day]=value
    days=set.union(*(set(by_metric[metric]) for metric in metrics))
    for day in sorted(days):
        values={'token_price' if metric=='token' else metric:by_metric[metric][day] for metric in by_metric if day in by_metric[metric]}
        record=db.scalar(select(AIComputeObservation).where(AIComputeObservation.team_id==team_id,
            AIComputeObservation.kind==kind,AIComputeObservation.date==day).with_for_update())
        if record and any(getattr(record,key)!=value for key,value in values.items()):
            conflict=any(getattr(record,key) is not None and getattr(record,key)!=value for key,value in values.items())
            if record.source_status=='manual_entry' or conflict and record.source_status!='public_visible_history':
                conflicts.append(day.isoformat());continue
            preserve_revision(db,record);record.revision+=1;revised+=1
        elif record:continue
        else:
            record=AIComputeObservation(team_id=team_id,kind=kind,date=day,revision=1,
                source='Silicon Data visible historical table',source_status='public_visible_history')
            db.add(record);inserted+=1
        for key,value in values.items():setattr(record,key,value)
        record.updated_at=utcnow()
    db.flush()
    return {'inserted':inserted,'revised':revised,'conflicts':conflicts}


def restore_workbook_history(db,team_id,*,before=None):
    """Fill real absent dates from authorized workbooks/captures; never replace a row."""
    import json
    from pathlib import Path
    asset=json.loads((Path(__file__).parent/'seed/ai-compute-history.json').read_text(encoding='utf-8'))
    inserted=0
    for kind,rows in asset['rows'].items():
        existing=set(db.scalars(select(AIComputeObservation.date).where(
            AIComputeObservation.team_id==team_id,AIComputeObservation.kind==kind)))
        for row in rows:
            day=date.fromisoformat(row['date'])
            if before and day.isoformat()>=before:continue
            if day in existing:continue
            fields=('token_price',) if kind=='token' else ('a100','h100','b200')
            values={key:Decimal(row[key]) if row.get(key) is not None and Decimal(row[key])>0 else None
                    for key in fields}
            if all(value is None for value in values.values()):continue
            status=row.get('_source_status','imported_workbook')
            db.add(AIComputeObservation(team_id=team_id,kind=kind,date=day,revision=1,
                source='Silicon Data visible historical capture' if status=='public_visible_history'
                       else 'Silicon Data workbook',source_status=status,**values))
            existing.add(day);inserted+=1
    db.flush()
    return inserted


def store_visible(db, team_id, capture: dict,*,allow_historical=False) -> dict:
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
    if latest is not None and latest.date > source_day and not allow_historical:
        raise ValueError(f'公开页面截至{stamp}，晚于它的{latest.date}数据已入库，本次停止生成')
    values = {row['metric']: Decimal(row['price']) for row in readings}
    display = {'captured_at': capture['captured_at'], 'readings': readings}
    if existing is not None and existing.source_status == 'manual_entry':
        return {'status': 'preserved_manual', 'revision': existing.revision}
    # Keep a more precise workbook/verified price while attaching the public
    # card's displayed 7D change when its rounded price agrees exactly.
    if existing is not None and existing.source_status not in ('public_visible_summary','public_visible_history'):
        for row in readings:
            old = getattr(existing, 'token_price' if row['metric'] == 'token' else row['metric'])
            if old is None or old != values[row['metric']]:
                raise ValueError(f'{stamp}已有不同来源数据，请人工核对后再生成')
        if (existing.public_display or {}).get('readings') != readings:
            preserve_revision(db,existing)
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
        preserve_revision(db,existing)
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
