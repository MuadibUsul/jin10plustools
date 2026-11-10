"""A single sleeping worker, PostgreSQL queue, bounded morning schedule."""
import json
import logging
import os
import httpx
import signal
import subprocess
import sys
import time
from pathlib import Path
from datetime import date,datetime,timedelta,time as clock

from sqlalchemy import case,select,text,update,func

from app.config import get_settings
from app.db.models import BriefConfig,BriefIssue,BriefJob,BriefVersion,BriefSourceSnapshot,BriefDataCheck,Team,utcnow
from app.services.team_settings import briefs_ai
from .delivery import default_notes
from app.db.session import get_session_factory,get_engine
from .policy import AUTO_CATEGORIES,CATEGORIES,CHINA,expected_date,first_run,production_window,next_slot
from .service import enqueue,ensure_issue,owned,append_version,root_for,find_issue,edition_payload
from .source_archive import archive_run,restore_pe_capture
from .progress import record,read

log=logging.getLogger('plus.briefs')
_active_process=None


def abort_on_lock_loss():
    log.critical('Worker exclusive database lock lost; stopping before further writes')
    if _active_process is not None:
        from app.services.processes import kill_tree
        try:kill_tree(_active_process)
        except Exception:log.exception('Could not terminate active source process')
    os._exit(75)

PUBLIC_COMPUTE_CATEGORIES = frozenset({'token_cost', 'gpu_rental'})


def drain_requested():
    path=get_settings().storage_dir/'.brief-worker-drain'
    if not path.exists():return False
    try:return json.loads(path.read_text())['expires_at']>time.time()
    except (OSError,ValueError,KeyError,TypeError):
        try:return path.stat().st_mtime+1800>time.time()
        except OSError:return False


def heartbeat(state='idle',job_id=None):
    from .sources import save
    save(get_settings().storage_dir/'.brief-worker-heartbeat.json',
         {'at':utcnow().isoformat(),'state':state,'job_id':str(job_id) if job_id else None,'draining':drain_requested()})


def public_read_requested(job) -> bool:
    """Only an editor's Generate action reads the public compute pages."""
    return (job.category in PUBLIC_COMPUTE_CATEGORIES and job.action == 'produce'
            and job.created_by is not None and getattr(job, 'manual_capture', None) is None)


