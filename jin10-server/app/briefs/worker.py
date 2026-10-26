"""A single sleeping worker, PostgreSQL queue, bounded morning schedule."""
import json
import logging
import os
import httpx
import signal
import subprocess
import sys
import time
from datetime import date,datetime,timedelta,time as clock

from sqlalchemy import select,text,update

from app.config import get_settings
from app.db.models import BriefConfig,BriefIssue,BriefJob,BriefVersion,BriefSourceSnapshot,Team,utcnow
from app.services.team_settings import briefs_ai
from .delivery import default_notes
from app.db.session import get_session_factory,get_engine
from .policy import AUTO_CATEGORIES,CATEGORIES,CHINA,expected_date,first_run,production_window,next_slot
from .service import enqueue,ensure_issue,owned,append_version,root_for,find_issue
from .source_archive import archive_run,restore_pe_capture
from .progress import record,read

log=logging.getLogger('plus.briefs')

PUBLIC_COMPUTE_CATEGORIES = frozenset({'token_cost', 'gpu_rental'})


def public_read_requested(job) -> bool:
    """Only an editor's Generate action reads the public compute pages."""
    return (job.category in PUBLIC_COMPUTE_CATEGORIES and job.action == 'produce'
            and job.created_by is not None and getattr(job, 'manual_capture', None) is None)


def capture_public_for_job(factory, job_id, out):
    from .public_compute import fetch_visible
    from .public_compute_store import store_visible
    from .sources import save

    with factory() as db:
        job = db.get(BriefJob, job_id)
        if not public_read_requested(job):
            return None
        category, issue_day, team_id = job.category, job.issue_date, job.team_id
    capture = fetch_visible(category, today=datetime.now(CHINA).date())
    source_day = date.fromisoformat(capture['data_date'])
    if source_day > issue_day:
        raise ValueError('网页数据日晚于所选生产日，请选择当前生产日后再生成')
    if (datetime.now(CHINA).date() - source_day).days > 7:
        raise ValueError(f'公开网页仍停留在{source_day}，超过七日；为避免旧稿，本次停止生成')
    with factory() as db:
        result = store_visible(db, team_id, capture)
        db.commit()
    save(out/'public-compute-read.json', {**capture, 'storage_result': result})
    return capture


def capture_manual_for_job(factory, job_id, out):
    from .manual_compute import store_manual
    from .sources import save

    with factory() as db:
        job = db.get(BriefJob, job_id)
        capture, team_id = job.manual_capture, job.team_id
        if not capture or job.category not in PUBLIC_COMPUTE_CATEGORIES:
            raise ValueError('任务没有有效的人工录入数据')
        if capture['category'] != job.category:
            raise ValueError('人工录入栏目与生成任务不一致')
        result = store_manual(db, team_id, capture)
        db.commit()
    save(out/'manual-compute-read.json', {**capture, 'storage_result': result})
    return capture


