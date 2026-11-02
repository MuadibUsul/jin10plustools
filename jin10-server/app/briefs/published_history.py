"""Import explicitly supplied published observations, preserving disclosed precision.

Only direct dated levels are imported. Changes, units in error, inferred prior
levels and publication dates used as market dates are never reconstruction inputs.
"""
from datetime import date,datetime
from decimal import Decimal
import hashlib
import re

from sqlalchemy import select
from app.db.models import BriefDataPoint,BriefSourceSnapshot
from .data_reliability import save_points
from .trading_calendar import is_session

METHOD='jin10_published_oil_exposure_rounded_v1'


def oil_observations(raw):
    document=raw.decode('utf-8-sig');points=[];seen=set()
    for section in re.split(r'(?m)^### ',document)[1:]:
        stamp=re.search(r'- 数据日：(\d{4}-\d{2}-\d{2})',section)
        published=re.search(r'- 发布时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',section)
        if not stamp or not published:continue
        day=date.fromisoformat(stamp[1])
        if not is_session('xnys',day):raise ValueError('存档数据日不是美国交易日')
        published_at=datetime.fromisoformat(published[1]+'+08:00')
        if published_at.date()<day:raise ValueError('发布时间早于数据日')
        for ticker,pattern in (
            ('USO',r'WTI（USO）名义敞口(?:估算)?约?([0-9.]+)万桶'),
            ('BNO',r'Brent（BNO）期货持仓(?:约|为)?([0-9.]+)万桶')):
            matches=re.findall(pattern,section)
            if not matches:continue
            if len(matches)!=1 or (ticker,day) in seen:raise ValueError('存档同日数值不唯一')
            seen.add((ticker,day));number=Decimal(matches[0])
            if not number.is_finite() or number<=0:raise ValueError('存档持仓数值无效')
            values={'date':day.isoformat(),'ticker':ticker,'barrels':str(number*10000),
                'reported_value_wan_barrels':matches[0],
                'reported_quantum_barrels':str(Decimal(1).scaleb(number.as_tuple().exponent)*10000),
                'approximate':True,'source_status':'user_supplied_published_archive',
                'source_published_at':published_at.isoformat(),
                'exposure_basis':'nominal_exposure_estimate' if ticker=='USO' else 'futures_contracts_1000',
                'method':METHOD}
            if ticker=='BNO':values['futures_barrels']=values['barrels']
            points.append({'series':ticker,'date':day.isoformat(),'method':METHOD,'values':values})
    if not points:raise ValueError('存档没有明确日期、单位和持仓值')
    return points


def import_oil_archive(db,job,raw,*,minimum_overlap_dates=3):
    """Check disclosed precision against issuer observations before adding gaps."""
    if job.category!='oil_etf':raise ValueError('仅允许导入原油ETF存档')
    points=oil_observations(raw);existing={}
    for row in db.scalars(select(BriefDataPoint).where(BriefDataPoint.team_id==job.team_id,
        BriefDataPoint.category=='oil_etf').order_by(BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())):
        if row.method=='issuer_futures_and_swap_estimate_v1':
            existing.setdefault((row.series,row.data_date.isoformat()),row)
    overlap={ticker:set() for ticker in ('USO','BNO')};pending=[]
    for point in points:
        key=(point['series'],point['date']);old=existing.get(key)
        if old is None:pending.append(point);continue
        if old.values.get('barrels') is None:raise ValueError('现有发行方记录缺少可比较持仓')
        difference=abs(Decimal(str(old.values['barrels']))-Decimal(point['values']['barrels']))
        tolerance=Decimal(point['values']['reported_quantum_barrels'])/2
        if difference>tolerance:raise ValueError(f'{point["series"]} {point["date"]}存档与发行方超出披露精度，停止导入')
        overlap[point['series']].add(point['date'])
    if any(len(days)<minimum_overlap_dates for days in overlap.values()):
        raise ValueError('与发行方的重叠日期不足，不能自动导入存档')
    sha=hashlib.sha256(raw).hexdigest();name='published-history/oil-'+sha+'.md'
    snapshot=db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id==job.id,
        BriefSourceSnapshot.attempt==job.attempts,BriefSourceSnapshot.relative_path==name))
    if snapshot is None:
        snapshot=BriefSourceSnapshot(team_id=job.team_id,job_id=job.id,attempt=job.attempts,
            category='oil_etf',data_date=job.issue_date,relative_path=name,
            content=raw,sha256=sha,byte_count=len(raw))
        db.add(snapshot);db.flush()
    elif snapshot.sha256!=sha or snapshot.content!=raw:raise ValueError('存档证据不一致')
    restored=save_points(db,job.team_id,'oil_etf',pending,job.id,
        source_snapshot_id=snapshot.id,collected_at=snapshot.captured_at)
    return {'restored_revisions':restored,'source_sha256':sha,
        'overlap_dates':{ticker:sorted(days) for ticker,days in overlap.items()},
        'archive_dates':sorted({point['date'] for point in pending}),
        'precision_policy':'direct_published_rounded_levels; no daily changes inferred',
        'source_method':METHOD}
