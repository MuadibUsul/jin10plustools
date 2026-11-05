"""Team-scoped production health, independent from API process liveness."""
import json
from datetime import datetime,timedelta,date
from sqlalchemy import select
from app.config import get_settings
from app.db.models import BriefIssue,BriefVersion,BriefJob,BriefConfig,BriefDataCheck,utcnow
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
    if worker.get('draining'):alerts.append({'code':'worker_draining','message':'快讯队列处于部署维护状态；若发布已结束，请检查部署记录'})
    if (get_settings().storage_dir/'.brief-deployment-failure.json').exists():
        alerts.append({'code':'deployment_failed','message':'最近一次PLUS发布未完成，请检查部署失败记录和处理进程'})
    items=[]
    config=db.get(BriefConfig,team_id)
    scheduled=bool(config and config.enabled)
    collecting=bool(config and config.data_enabled)
    def aware(value):return value.replace(tzinfo=now.tzinfo) if value.tzinfo is None else value
    for category in AUTO_CATEGORIES:
        issue=db.scalar(select(BriefIssue).where(BriefIssue.team_id==team_id,BriefIssue.category==category,
            BriefIssue.version>0).order_by(BriefIssue.issue_date.desc()).limit(1))
        version=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version)) if issue else None
        actual=(version.payload or {}).get('data_date') if version else None
        try:target=expected_date(category,local.date()).isoformat()
        except CalendarUnavailable as exc:
            alerts.append({'code':'calendar','category':category,'message':str(exc)});continue
        item={'category':category,'data_date':actual,'target_date':target,'status':'current' if actual==target else 'awaiting_source'}
        item['schedule_enabled']=scheduled
        if category=='oil_etf':
            query=select(BriefDataCheck).where(BriefDataCheck.team_id==team_id,BriefDataCheck.category==category)
            attempt=db.scalar(query.order_by(BriefDataCheck.created_at.desc()).limit(1))
            checked=db.scalar(query.where(BriefDataCheck.report['current_data_valid'].as_boolean().is_(True)).order_by(
                BriefDataCheck.created_at.desc()).limit(1))
            recent=bool(checked and timedelta(0)<=now-aware(checked.created_at)<=timedelta(hours=26))
            item['last_verified_at']=checked.created_at.isoformat() if checked else None
            item['last_attempt_ok']=attempt.report.get('current_data_valid',False) if attempt else None
            item['status']='latest_available' if actual and recent else 'verification_stale' if actual else 'awaiting_source'
            if (scheduled or collecting) and item['status']=='verification_stale':
                alerts.append({'code':'source_verification_stale','category':category,'message':'原油发行方最近成功核查超过26小时或尚无可验证记录；请检查抓取任务'})
            elif (scheduled or collecting) and recent and item['last_attempt_ok'] is False and now-aware(checked.created_at)>timedelta(hours=2):
                alerts.append({'code':'source_check_failed','category':category,'message':'原油发行方最新核查失败，已保留最近可用数据；请检查取数任务'})
        if scheduled and local.hour>=10 and item['status']=='awaiting_source':
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
