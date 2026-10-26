"""Brief editions and immutable revisions, isolated from daily-report materials."""
import hashlib
import json
import shutil
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.services.copy_dates import copy_dates
from app.db.models import BriefAudit, BriefConfig, BriefIssue, BriefJob, BriefVersion, BriefSourceSnapshot, Team, utcnow
from .delivery import complete_body, default_notes
from .policy import CATEGORIES, expected_date


def root_for(team_id):
    return get_settings().storage_dir / str(team_id) / 'briefs'


def owned(db, team_id, issue_id, lock=False):
    query=select(BriefIssue).where(BriefIssue.id==issue_id,BriefIssue.team_id==team_id)
    if lock:query=query.with_for_update()
    return db.scalars(query).first()


def find_issue(db,team_id,day,category):
    query=select(BriefIssue).where(BriefIssue.team_id==team_id,BriefIssue.issue_date==day,BriefIssue.category==category)
    return db.scalars(query).first()


def ensure_issue(db,team_id,day,category):
    item=find_issue(db,team_id,day,category)
    if item:return item
    try:
        with db.begin_nested():
            db.add(BriefIssue(team_id=team_id,issue_date=day,category=category));db.flush()
    except IntegrityError:pass
    return find_issue(db,team_id,day,category)


def enqueue(db,team_id,day,category,*,action='produce',preview=False,user_id=None,unique=False,manual_capture=None):
    key=f'{team_id}:{day}:{category}:{action}:{"preview" if preview else "production"}'
    active_key=f'{team_id}:{day}:{category}'
    # A failed explicit retry is a new attempt; active duplicate clicks reuse a job.
    active=db.scalars(select(BriefJob).where(BriefJob.team_id==team_id,BriefJob.issue_date==day,
        BriefJob.category==category,
        BriefJob.status.in_(['queued','running','waiting']))).first()
    if active:
        # A manual click takes a queued automatic retry out of the morning schedule.
        if user_id is not None and active.created_by is None and active.status in ('queued','waiting'):
            active.created_by=user_id;active.preview=preview
            active.status='queued';active.next_attempt_at=utcnow()
            db.flush()
        return active
    if unique:key+=':'+uuid.uuid4().hex
    previous=db.scalars(select(BriefJob).where(BriefJob.dedupe_key==key)).first()
    if previous:return previous
    try:
        with db.begin_nested():
            item=BriefJob(team_id=team_id,issue_date=day,category=category,action=action,
                          preview=preview,dedupe_key=key,active_key=active_key,created_by=user_id,
                          manual_capture=manual_capture)
            db.add(item);db.flush()
        ensure_issue(db,team_id,day,category)
        return item
    except IntegrityError:
        return db.scalars(select(BriefJob).where((BriefJob.active_key==active_key)|(BriefJob.dedupe_key==key))).first()


def append_version(db,issue,payload,*,user_id=None,kind='generated',title=None,body=None):
    # Lock is always acquired by caller before allocating a revision number.
    payload=dict(payload)
    if 'fixed_notes' not in payload:
        config=db.get(BriefConfig,issue.team_id)
        payload['fixed_notes']=(config.notes or {}).get(issue.category,default_notes(issue.category)) if config else default_notes(issue.category)
    issue.version+=1;issue.updated_at=utcnow()
    item=BriefVersion(issue_id=issue.id,number=issue.version,kind=kind,
        title=copy_dates(title or payload['title']),dynamic_body=copy_dates(body if body is not None else payload['dynamic_body']),
        payload=payload,created_by=user_id)
    db.add(item);db.add(BriefAudit(issue_id=issue.id,action=kind,version=issue.version,user_id=user_id))
    db.flush();prune_versions(db,issue);return item


def prune_versions(db,issue):
    """Keep the latest two revisions plus the confirmed one; never drop a confirmation."""
    versions=list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number.desc())))
    keep={version.number for version in versions[:2]}
    if issue.confirmed_version: keep.add(issue.confirmed_version)
    for old in versions:
        if old.number in keep: continue
        # Notes remain reproducible without retaining extra downloadable editions.
        team=db.get(Team,issue.team_id,with_for_update=True)
        saved=dict(team.settings or {})
        notes=list(saved.get('retired_issue_notes',[]))
        notes.append({'issue_id':str(issue.id),'version':old.number,'notes':(old.payload or {}).get('fixed_notes',''),'created_at':old.created_at.isoformat()})
        team.settings={**saved,'retired_issue_notes':notes}
        from app.services.business_history import record
        record(db,team)
        db.add(BriefAudit(issue_id=issue.id,action='retired_notes',version=old.number,user_id=old.created_by))
        db.execute(update(BriefSourceSnapshot).where(BriefSourceSnapshot.version_id==old.id).values(version_id=None))
        if issue.confirmed_version==old.number:
            issue.confirmed_version=None;issue.confirmed_by=None;issue.confirmed_at=None
        delivery=root_for(issue.team_id)/'delivery'/str(old.id)
        if delivery.is_dir(): shutil.rmtree(delivery, ignore_errors=True)
        db.delete(old)


