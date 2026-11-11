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
from .delivery import IMAGE_NOTE_CATEGORIES, complete_body, default_notes
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


def enqueue(db,team_id,day,category,*,action='produce',preview=False,user_id=None,unique=False,manual_capture=None,cycle=None,render_base_version=None):
    key=f'{team_id}:{day}:{category}:{action}:{"preview" if preview else "production"}'
    if cycle is not None:key+=':'+str(cycle)
    active_key=f'{team_id}:{day}:{category}'
    # A failed explicit retry is a new attempt; active duplicate clicks reuse a job.
    active=db.scalars(select(BriefJob).where(BriefJob.team_id==team_id,BriefJob.issue_date==day,
        BriefJob.category==category,
        BriefJob.status.in_(['queued','running','waiting']))).first()
    if active:
        # A manual click takes a queued automatic retry out of the morning schedule.
        if user_id is not None and (active.status=='waiting' or active.created_by is None and active.status=='queued'):
            if active.schedule_origin is None:active.schedule_origin=active.created_by is None and not active.preview
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
                          schedule_origin=user_id is None and not preview and action!='reconcile',
                          manual_capture=manual_capture,render_base_version=render_base_version)
            db.add(item);db.flush()
        ensure_issue(db,team_id,day,category)
        return item
    except IntegrityError:
        return db.scalars(select(BriefJob).where((BriefJob.active_key==active_key)|(BriefJob.dedupe_key==key))).first()


def append_version(db,issue,payload,*,user_id=None,kind='generated',title=None,body=None):
    # Lock is always acquired by caller before allocating a revision number.
    payload=dict(payload)
    payload['title']=copy_dates(title if title is not None else payload['title'])
    payload['dynamic_body']=copy_dates(body if body is not None else payload['dynamic_body'])
    if 'fixed_notes' not in payload:
        config=db.get(BriefConfig,issue.team_id)
        payload['fixed_notes']=(config.notes or {}).get(issue.category,default_notes(issue.category)) if config else default_notes(issue.category)
    issue.version+=1;issue.updated_at=utcnow()
    item=BriefVersion(issue_id=issue.id,number=issue.version,kind=kind,
        title=payload['title'],dynamic_body=payload['dynamic_body'],
        payload=payload,created_by=user_id)
    db.add(item);db.add(BriefAudit(issue_id=issue.id,action=kind,version=issue.version,user_id=user_id))
    db.flush();prune_versions(db,issue);return item


def edition_payload(version):
    """The stored editorial columns are authoritative, including legacy edits."""
    from copy import deepcopy
    payload=deepcopy(version.payload or {})
    payload.update(title=copy_dates(version.title),dynamic_body=copy_dates(version.dynamic_body))
    return payload


