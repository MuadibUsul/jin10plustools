"""A single sleeping worker, PostgreSQL queue, bounded morning schedule."""
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime,timedelta,time as clock

from sqlalchemy import select,text,update

from app.config import get_settings
from app.db.models import BriefConfig,BriefIssue,BriefJob,BriefVersion,BriefSourceSnapshot,utcnow
from app.db.session import get_session_factory,get_engine
from .policy import CATEGORIES,CHINA,expected_date,first_run,production_window,next_slot
from .service import enqueue,ensure_issue,owned,append_version,root_for
from .source_archive import archive_run

log=logging.getLogger('plus.briefs')


def schedule(factory,now=None):
    now=now or datetime.now(CHINA)
    if not production_window(now):return 0
    local=now.astimezone(CHINA);count=0
    with factory() as db:
        for config in db.scalars(select(BriefConfig).where(BriefConfig.enabled.is_(True))):
            for category in CATEGORIES:
                if local.time().replace(tzinfo=None)<first_run(category):continue
                enqueue(db,config.team_id,local.date(),category);count+=1
        db.commit()
    return count


def claim(factory,now=None):
    now=now or utcnow()
    with factory() as db:
        # Main loop has one advisory-locked runner; expired jobs survive restarts.
        db.execute(update(BriefJob).where(BriefJob.status=='running',BriefJob.lease_until<now)
                   .values(status='queued',error='上次运行中断，已重新排队',lease_until=None))
        row=db.scalars(select(BriefJob).where(BriefJob.status.in_(['queued','waiting']),
            BriefJob.next_attempt_at<=now).order_by(BriefJob.next_attempt_at,BriefJob.created_at)
            .with_for_update(skip_locked=True).limit(1)).first()
        if not row:db.commit();return None
        if row.created_by is None and not row.preview and (row.issue_date!=now.astimezone(CHINA).date() or not production_window(now)):
            row.status='skipped';row.active_key=None;row.error='已超过工作日上午生产窗口';row.updated_at=now;db.commit();return None
        row.status='running';row.attempts+=1;row.lease_until=now+timedelta(minutes=7);row.updated_at=now
        db.commit();return row.id


def execute(factory,id):
    settings=get_settings()
    with factory() as db:
        job=db.get(BriefJob,id)
        if not job or job.status!='running':return
        team_id=job.team_id;category=job.category;preview=job.preview;day=job.issue_date;action=job.action
        # Scheduled jobs have no requesting user; manual jobs keep their editor id.
        automatic=job.created_by is None and not preview
    root=root_for(team_id);out=root/'runs'/str(id);out.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env['DEEPSEEK_API_KEY']=settings.briefs_deepseek_api_key;env['DEEPSEEK_MODEL']=settings.briefs_deepseek_model
    command=[sys.executable,'-m','app.briefs.runner','--category',category,'--date',expected_date(category,day).isoformat(),
             '--root',str(root),'--run-id',str(id),'--action',action]
    process=subprocess.Popen(command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=os.name!='nt')
    cutoff=time.monotonic()+360
    error=None
    while process.poll() is None:
        if time.monotonic()>cutoff or (automatic and not production_window(datetime.now(CHINA))):
            if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
            process.wait();error='任务超时或自动任务已到10:00截止时间';break
        time.sleep(1)
    result=out/'result.json'
    # If AI times out, the already saved complete rule draft is still usable.
    payload=json.loads(result.read_text(encoding='utf-8')) if result.exists() else None
    if not payload and (out/'error.json').exists():
        report=json.loads((out/'error.json').read_text(encoding='utf-8'));error=report['message']
    with factory() as db:
        job=db.get(BriefJob,id);job.lease_until=None;job.updated_at=utcnow()
        try:
            source_dir=None
            if payload and payload.get('artifact_dir'):
                source_dir=(root/payload['artifact_dir']).resolve()
                if not source_dir.is_relative_to(root.resolve()):
                    raise ValueError('被复用的来源快照路径不安全')
            source_archive=archive_run(db,job,out,history_dir=root/'history'/category,
                                       source_dir=source_dir)
        except Exception as exc:
            db.rollback()
            job=db.get(BriefJob,id)
            job.status='failed';job.active_key=None;job.lease_until=None
            job.error=f'来源数据入库失败：{exc}'
            job.updated_at=utcnow();db.commit()
            log.exception('Brief source archive failed for job %s',id)
            return
        if payload and payload.get('valid'):
            if automatic and not production_window(datetime.now(CHINA)):
                job.status='skipped';job.active_key=None;job.error='超过10:00，快照保留但未生成当天稿';db.commit();return
            issue=ensure_issue(db,team_id,day,category);issue=owned(db,team_id,issue.id,True)
            if action!='produce' and issue.version:
                previous=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version)).first()
                if previous and 'fixed_notes' in previous.payload:payload['fixed_notes']=previous.payload['fixed_notes']
            payload['preview']=preview
            payload['source_archive']=source_archive
            if error:payload.setdefault('warnings',[]).append('规则稿已保存；模型未完成或任务达到时限')
            version=append_version(db,issue,payload,user_id=job.created_by)
            db.execute(update(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id==id,
                BriefSourceSnapshot.attempt==job.attempts).values(version_id=version.id))
            job.status='succeeded';job.active_key=None;job.error=None
        else:
            job.error=error or '未生成完整结果，来源可能尚未更新'
            slot=next_slot(category,datetime.now(CHINA)) if automatic else None
            if slot and job.attempts<6:
                job.status='waiting';job.next_attempt_at=slot
            else:job.status='failed';job.active_key=None
        db.commit()


def main():
    logging.basicConfig(level=logging.INFO)
    factory=get_session_factory();engine=get_engine()
    # A session advisory lock also protects shared per-team historical snapshots.
    with engine.connect() as connection:
        if engine.dialect.name=='postgresql':
            if not connection.execute(text('SELECT pg_try_advisory_lock(71010501)')).scalar():
                raise SystemExit('Another brief worker is active')
        while True:
            try:
                schedule(factory);id=claim(factory)
                if id:execute(factory,id);continue
            except Exception:log.exception('Brief worker iteration failed')
            time.sleep(30)


if __name__=='__main__':main()