def capture_public_for_job(factory, job_id, out):
    from .public_compute import fetch_visible
    from .public_compute_store import store_visible,store_visible_history,restore_workbook_history
    from .sources import save

    with factory() as db:
        job = db.get(BriefJob, job_id)
        background=job.action=='reconcile' and job.category in PUBLIC_COMPUTE_CATEGORIES
        if not public_read_requested(job) and not background:
            return None
        category, issue_day, team_id = job.category, job.issue_date, job.team_id
    target_day = expected_date(category, issue_day) if category == 'gpu_rental' else None
    partial={'category':category,'readings':[],'history':{},'captured_at':utcnow().isoformat()}
    def page_captured(metric,reading,history):
        partial['readings'].append(reading);partial['history'][metric]=history
        partial['data_date']=max(row['date'] for row in partial['readings'])
        save(out/'public-compute-read.json',partial)
    capture = fetch_visible(category, today=datetime.now(CHINA).date(),
                            target_date=target_day,on_page=page_captured,
                            allow_missing_week=True)
    save(out/'public-compute-read.json',capture)
    source_day = date.fromisoformat(capture['data_date'])
    if target_day is not None and source_day != target_day and not background:
        raise ValueError(f'GPU公开数据日{source_day}与目标数据日{target_day}不一致')
    if source_day > issue_day and not background:
        raise ValueError('网页数据日晚于所选生产日，请选择当前生产日后再生成')
    if (datetime.now(CHINA).date() - source_day).days > 7 and not background:
        raise ValueError(f'公开网页仍停留在{source_day}，超过七日；为避免旧稿，本次停止生成')
    with factory() as db:
        restored_workbook=restore_workbook_history(db,team_id,before=capture['data_date'])
        historical=store_visible_history(db,team_id,capture)
        result = (store_visible(db, team_id, capture,allow_historical=background) if capture.get('readings')
                  else {'status':'historical_prices_only'})
        result['historical']=historical
        result['workbook_dates_restored']=restored_workbook
        db.commit()
        if not background:
            from app.db.models import AIComputeObservation
            observed=db.scalar(select(AIComputeObservation).where(AIComputeObservation.team_id==team_id,
                AIComputeObservation.kind==('gpu' if category=='gpu_rental' else 'token'),
                AIComputeObservation.date==source_day))
            fields=('a100','h100','b200') if category=='gpu_rental' else ('token_price',)
            if observed is None or any(getattr(observed,key) is None for key in fields):
                raise ValueError('目标日价格尚未齐全；可取得的真实历史已入库，不以较早日期冒充')
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
    if drain_requested():return 0
    now=now or datetime.now(CHINA)
    if not production_window(now):return 0
    local=now.astimezone(CHINA);count=0
    with factory() as db:
        for config in db.scalars(select(BriefConfig).where(BriefConfig.enabled.is_(True))):
            for category in AUTO_CATEGORIES:
                if local.time().replace(tzinfo=None)<first_run(category):continue
                from .trading_calendar import CalendarUnavailable
                try:target=expected_date(category,local.date())
                except CalendarUnavailable:continue
                issue=find_issue(db,config.team_id,local.date(),category)
                if issue and issue.version:
                    if category!='oil_etf':continue
                    current=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version))
                    if current is None or current.kind=='edited':continue
                    # The issuer can update after the first successful latest-value
                    # run. Recheck once per 20-minute slot without duplicate drafts.
                    cycle='latest-'+local.strftime('%H')+'-'+str(local.minute//20)
                    existing=db.scalar(select(BriefJob.id).where(BriefJob.team_id==config.team_id,
                        BriefJob.issue_date==local.date(),BriefJob.category==category,BriefJob.dedupe_key.endswith(':'+cycle)))
                    if not existing:
                        enqueue(db,config.team_id,local.date(),category,action='refresh',cycle=cycle);count+=1
                    continue
                delivered=db.scalar(select(BriefVersion.id).join(BriefIssue,BriefVersion.issue_id==BriefIssue.id).where(
                    BriefIssue.team_id==config.team_id,BriefIssue.category==category,
                    BriefVersion.number==BriefIssue.version,
                    BriefVersion.payload['data_date'].as_string()==target.isoformat()).limit(1))
                if delivered:continue
                enqueue(db,config.team_id,local.date(),category);count+=1
        db.commit()
    return count


def claim(factory,now=None):
    if drain_requested():return None
    now=now or utcnow()
    with factory() as db:
        # Main loop has one advisory-locked runner; expired jobs survive restarts.
        db.execute(update(BriefJob).where(BriefJob.status=='running',BriefJob.lease_until<now)
                   .values(status='queued',error='上次运行中断，已重新排队',lease_until=None))
        row=db.scalars(select(BriefJob).where(BriefJob.status.in_(['queued','waiting']),
            BriefJob.next_attempt_at<=now).order_by(
                case((BriefJob.category=='liquidity',0),else_=1),
                BriefJob.next_attempt_at,BriefJob.created_at)
            .with_for_update(skip_locked=True).limit(1)).first()
        if not row:db.commit();return None
        if row.action!='reconcile' and row.created_by is None and not row.preview and (row.issue_date!=now.astimezone(CHINA).date() or not production_window(now)):
            row.status='skipped';row.active_key=None;row.error='已超过工作日上午生产窗口';row.updated_at=now;db.commit();return None
        row.status='running';row.attempts+=1;row.lease_until=now+timedelta(minutes=7);row.updated_at=now
        db.commit();return row.id


def schedule_reconciliation(factory,now=None):
    """One queued repair per team; bounded cycles, no AI and no draft overwrite."""
    from .trading_calendar import CalendarUnavailable
    now=now or datetime.now(CHINA);local=now.astimezone(CHINA)
    if local.hour<10:return 0  # Morning production keeps queue priority.
    cycle=local.strftime('%Y%m%d')+'-'+str(local.hour//6)
    count=0
    with factory() as db:
        for config in db.scalars(select(BriefConfig).where(BriefConfig.data_enabled.is_(True))):
            attempts=db.scalar(select(func.count()).select_from(BriefJob).where(BriefJob.team_id==config.team_id,
                BriefJob.action=='reconcile',BriefJob.dedupe_key.endswith(':'+cycle)))
            if attempts>=18:continue
            if db.scalar(select(BriefJob.id).where(BriefJob.team_id==config.team_id,
                BriefJob.status.in_(['queued','waiting','running'])).limit(1)):continue
            # Current sources are rechecked after success. Older missing/failed
            # editions receive bounded catch-up attempts across subsequent days.
            queued=False;seen=set()
            for offset in range(8):
                issue_day=local.date()-timedelta(days=offset)
                for category in CATEGORIES:
                    try:target=expected_date(category,issue_day)
                    except CalendarUnavailable:continue
                    if (category,target) in seen:continue
                    seen.add((category,target))
                    latest=db.scalar(select(BriefDataCheck).where(BriefDataCheck.team_id==config.team_id,
                        BriefDataCheck.category==category,BriefDataCheck.data_date==target).order_by(
                        BriefDataCheck.created_at.desc()).limit(1))
                    if offset and latest and latest.report.get('current_data_valid'):continue
                    key=f'{config.team_id}:{issue_day}:{category}:reconcile:production:{cycle}'
                    if db.scalar(select(BriefJob.id).where(BriefJob.dedupe_key==key)):continue
                    enqueue(db,config.team_id,issue_day,category,action='reconcile',cycle=cycle)
                    count+=1;queued=True;break
                if queued:break
        db.commit()
    return count


def execute(factory,id):
    global _active_process
    heartbeat('running',id)
    settings=get_settings()
    with factory() as db:
        job=db.get(BriefJob,id)
        if not job or job.status!='running':return
        team_id=job.team_id;category=job.category;preview=job.preview;day=job.issue_date;action=job.action
        # Scheduled jobs have no requesting user; manual jobs keep their editor id.
        automatic=job.created_by is None and not preview
        scheduled_retry=job.schedule_origin if job.schedule_origin is not None else automatic
        requested_public_read=public_read_requested(job)
        requested_manual_read=(category in PUBLIC_COMPUTE_CATEGORIES and action=='produce'
                               and job.manual_capture is not None)
        team=db.get(Team,team_id)
        ai=briefs_ai(team)
        team_settings=dict(team.settings or {})
        starting_issue=find_issue(db,team_id,day,category)
        starting_version=starting_issue.version if starting_issue else 0
        render_source_id=None;render_payload=None
        render_version_changed = False
        followup=category=='oil_etf' and ':latest-' in job.dedupe_key and scheduled_retry
        generation_base=None;generation_payload=None
        if followup:
            issue=find_issue(db,team_id,day,category)
            current=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version)) if issue else None
            if current:generation_base=current.id;generation_payload=edition_payload(current)
        if action=='render':
            render_version_changed = job.render_base_version is not None and job.render_base_version != starting_version
            if starting_version and not render_version_changed:
                selected=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==starting_issue.id,BriefVersion.number==starting_version))
                if selected:
                    render_source_id=selected.id;render_payload=edition_payload(selected)
        config=db.get(BriefConfig,team_id)
        category_notes=(config.notes or {}).get(category,default_notes(category)) if config else default_notes(category)
        # A refresh keeps this edition's editable notes. Pass the same notes to
        # the renderer now, rather than restoring only the payload after drawing.
        if action in ('refresh','ai'):
            note_issue=find_issue(db,team_id,day,category)
            note_version=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==note_issue.id,
                BriefVersion.number==note_issue.version)) if note_issue else None
            if note_version and 'fixed_notes' in note_version.payload:
                category_notes=note_version.payload['fixed_notes']
        if category in PUBLIC_COMPUTE_CATEGORIES | {'liquidity'} and not str(category_notes or '').strip():
            category_notes=default_notes(category)
    root=root_for(team_id);out=root/'runs'/str(id);out.mkdir(parents=True,exist_ok=True)
    if action == 'render' and render_payload is None:
        error = '稿件已更新，请基于最新版本重新制图' if render_version_changed else '尚无可重绘稿件，请先生成'
        with factory() as db:
            failed=db.get(BriefJob,id);failed.status='failed';failed.active_key=None
            failed.lease_until=None;failed.error=error;failed.updated_at=utcnow();db.commit()
        record(out,1,error);return
    try:target_day=expected_date(category,day)
    except ValueError as exc:
        with factory() as db:
            failed=db.get(BriefJob,id);failed.status='failed';failed.error=str(exc)
            failed.active_key=None;failed.lease_until=None;db.commit()
        record(out,1,str(exc));return
    if action=='render':
        from .sources import save
        target_day=date.fromisoformat(render_payload['data_date'])
        render_payload['render_source_version_id']=str(render_source_id)
        save(out/'render-input.json',render_payload)
    record(out,1,f'任务开始：生产日 {day}，目标数据日 {target_day}')
    restored=0
    try:
        with factory() as db:
            from .repair import recover_snapshots,restore_working_history
            if action!='render':
                recover_snapshots(db,team_id,category,out/'recovered-evidence')
                restored=restore_working_history(db,team_id,category,root/'history'/category)
            db.commit()
    except (ValueError,OSError) as exc:
        from .sources import save
        save(out/'history-restore-error.json',{'error':str(exc),'fresh_acquisition_continues':True})
    if restored:record(out,1,f'已从数据库恢复{restored}条真实历史记录')
    if action=='reconcile' and category in PUBLIC_COMPUTE_CATEGORIES:
        # Collect and backfill observations automatically; generating or adopting
        # text/images for these two categories remains an editor-only action.
        try:
            capture=capture_public_for_job(factory,id,out)
            from .data_reliability import record_run
            from .sources import save
            from app.db.models import AIComputeObservation
            with factory() as db:
                observed=list(db.scalars(select(AIComputeObservation).where(
                    AIComputeObservation.team_id==team_id,
                    AIComputeObservation.kind=='gpu' if category=='gpu_rental' else AIComputeObservation.kind=='token').order_by(
                    AIComputeObservation.date)))
                rows=[{'date':row.date.isoformat(),'token_price':row.token_price,'a100':row.a100,
                    'h100':row.h100,'b200':row.b200,'status':row.source_status,
                    'public_display':row.public_display,'manual_display':row.manual_display} for row in observed]
                save(out/'ai-compute-source.json',rows)
                fields=('a100','h100','b200') if category=='gpu_rental' else ('token_price',)
                matched=next((row for row in rows if row['date']==target_day.isoformat()),None)
                complete=bool(matched and all(matched[key] is not None for key in fields))
                payload={'data_date':target_day.isoformat(),'valid':complete,'source_latest_date':capture['data_date']}
                current_job=db.get(BriefJob,id)
                report=record_run(db,current_job,out,payload,
                    None if complete else '目标日尚未发布；本次已补回来源能提供的真实历史')
                archive_run(db,current_job,out,data_date=target_day.isoformat())
                from .data_reliability import link_sources
                link_sources(db,current_job)
                current_job.status='succeeded' if complete else 'failed'
                current_job.error=None if complete else report['error']
                current_job.active_key=None;current_job.lease_until=None;db.commit()
            record(out,6 if complete else 2,'历史数据已补存；未自动生成或发布稿件' if complete else report['error'])
        except Exception as exc:
            with factory() as db:
                failed=db.get(BriefJob,id);failed.status='failed';failed.error=str(exc)[:500]
                from .data_reliability import record_run
                record_run(db,failed,out,None,str(exc))
                archive_run(db,failed,out,data_date=target_day.isoformat())
                failed.active_key=None;failed.lease_until=None;db.commit()
            record(out,2,'公开历史补抓未完成：'+str(exc)[:300])
        return
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
                from .data_reliability import record_run,link_sources
                record_run(db,failed,out,None,message)
                archive_run(db,failed,out,data_date=target_day.isoformat())
                link_sources(db,failed)
                db.commit()
            record(out,2,'公开网页读取未完成：'+message[:500])
            return
    if category=='valuation' and action in ('produce','refresh','reconcile'):
        with factory() as db:
            from .source_archive import restore_daily_pe,restore_all_a_capture
            from .trading_calendar import sessions,previous_session
            restored_cap=restore_all_a_capture(db,db.get(BriefJob,id),out,target_day.isoformat(),
                previous_session('xshg',target_day).isoformat())
            recovered_daily=restore_daily_pe(db,db.get(BriefJob,id),out,target_day.isoformat(),
                required_dates=sessions('xshg',target_day-timedelta(days=92),target_day))
            if recovered_daily:
                from .repair import restore_working_history
                restore_working_history(db,team_id,category,root/'history'/category)
            restored=restore_pe_capture(db,db.get(BriefJob,id),out,
                                        expected_date(category,day).isoformat())
            db.commit()
            if restored_cap:record(out,1,'已核验数据库中相同两日的完整市值原始分页，作为网络故障时的复用来源')
            if restored:log.info('Restored dated PE evidence from PostgreSQL for job %s',id)
            if restored:record(out,1,'已恢复经哈希核验的历史PE证据；仍需检查目标日及其前一交易日是否齐全')
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
    env['DATA_COLLECTION_ONLY']='1' if action=='reconcile' else '0'
    old_result=out/'result.json'
    if job.attempts>1 and old_result.exists():
        old_result.replace(out/f'prior-result-{job.attempts}-{time.time_ns()}.json')
    command=[sys.executable,'-m','app.briefs.runner','--category',category,'--date',expected_date(category,day).isoformat(),
             '--root',str(root),'--run-id',str(id),'--action','refresh' if action=='reconcile' or automatic and action=='produce' else action]
    if action=='render':command.extend(['--render-input',str(out/'render-input.json')])
    process=subprocess.Popen(command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=os.name!='nt')
    _active_process=process
    cutoff=time.monotonic()+360
    error=None;retryable=True
    while process.poll() is None:
        heartbeat('running',id)
        if time.monotonic()>cutoff or (automatic and action!='reconcile' and not production_window(datetime.now(CHINA))):
            if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
            process.wait();error='任务超时或自动任务已到10:00截止时间';break
        time.sleep(1)
    _active_process=None
    result=out/'result.json'
    # If AI times out, the already saved complete rule draft is still usable.
    payload=json.loads(result.read_text(encoding='utf-8')) if result.exists() else None
    if not payload and (out/'error.json').exists():
        report=json.loads((out/'error.json').read_text(encoding='utf-8'));error=report['message']
        retryable=report.get('retryable',True)
    if action!='reconcile' and payload and ai.get('provider')=='chat2api':
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
    duplicate_current=False
    if followup and payload and payload.get('valid') and generation_payload:
        from .product_inputs import publication_digest
        previous_folder=root/generation_payload['artifact_dir']
        current_digest=publication_digest(category,out,payload)
        duplicate_current=bool(current_digest and current_digest==publication_digest(category,previous_folder,generation_payload))
    if payload and payload.get('valid') and action not in ('render','reconcile') and not duplicate_current:
        from .body_templates import apply as apply_body_template
        from .skill_production import apply_skill_copy
        apply_body_template(payload,category,team_settings)
        apply_skill_copy(payload,category,team_settings)
        if (payload.get('editorial') or {}).get('status')=='active':
            editorial=payload['editorial']
            method=(editorial.get('candidate') or {}).get('draft_method')
            label={'bound_prose':'AI基于核验事实写稿','facts_fallback':'完整事实稿回退'}.get(method)
            if label is None:
                label='AI选择表达模块' if editorial.get('selection_status')=='model_selected' else '完整固定组合'
            record(out,4,'样本风格文案已采用：'+label)
        elif payload.get('skill'):
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
        if ai.get('configuration_error'):
            payload.setdefault('warnings',[]).append('AI密钥无法解密，已保留完整风格规则稿；请管理员重新配置该接入')
        # The date cache deliberately keeps the validated pre-Skill rule product;
        # this run's durable result must match what is committed as the final edition.
        save(out/'result.json',payload)
    with factory() as db:
        job=db.get(BriefJob,id);job.lease_until=None;job.updated_at=utcnow()
        render_issue=None
        if action=='render':
            current=find_issue(db,team_id,day,category)
            render_issue=owned(db,team_id,current.id,True) if current else None
            latest=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==render_issue.id,
                BriefVersion.number==render_issue.version)) if render_issue else None
            if latest is None or latest.id!=render_source_id:
                job.status='failed';job.active_key=None;job.error='稿件已更新，请基于最新版本重新制图'
                db.commit();record(out,5,job.error);return
        stopped_step=read(out).get('step',1)
        record(out,5 if payload and payload.get('valid') else stopped_step,'正在归档原始数据、图片快照与本次运行结果')
        try:
            from .data_reliability import record_run
            data_report=record_run(db,job,out,payload,error)
            if payload and payload.get('valid'):
                from .sources import save
                save(out/'result.json',payload)
            source_dir=None
            if payload and payload.get('artifact_dir'):
                source_dir=(root/payload['artifact_dir']).resolve()
                if not source_dir.is_relative_to(root.resolve()):
                    raise ValueError('被复用的来源快照路径不安全')
            source_archive=archive_run(db,job,out,
                                       source_dir=source_dir,
                                       data_date=payload.get('data_date') if payload else None)
            from .data_reliability import link_sources
            link_sources(db,job)
            if payload and payload.get('valid') and category == 'valuation' and action!='render':
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
        if action=='reconcile':
            job.status='succeeded' if payload and payload.get('valid') else 'failed'
            job.error=None if job.status=='succeeded' else (error or '数据补查未完成，保留缺口等待后续周期')
            job.active_key=None;db.commit()
            record(out,6,'数据补查完成；未覆盖已编辑或已确认稿件' if job.status=='succeeded' else job.error)
            return
        if followup and payload and payload.get('valid'):
            current_issue=find_issue(db,team_id,day,category)
            locked=owned(db,team_id,current_issue.id,True) if current_issue else None
            current=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==locked.id,BriefVersion.number==locked.version)) if locked else None
            if duplicate_current or current is None or current.id!=generation_base or current.kind=='edited':
                job.status='succeeded';job.active_key=None;job.error=None;db.commit()
                record(out,6,'发行方数据复查完成；保留当前稿件，未覆盖编辑成果' if not duplicate_current else '发行方数据未变化，保留当前稿件')
                return
        if payload and payload.get('valid'):
            if automatic and not production_window(datetime.now(CHINA)):
                job.status='skipped';job.active_key=None;job.error='超过10:00，快照保留但未生成当天稿';db.commit();record(out,5,job.error);return
            issue=render_issue or ensure_issue(db,team_id,day,category);issue=owned(db,team_id,issue.id,True)
            if action!='render' and issue.version!=starting_version:
                job.status='failed';job.active_key=None
                job.error='生成期间稿件已更新；已保留当前稿件和本次来源，请核对后重新生成'
                db.commit();record(out,5,job.error);return
            if action not in ('produce','render') and issue.version:
                previous=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id,BriefVersion.number==issue.version)).first()
                if previous and 'fixed_notes' in previous.payload:payload['fixed_notes']=previous.payload['fixed_notes']
            payload['preview']=preview
            payload['source_archive']=source_archive
            if error:payload.setdefault('warnings',[]).append('规则稿已保存；模型未完成或任务达到时限')
            version=append_version(db,issue,payload,user_id=job.created_by,
                                   kind='rendered' if action=='render' else 'generated')
            db.execute(update(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id==id,
                BriefSourceSnapshot.attempt==job.attempts).values(version_id=version.id))
            job.status='succeeded';job.active_key=None;job.error=None
        else:
            job.error=error or '未生成完整结果，来源可能尚未更新'
            slot=next_slot(category,datetime.now(CHINA)) if scheduled_retry and retryable else None
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
    from .worker_lock import WorkerLock
    with WorkerLock(engine,get_settings().storage_dir/'.brief-worker.lock',abort_on_lock_loss) as guard:
        while True:
            guard.check()
            try:
                heartbeat('idle')
                if drain_requested():time.sleep(5);continue
                schedule(factory);schedule_reconciliation(factory);id=claim(factory)
                if id:execute(factory,id);continue
            except Exception:log.exception('Brief worker iteration failed')
            time.sleep(30)


if __name__=='__main__':main()
