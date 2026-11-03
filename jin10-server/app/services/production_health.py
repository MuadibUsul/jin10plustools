"""Team-scoped production health, independent from API process liveness."""
import json
from datetime import datetime,timedelta,date
from sqlalchemy import select
from app.config import get_settings
from app.db.models import BriefIssue,BriefVersion,BriefJob,utcnow
from app.briefs.policy import AUTO_CATEGORIES,CATEGORIES,CHINA,expected_date
from app.briefs.trading_calendar import calendars,CalendarUnavailable


def calendar_status(today=None):
    today=today or datetime.now(CHINA).date();items=[]
    for market,spec in calendars().items():
        if not isinstance(spec,dict) or 'end' not in spec:continue
        remaining=(date.fromisoformat(spec['end'])-today).days
        items.append({'market':market,'verified_through':spec['end'],'days_remaining':remaining,
            'status':'expired' if remaining<0 else 'needs_update' if remaining<=90 else 'ok'})
    return items


def production_status(db,team_id,now=None):
    now=now or utcnow();local=now.astimezone(CHINA);alerts=[]
    path=get_settings().storage_dir/'.brief-worker-heartbeat.json'
    try:
        worker=json.loads(path.read_text(encoding='utf-8'))
        stamp=datetime.fromisoformat(worker['at']);worker['ready']=timedelta(0)<=now-stamp<timedelta(seconds=150)
    except (OSError,ValueError,KeyError,TypeError):worker={'ready':False,'state':'unknown'}
    if not worker['ready']:alerts.append({'code':'worker_heartbeat','message':'快讯处理进程心跳异常，请检查队列'})
    items=[]
    for category in AUTO_CATEGORIES:
        issue=db.scalar(select(BriefIssue).where(BriefIssue.team_id==team_id,BriefIssue.category==category,
            BriefIssue.version>0).order_by(BriefIssue.issue_date.desc()).limit(1))
        version=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version)) if issue else None
        actual=(version.payload or {}).get('data_date') if version else None
        try:target=expected_date(category,local.date()).isoformat()
        except CalendarUnavailable as exc:
            alerts.append({'code':'calendar','category':category,'message':str(exc)});continue
        item={'category':category,'data_date':actual,'target_date':target,'status':'current' if actual==target else 'awaiting_source'}
        if category=='oil_etf':item['status']='latest_available' if actual else 'awaiting_source'
        if local.hour>=10 and item['status']=='awaiting_source':
            alerts.append({'code':'missing_daily_product','category':category,'message':CATEGORIES[category]+'尚无目标数据日的完整成稿'})
        pending=db.scalar(select(BriefJob).where(BriefJob.team_id==team_id,BriefJob.category==category,
            BriefJob.status.in_(['queued','waiting','running'])).order_by(BriefJob.created_at).limit(1))
        if pending:
            pending_time=pending.updated_at if pending.status=='running' else pending.next_attempt_at
            if pending_time.tzinfo is None:pending_time=pending_time.replace(tzinfo=now.tzinfo)
            if now-pending_time>timedelta(minutes=10):alerts.append({'code':'stalled_queue','category':category,'message':CATEGORIES[category]+'任务长时间未推进'})
        items.append(item)
    calendar=calendar_status(local.date())
    for item in calendar:
        if item['status']!='ok':alerts.append({'code':'calendar_expiry','market':item['market'],
            'message':item['market']+'日历核验有效至'+item['verified_through']+'，需要更新官方日历'})
    return {'status':'attention' if alerts else 'ok','checked_at':now.isoformat(),'worker':worker,
        'items':items,'calendars':calendar,'alerts':alerts}
