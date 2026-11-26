"""Keep a pre-close turnover print out of the verified daily close.

Original responses, draft files and stored numbers stay in place. This only
refuses to treat an early print as the close and blocks confirming that draft.
"""
from datetime import datetime, time
from decimal import Decimal
import json
from pathlib import Path

from .policy import CHINA


def close_rejection(row):
    """Reason this row is not a verified 15:00 close. No clock means no rejection."""
    updated = row.get('updated_at') or row.get('source_updated_at')
    if not updated:
        return None
    try:
        stamp = datetime.fromisoformat(str(updated).replace('Z', '+00:00'))
        day = datetime.fromisoformat(row['date']).date()
    except (TypeError, ValueError, KeyError):
        return '来源日期或更新时间不合法，不能核验收盘时点'
    if stamp.tzinfo is None:
        return '来源更新时间缺少时区，不能核验收盘时点'
    if stamp < datetime.combine(day, time(15), CHINA):
        return '原始行情最后更新时间早于当日15:00，不是已核验的收盘值'
    return None


def turnover_key(row):
    """(date, total amount, total volume) for either a raw market row or a plot row."""
    try:
        if 'sh_turn' in row:
            turn = Decimal(str(row['sh_turn'])) + Decimal(str(row['sz_turn']))
            vol = Decimal(str(row['sh_vol'])) + Decimal(str(row['sz_vol']))
        else:
            turn = Decimal(str(row['turn']))
            vol = Decimal(str(row['vol']))
        return row['date'], turn, vol
    except (KeyError, TypeError, ArithmeticError):
        return None


def draft_block(payload, folder, *, db=None, team_id=None):
    """Return (message, matching keys) when this edition used a pre-close print."""
    facts = payload.get('facts') or {}
    urls = [facts.get('source') or '']
    urls.extend(item.get('url') or '' for item in facts.get('supplemental_sources') or [])
    if any('flash.jin10.com' in url for url in urls):
        return '此稿使用了已隔离的快讯反补数据，请重新生成并核对', set()
    path = Path(folder) / 'plot-data.json'
    if not path.is_file():
        return None
    if path.stat().st_size > 8_000_000:
        raise ValueError('稿件数据快照过大，无法核验收盘时点')
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list):
        raise ValueError('稿件收盘数据快照结构无效')
    for row in rows:
        reason = close_rejection(row)
        if reason:
            return reason + '；请重新生成并核对', set()
    rejected = {}
    raw_pages = sorted(Path(folder).glob('volume-raw-page-*.json'))
    for raw_path in raw_pages:
        if raw_path.stat().st_size > 4_000_000:
            continue
        document = json.loads(raw_path.read_text(encoding='utf-8-sig'), parse_float=Decimal)
        for raw in document.get('data') or []:
            reason = close_rejection(raw)
            key = turnover_key(raw)
            if reason and key:
                rejected[key] = reason
    if not raw_pages and db is not None and team_id is not None:
        from datetime import date
        import uuid
        from sqlalchemy import select
        from app.db.models import BriefDataPoint, BriefSourceSnapshot
        archive_job = (payload.get('source_archive') or {}).get('job_id')
        if archive_job:
            try:
                job_uuid = uuid.UUID(str(archive_job))
            except ValueError:
                job_uuid = None
            if job_uuid is not None:
                snaps = db.scalars(select(BriefSourceSnapshot).where(
                    BriefSourceSnapshot.team_id == team_id,
                    BriefSourceSnapshot.job_id == job_uuid,
                    BriefSourceSnapshot.relative_path.like('%volume-raw-page-%.json'),
                ))
                for snap in snaps:
                    if snap.byte_count > 4_000_000:
                        continue
                    document = json.loads(snap.content.decode('utf-8-sig'), parse_float=Decimal)
                    for raw in document.get('data') or []:
                        reason = close_rejection(raw)
                        key = turnover_key(raw)
                        if reason and key:
                            rejected[key] = reason
        row_dates = [date.fromisoformat(r['date']) for r in rows if isinstance(r, dict) and r.get('date')]
        if row_dates:
            quarantined = db.scalars(select(BriefDataPoint).where(
                BriefDataPoint.team_id == team_id,
                BriefDataPoint.category == 'volume',
                BriefDataPoint.data_date.in_(row_dates),
                BriefDataPoint.quality_status == 'rejected',
            ))
            for point in quarantined:
                key = turnover_key({'date': point.data_date.isoformat(), **point.values})
                if key:
                    rejected.setdefault(key, point.quality_reason or '原始行情未通过收盘时点核验')
    matched = set()
    for row in rows:
        key = turnover_key(row)
        if key in rejected:
            matched.add(key)
    if matched:
        day = sorted(key[0] for key in matched)[0]
        return '此稿包含未核验收盘的成交额，请重新生成并核对：' + day, matched
    return None


def mark_rejected_closes(db, team_id, matched):
    """Flag matching observations. Values, hashes and revisions stay unchanged."""
    if not matched:
        return
    from datetime import date
    from sqlalchemy import select
    from app.db.models import BriefDataPoint
    days = [date.fromisoformat(day) for day, _, _ in matched]
    points = db.scalars(select(BriefDataPoint).where(
        BriefDataPoint.team_id == team_id, BriefDataPoint.category == 'volume',
        BriefDataPoint.data_date.in_(days), BriefDataPoint.quality_status == 'accepted'))
    for point in points:
        key = turnover_key({'date': point.data_date.isoformat(), **point.values})
        if key in matched:
            point.quality_status = 'rejected'
            point.quality_reason = '原始行情最后更新时间早于当日15:00，不是已核验的收盘值'
    db.flush()