def version_out(issue,version):
    data=version.payload or {}
    notes=data.get('fixed_notes',default_notes(issue.category))
    ai=dict(data.get('deepseek',{'status':'not_configured'}))
    for key in ('title','body'):
        if key in ai:ai[key]=copy_dates(ai[key])
    if ai.get('source_body_sha'):
        ai['stale']=ai['source_body_sha']!=hashlib.sha256(version.dynamic_body.encode()).hexdigest()
    elif ai.get('status')=='ok':
        # Older candidates predate source tracking; never let them silently overwrite an edit.
        ai['untracked']=True
    # These categories keep notes on the chart. Token CPI and GPU rental are included
    # so the draft body stays dynamic-only; drawing those two footers is separate.
    image_notes=issue.category in ('valuation','turnover','us_barometer','token_cost','gpu_rental')
    body=complete_body(issue.category,copy_dates(version.dynamic_body),notes)
    return {'number':version.number,'kind':version.kind,'title':copy_dates(version.title),
        'dynamic_body':body if image_notes else copy_dates(version.dynamic_body),'body':body,
        'fixed_notes':notes,'notes_placement':'image' if image_notes else 'body','created_at':version.created_at,
        'data_date':data.get('data_date'),'warnings':data.get('warnings',[]),
        'facts':data.get('facts',{}),'deepseek':ai,
        'images':[{'name':x,'url':f'/api/briefs/issues/{issue.id}/files/{version.number}/{x}'} for x in data.get('images',[])],
        'preview':data.get('preview',True),'valid':data.get('valid',False)}


def issue_out(db,issue):
    versions=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number.desc())).all()
    jobs=db.scalars(select(BriefJob).where(BriefJob.team_id==issue.team_id,BriefJob.category==issue.category,
        BriefJob.issue_date==issue.issue_date).order_by(BriefJob.created_at.desc())).all()
    return {'id':str(issue.id),'category':issue.category,'label':CATEGORIES[issue.category],
        'issue_date':issue.issue_date,'expected_date':expected_date(issue.category,issue.issue_date),
        'version':issue.version,'confirmed_version':issue.confirmed_version,'confirmed_at':issue.confirmed_at,
        'versions':[version_out(issue,v) for v in versions],
        'jobs':[job_out(j) for j in jobs[:15]]}


def job_out(item):
    from .progress import for_job
    return {'id':str(item.id),'category':item.category,'issue_date':item.issue_date,'action':item.action,
        'preview':item.preview,'status':item.status,'attempts':item.attempts,'error':item.error,
        'next_attempt_at':item.next_attempt_at,'created_at':item.created_at,'updated_at':item.updated_at,
        'progress':for_job(item,root_for(item.team_id))}


def artifact_path(issue,version,name):
    data=version.payload
    if name not in data.get('images',[]):raise ValueError('未登记的文件')
    root=root_for(issue.team_id).resolve()
    folder=(root/data['artifact_dir']).resolve()
    path=(folder/name).resolve()
    if not path.is_relative_to(root) or path.parent!=folder or not path.is_file():raise ValueError('图片文件不存在')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=data['image_sha256'][name]:raise ValueError('图片校验失败')
    return path


def delivered_image(issue,version,name):
    from app.services.image_delivery import delivery_signature, WATERMARK, MAX_BYTES, store_image
    source=artifact_path(issue,version,name)
    # Token CPI and GPU rental notes stay in fixed_notes for the chart pipeline.
    # This download path does not paint them onto the image.
    notes=version.payload.get('fixed_notes',default_notes(issue.category)) if issue.category in ('valuation','turnover','us_barometer') else ''
    # Earlier us-barometer editions kept two images; notes belong only on the merged chart.
    if issue.category=='us_barometer' and name!='美股晴雨表.png':notes=''
    policy=delivery_signature() + ('-turnover-notes-1' if issue.category=='turnover' else '-us-barometer-merged-1' if issue.category=='us_barometer' else '')
    key=hashlib.sha256(json.dumps([version.payload['image_sha256'][name],notes,issue.category,
        version.payload.get('unified_valuation',False),
        policy,hashlib.sha256(WATERMARK.read_bytes()).hexdigest()],ensure_ascii=False).encode()).hexdigest()
    target=root_for(issue.team_id)/'delivery'/str(version.id)/key
    for suffix,media in (('.png','image/png'),('.jpg','image/jpeg')):
        cached=target.with_suffix(suffix)
        if cached.is_file() and 0<cached.stat().st_size<=MAX_BYTES:return cached,media
    return store_image(source,target,notes=notes,dark=issue.category in ('valuation','turnover','us_barometer'),
                       remove_valuation_source=issue.category=='valuation' and not version.payload.get('unified_valuation'),
                       reuse_footer=issue.category=='liquidity',
                       turnover_snapshot=source.with_name('source.json') if issue.category=='turnover'
                       and not version.payload.get('turnover_watermark_removed') else None)
