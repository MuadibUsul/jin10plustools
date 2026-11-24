"""Evidence-based exclusions; never delete or rewrite an observation's values."""
from datetime import datetime, time
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select

from app.db.models import BriefDataPoint, BriefSourceSnapshot
from .policy import CHINA

VOLUME_METHOD = 'hushen_market_turnover_yi_v2'
VOLUME_METHODS = frozenset({'jin10_hushen_turnover_yi_v1',
                          'eastmoney_hushen_index_turnover_yi_v1', VOLUME_METHOD})


def volume_exclusion(values):
    if values.get('source_kind') == 'jin10_published_closing_flash':
        return '已发布快讯不作为行情采集源；原文及原始数值保留待查'
    updated = values.get('source_updated_at') or values.get('updated_at')
    if updated:
        try:
            stamp = datetime.fromisoformat(updated.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                return '来源更新时间缺少时区，不能核验收盘时点'
            close = datetime.combine(datetime.fromisoformat(values['date']).date(), time(15), CHINA)
            if stamp < close:
                return '原始行情最后更新时间早于当日15:00，不是已核验的收盘值'
        except (ValueError, TypeError, KeyError):
            return '来源日期或更新时间不合法'
    return None


def volume_edition_exclusion(db,team_id,payload,folder):
    """Prevent adoption of a retained draft whose observation was quarantined."""
    from .trading_calendar import require_window
    path=folder/'plot-data.json'
    facts=payload.get('facts') or {}
    urls=[facts.get('source',''),*[item.get('url','') for item in facts.get('supplemental_sources') or []]]
    if any('flash.jin10.com' in url for url in urls):
        return '此稿使用了已隔离的快讯反补数据，请重新生成并核对'
    if not path.is_file():return None
    if path.stat().st_size>8_000_000:return '稿件数据快照过大，无法核验'
    rows=json.loads(path.read_text(encoding='utf-8-sig'))
    needed=set(require_window(rows,'xshg',payload['data_date'],21,label='稿件收盘数据'))
    by_day={row['date']:row for row in rows if row['date'] in needed}
    for row in by_day.values():
        reason=volume_exclusion(row)
        if reason:return reason+'；请重新生成并核对'
    rejected=db.scalars(select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
        BriefDataPoint.category=='volume',BriefDataPoint.quality_status=='rejected',
        BriefDataPoint.data_date.in_([datetime.fromisoformat(day).date() for day in needed])))
    for point in rejected:
        row=by_day[point.data_date.isoformat()]
        if all(Decimal(str(row[key]))==Decimal(str(point.values[key])) for key in ('turn','vol')):
            return '此稿包含已隔离的收盘观察，请重新生成并核对：'+point.data_date.isoformat()
    return None


def quarantine_volume(db, team_id):
    """Identify old normalized intraday rows using their actual raw captures.

    Legacy normalization discarded updated_at. Match BOTH monetary and volume
    values before excluding such a row; a bad capture never invalidates a later
    genuine closing revision for the same date.
    """
    points = list(db.scalars(select(BriefDataPoint).where(
        BriefDataPoint.team_id == team_id, BriefDataPoint.category == 'volume',
        BriefDataPoint.quality_status == 'accepted')))
    legacy = {point.data_date.isoformat() for point in points
              if not point.values.get('source_kind')}
    intraday = set()
    if legacy:
        captures = db.scalars(select(BriefSourceSnapshot).where(
            BriefSourceSnapshot.team_id == team_id, BriefSourceSnapshot.category == 'volume',
            BriefSourceSnapshot.relative_path.like('%volume-raw-page-%.json')).order_by(
            BriefSourceSnapshot.captured_at.desc()).limit(2048))
        seen = set()
        for capture in captures:
            if capture.sha256 in seen:
                continue
            seen.add(capture.sha256)
            if hashlib.sha256(capture.content).hexdigest() != capture.sha256:
                raise ValueError('成交额原始归档哈希不符，停止历史质量核验')
            raw = json.loads(capture.content, parse_float=Decimal)
            for row in raw.get('data', []):
                if row.get('date') not in legacy or not volume_exclusion(row):
                    continue
                try:
                    intraday.add((row['date'], Decimal(str(row['sh_turn'])) + Decimal(str(row['sz_turn'])),
                                  Decimal(str(row['sh_vol'])) + Decimal(str(row['sz_vol']))))
                except (KeyError, TypeError, ArithmeticError):
                    continue
    rejected = []
    for point in points:
        reason = volume_exclusion(point.values)
        if not reason and not point.values.get('source_kind'):
            try:
                key = (point.data_date.isoformat(), Decimal(str(point.values['turn'])),
                       Decimal(str(point.values['vol'])))
                if key in intraday:
                    reason = '已与原始分页核对：旧解析丢失更新时间，误将盘中值保存为收盘值'
            except (KeyError, TypeError, ArithmeticError):
                reason = '成交额历史必要字段不合法'
        if reason:
            point.quality_status = 'rejected'
            point.quality_reason = reason
            rejected.append({'id': str(point.id), 'date': point.data_date.isoformat(), 'reason': reason})
    db.flush()
    return rejected