def schedule(factory,now=None):
    now=now or datetime.now(CHINA)
    if not production_window(now):return 0
    local=now.astimezone(CHINA);count=0
    with factory() as db:
        for config in db.scalars(select(BriefConfig).where(BriefConfig.enabled.is_(True))):
            for category in AUTO_CATEGORIES:
                if local.time().replace(tzinfo=None)<first_run(category):continue
                issue=find_issue(db,config.team_id,local.date(),category)
                if issue and issue.version:continue
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
        requested_public_read=public_read_requested(job)
        requested_manual_read=(category in PUBLIC_COMPUTE_CATEGORIES and action=='produce'
                               and job.manual_capture is not None)
        team=db.get(Team,team_id)
        ai=briefs_ai(team)
        team_settings=dict(team.settings or {})
        config=db.get(BriefConfig,team_id)
        category_notes=(config.notes or {}).get(category,default_notes(category)) if config else default_notes(category)
        if category in PUBLIC_COMPUTE_CATEGORIES and not str(category_notes or '').strip():
            category_notes=default_notes(category)
    root=root_for(team_id);out=root/'runs'/str(id);out.mkdir(parents=True,exist_ok=True)
    record(out,1,f'任务开始：生产日 {day}，目标数据日 {expected_date(category,day)}')
    capture = None
    if requested_public_read or requested_manual_read:
        try:
            capture = (capture_manual_for_job(factory, id, out) if requested_manual_read
                       else capture_public_for_job(factory, id, out))
            origin = '人工录入' if requested_manual_read else '公开网页'
            record(out,2,f'{origin}数据已核对，截至{capture["data_date"]}')
        except Exception as exc:
            if not isinstance(exc, ValueError):
                log.exception('Public compute read failed for job %s', id)
            message = str(exc) if isinstance(exc, ValueError) else '公开网页读取发生内部错误，请稍后由编辑重试'
            with factory() as db:
                failed = db.get(BriefJob,id)
                failed.status='failed';failed.active_key=None;failed.lease_until=None
                failed.error=message[:500];failed.updated_at=utcnow()
                db.commit()
            record(out,2,'公开网页读取未完成：'+message[:500])
            return
    if category=='valuation' and action=='produce':
        with factory() as db:
            restored=restore_pe_capture(db,db.get(BriefJob,id),out,
                                        expected_date(category,day).isoformat())
            if restored:log.info('Restored dated PE evidence from PostgreSQL for job %s',id)
            if restored:record(out,1,'已恢复经过哈希核验的PE原始来源证据')
    env=dict(os.environ);env['AI_PROVIDER']=ai['provider'];env['AI_API_KEY']=ai['key']
    env['AI_MODEL']=ai['model'];env['AI_BASE_URL']=ai['base_url']
    env['CATEGORY_NOTES']=category_notes
    env['AI_COMPUTE_TEAM_ID']=str(team_id)
    env.pop('AI_COMPUTE_DATA_DATE', None)
    if capture is not None:
        env['AI_COMPUTE_DATA_DATE']=capture['data_date']
    env['MANUAL_GENERATION']='1' if job.created_by is not None else '0'
    # The runner keeps the fixed rule payload; Skill copy runs after it returns.
    env['SKILL_PRODUCTION']='1'
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
    if payload and ai.get('provider')=='chat2api':
        if ai.get('requested_model') and ai['requested_model']!=ai['model']:
            payload.setdefault('warnings',[]).append(
                f"AI 模型 {ai['requested_model']} 不受 chat2api 节点支持，本次实际使用 {ai['model']}")
        try:
            from app.api.chat2api import TOKEN_COUNT
            prefix=settings.chat2api_prefix.strip('/')
            url=f"{settings.chat2api_url.rstrip('/')}/{prefix}/tokens"
            with httpx.Client(timeout=5) as client:
                count=TOKEN_COUNT.search(client.get(url).text)
            if count and int(count.group(1))==0:
                payload.setdefault('warnings',[]).append('chat2api 号池为空，本次 AI 可能使用匿名降级模型')
        except Exception:
            pass
    if payload and payload.get('valid') and action!='render':
        from .body_templates import apply as apply_body_template
        from .skill_production import apply_skill_copy
        apply_body_template(payload,category,team_settings)
        apply_skill_copy(payload,category,team_settings)
        if payload.get('skill'):
            record(out,4,'Skill AI 文案已采用' if payload['skill'].get('status')=='ok'
                   else 'Skill AI 未完成，已保留固定组合文案')
    if payload and payload.get('valid') and category in PUBLIC_COMPUTE_CATEGORIES:
        final_notes = category_notes
        if action != 'produce':
            with factory() as db:
                existing = find_issue(db, team_id, day, category)
                if existing and existing.version:
                    previous_notes = db.scalars(select(BriefVersion).where(
                        BriefVersion.issue_id == existing.id,
                        BriefVersion.number == existing.version)).first()
                    if previous_notes and 'fixed_notes' in (previous_notes.payload or {}):
                        final_notes = previous_notes.payload['fixed_notes']
        from .ai_compute import align_chart_notes
        from .sources import save as save_payload
        if align_chart_notes(payload, category, out, final_notes):
            cache = root / 'cache' / category / f'{expected_date(category, day).isoformat()}.json'
            if cache.is_file():
                cached = json.loads(cache.read_text(encoding='utf-8'))
                if cached.get('artifact_dir') == payload.get('artifact_dir'):
                    cached['fixed_notes'] = payload.get('fixed_notes')
                    cached['chart_notes'] = payload.get('chart_notes')
                    cached['image_sha256'] = payload.get('image_sha256')
                    save_payload(cache, cached)
    if payload and payload.get('valid'):
        from .sources import save
        # The date cache deliberately keeps the validated pre-Skill rule product;
        # this run's durable result must match what is committed as the final edition.
        save(out/'result.json',payload)
    with factory() as db:
        job=db.get(BriefJob,id);job.lease_until=None;job.updated_at=utcnow()
        stopped_step=read(out).get('step',1)
        record(out,5 if payload and payload.get('valid') else stopped_step,'正在归档原始数据、图片快照与本次运行结果')
        try:
            source_dir=None
            if payload and payload.get('artifact_dir'):
                source_dir=(root/payload['artifact_dir']).resolve()
                if not source_dir.is_relative_to(root.resolve()):
                    raise ValueError('被复用的来源快照路径不安全')
            source_archive=archive_run(db,job,out,history_dir=root/'history'/category,
                                       source_dir=source_dir,
                                       data_date=payload.get('data_date') if payload else None)
            if payload and payload.get('valid') and category == 'valuation':
                from .valuation_observations import save_history
                save_history(db, team_id, id, out/'erp-plot-data.json')
        except Exception as exc:
            db.rollback()
            job=db.get(BriefJob,id)
            job.status='failed';job.active_key=None;job.lease_until=None
            job.error=f'来源数据入库失败：{exc}'
            job.updated_at=utcnow();db.commit()
            record(out,5,'来源归档失败，任务已停止')
            log.exception('Brief source archive failed for job %s',id)
            return
        if payload and payload.get('valid'):
            if automatic and not production_window(datetime.now(CHINA)):
                job.status='skipped';job.active_key=None;job.error='超过10:00，快照保留但未生成当天稿';db.commit();record(out,5,job.error);return
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
        record(out,6 if job.status=='succeeded' else stopped_step,
               '生成完成，稿件与快照已保存，可查看与取稿' if job.status=='succeeded' else job.error)


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