def prepare_chart_notes_revision(issue, previous, notes):
    """Render edited footer notes into a new immutable image for this edition."""
    from .ai_compute_charts import gpu_chart, token_chart

    images = previous.payload.get('images') or []
    if len(images) != 1:
        raise ValueError('本版图表不完整，请重新生成稿件后修改注释')
    source_image = artifact_path(issue, previous, images[0])
    source_name = 'liquidity-snapshot.json' if issue.category == 'liquidity' else 'ai-compute-source.json'
    source = source_image.parent / source_name
    if not source.is_file():
        raise ValueError('本版缺少原始图表数据，请重新生成稿件后修改注释')
    rows = json.loads(source.read_text(encoding='utf-8'))
    root = root_for(issue.team_id)
    folder = root / 'delivery' / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copy2(source, folder / source.name)
        if issue.category == 'token_cost':
            image = token_chart(rows, folder, notes=notes)
        elif issue.category == 'gpu_rental':
            actual = previous.payload.get('data_date') or ''
            image = gpu_chart([row for row in rows if not actual or row['date'] <= actual], folder, notes=notes)
        elif issue.category == 'liquidity':
            from .engines.liquidity.render import render_poster
            logo = Path(__file__).parent / 'engines/liquidity/assets/plus-original.png'
            image, _, _ = render_poster(rows, folder, logo_path=logo, notes=notes)
        else:
            raise ValueError('该栏目暂不支持重绘图内注释')
        payload = edition_payload(previous)
        payload['fixed_notes'] = payload['chart_notes'] = notes
        payload['artifact_dir'] = folder.relative_to(root).as_posix()
        payload['image_sha256'] = {image.name: hashlib.sha256(image.read_bytes()).hexdigest()}
        return payload, folder
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def prune_versions(db,issue):
    """Keep the latest two revisions plus the confirmed one; never drop a confirmation."""
    versions=list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number.desc())))
    keep={version.number for version in versions[:2]}
    if issue.confirmed_version: keep.add(issue.confirmed_version)
    retired={v.id for v in versions if v.number not in keep}
    root=root_for(issue.team_id).resolve();delivery_root=(root/'delivery').resolve()
    retained=list(db.scalars(select(BriefVersion).join(BriefIssue).where(
        BriefIssue.team_id==issue.team_id,BriefVersion.id.not_in(retired)))) if retired else []
    referenced={(root/(v.payload or {}).get('artifact_dir','')).resolve() for v in retained}
    pending=db.info.get('derived_directory_cleanup',set())
    for folder in referenced:pending.discard((folder,delivery_root))
    for old in versions:
        if old.number in keep: continue
        db.add(BriefAudit(issue_id=issue.id,action='retired_notes',version=old.number,user_id=old.created_by,
            details={'title':old.title,'dynamic_body':old.dynamic_body,
                **{key:(old.payload or {}).get(key) for key in ('data_date','fixed_notes','facts','editorial')},
                'created_at':old.created_at.isoformat()}))
        db.execute(update(BriefSourceSnapshot).where(BriefSourceSnapshot.version_id==old.id).values(version_id=None))
        if issue.confirmed_version==old.number:
            issue.confirmed_version=None;issue.confirmed_by=None;issue.confirmed_at=None
        from app.services.file_cleanup import after_commit_directory
        delivery=(root/(old.payload or {}).get('artifact_dir','')).resolve()
        if delivery!=delivery_root and delivery.is_relative_to(delivery_root) and delivery not in referenced:
            after_commit_directory(db,delivery,delivery_root)
        legacy=delivery_root/str(old.id)
        if legacy!=delivery and legacy not in referenced:after_commit_directory(db,legacy,delivery_root)
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
    # These categories keep notes on the image. The body stays dynamic-only,
    # including earlier editions that still stored the appended note block.
    image_notes=issue.category in IMAGE_NOTE_CATEGORIES
    body=complete_body(issue.category,copy_dates(version.dynamic_body),notes)
    return {'number':version.number,'kind':version.kind,'title':copy_dates(version.title),
        'dynamic_body':body if image_notes else copy_dates(version.dynamic_body),'body':body,
        'fixed_notes':notes,'notes_placement':'image' if image_notes else 'body','created_at':version.created_at,
        'data_date':data.get('data_date'),'warnings':data.get('warnings',[]),
        'data_reliability':data.get('data_reliability'),
        'facts':data.get('facts',{}),'deepseek':ai,
        'editorial':{key:(data.get('editorial') or {}).get(key) for key in
            ('status','mode','reason','writer','selection_status','sample_count','pending_pack_version')},
        'editorial_quality':((data.get('editorial') or {}).get('candidate') or {}).get('style_quality'),
        'skill':{key:(data.get('skill') or {}).get(key) for key in ('status','reason','provider','model','style_status')},
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
        'render_base_version':item.render_base_version,
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
    # AI-compute notes are already drawn in the chart footer. A second delivery
    # band would repeat them under the watermark.
    band_notes=issue.category in ('volume','valuation','turnover','us_barometer','us_structure')
    notes=version.payload.get('fixed_notes',default_notes(issue.category)) if band_notes else ''
    # Earlier us-barometer editions kept two images; notes belong only on the merged chart.
    if issue.category=='us_barometer' and name!='美股晴雨表.png':notes=''
    policy=delivery_signature() + ('-turnover-notes-1' if issue.category=='turnover' else '-us-barometer-merged-1' if issue.category=='us_barometer' else '-us-structure-black-notes-1' if issue.category=='us_structure' else '')
    key=hashlib.sha256(json.dumps([version.payload['image_sha256'][name],notes,issue.category,
        version.payload.get('unified_valuation',False),
        policy,hashlib.sha256(WATERMARK.read_bytes()).hexdigest()],ensure_ascii=False).encode()).hexdigest()
    target=root_for(issue.team_id)/'delivery'/str(version.id)/key
    for suffix,media in (('.png','image/png'),('.jpg','image/jpeg')):
        cached=target.with_suffix(suffix)
        if cached.is_file() and 0<cached.stat().st_size<=MAX_BYTES:return cached,media
    return store_image(source,target,notes=notes,dark=issue.category in ('valuation','turnover','us_barometer','us_structure'),
                       remove_valuation_source=issue.category=='valuation' and not version.payload.get('unified_valuation'),
                       reuse_footer=issue.category=='liquidity',
                       turnover_snapshot=source.with_name('source.json') if issue.category=='turnover'
                       and not version.payload.get('turnover_watermark_removed') else None)
