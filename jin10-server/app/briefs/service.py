"""Brief editions and immutable revisions, isolated from daily-report materials."""
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.models import BriefAudit, BriefIssue, BriefJob, BriefVersion, utcnow
from .delivery import complete_body, FIXED_NOTES
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


def enqueue(db,team_id,day,category,*,action='produce',preview=False,user_id=None,unique=False):
    key=f'{team_id}:{day}:{category}:{action}:{"preview" if preview else "production"}'
    active_key=f'{team_id}:{day}:{category}'
    # A failed explicit retry is a new attempt; active duplicate clicks reuse a job.
    active=db.scalars(select(BriefJob).where(BriefJob.team_id==team_id,BriefJob.issue_date==day,
        BriefJob.category==category,
        BriefJob.status.in_(['queued','running','waiting']))).first()
    if active:return active
    if unique:key+=':'+uuid.uuid4().hex
    previous=db.scalars(select(BriefJob).where(BriefJob.dedupe_key==key)).first()
    if previous:return previous
    try:
        with db.begin_nested():
            item=BriefJob(team_id=team_id,issue_date=day,category=category,action=action,
                          preview=preview,dedupe_key=key,active_key=active_key,created_by=user_id)
            db.add(item);db.flush()
        ensure_issue(db,team_id,day,category)
        return item
    except IntegrityError:
        return db.scalars(select(BriefJob).where((BriefJob.active_key==active_key)|(BriefJob.dedupe_key==key))).first()


def append_version(db,issue,payload,*,user_id=None,kind='generated',title=None,body=None):
    # Lock is always acquired by caller before allocating a revision number.
    issue.version+=1;issue.updated_at=utcnow()
    item=BriefVersion(issue_id=issue.id,number=issue.version,kind=kind,
        title=title or payload['title'],dynamic_body=body if body is not None else payload['dynamic_body'],
        payload=payload,created_by=user_id)
    db.add(item);db.add(BriefAudit(issue_id=issue.id,action=kind,version=issue.version,user_id=user_id))
    db.flush();return item


def version_out(issue,version):
    data=version.payload or {}
    return {'number':version.number,'kind':version.kind,'title':version.title,
        'dynamic_body':version.dynamic_body,'body':complete_body(issue.category,version.dynamic_body),
        'fixed_notes':FIXED_NOTES.get(issue.category,''),'created_at':version.created_at,
        'data_date':data.get('data_date'),'warnings':data.get('warnings',[]),
        'facts':data.get('facts',{}),'deepseek':data.get('deepseek',{'status':'not_configured'}),
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
    return {'id':str(item.id),'category':item.category,'issue_date':item.issue_date,'action':item.action,
        'preview':item.preview,'status':item.status,'attempts':item.attempts,'error':item.error,
        'next_attempt_at':item.next_attempt_at,'updated_at':item.updated_at}


def artifact_path(issue,version,name):
    data=version.payload
    if name not in data.get('images',[]):raise ValueError('未登记的文件')
    root=root_for(issue.team_id).resolve()
    folder=(root/data['artifact_dir']).resolve()
    path=(folder/name).resolve()
    if not path.is_relative_to(root) or path.parent!=folder or not path.is_file():raise ValueError('图片文件不存在')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=data['image_sha256'][name]:raise ValueError('图片校验失败')
    return path
