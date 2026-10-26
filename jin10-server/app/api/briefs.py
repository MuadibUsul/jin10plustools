from __future__ import annotations
import shutil
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.models import BriefAudit, BriefConfig, BriefIssue, BriefJob, BriefVersion, Role, Team, utcnow
from app.services.team_settings import update_notes
from app.deps import CurrentUser, DbSession, Editor
from app.briefs.policy import CATEGORIES, CHINA
from app.briefs.service import owned, enqueue, issue_out, job_out, append_version, artifact_path, delivered_image, prepare_compute_notes_revision
from app.briefs.delivery import complete_body
from app.services.image_delivery import download_name

router=APIRouter(prefix='/api/briefs',tags=['fixed briefs'])


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal[*CATEGORIES]
    issue_date: date
    action: Literal['produce','refresh','render','ai']='produce'


class ManualComputeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal['token_cost', 'gpu_rental']
    issue_date: date
    data_date: date
    token_price: str | None = None
    a100: str | None = None
    h100: str | None = None
    b200: str | None = None
    token_change_pct: str | None = None
    a100_change_pct: str | None = None
    h100_change_pct: str | None = None
    b200_change_pct: str | None = None
    source_note: str = Field(default='', max_length=200)


class EditRequest(BaseModel):
    base_version: int=Field(ge=1)
    title: str=Field(min_length=1,max_length=300)
    dynamic_body: str=Field(min_length=1,max_length=20000)
    fixed_notes: str|None=Field(default=None,max_length=2000)
    notes_as_default: bool=False


class ConfirmRequest(BaseModel):
    version: int=Field(ge=1)


class ConfigRequest(BaseModel):
    enabled: bool


def get_issue(db,user,id,lock=False):
    item=owned(db,user.team_id,id,lock)
    if item is None:raise HTTPException(404,'稿件不存在')
    return item


def visible(db,user,item):
    result=issue_out(db,item)
    if user.role==Role.VIEWER:
        result['versions']=[v for v in result['versions'] if v['number']==item.confirmed_version]
    return result


@router.get('/categories')
def categories(db:DbSession,user:CurrentUser):
    config=db.get(BriefConfig,user.team_id)
    return {'categories':[{'id':key,'label':label,'image_required':key!='oil_etf'} for key,label in CATEGORIES.items()],
            'schedule_enabled':bool(config and config.enabled),'window':'工作日 08:00—10:00（北京时间）',
            'date_policy':'周一取周五，其余取前一工作日；流动性取当天；不维护节假日日历',
            'can_edit':user.role in (Role.ADMIN,Role.EDITOR),'can_configure':user.role==Role.ADMIN}


@router.put('/config')
def config(body:ConfigRequest,db:DbSession,user:Editor):
    if user.role!=Role.ADMIN:raise HTTPException(403,'仅管理员可修改排期')
    item=db.get(BriefConfig,user.team_id)
    if not item:item=BriefConfig(team_id=user.team_id);db.add(item)
    item.enabled=body.enabled;item.updated_at=utcnow()
    from app.services.business_history import record
    record(db,db.get(Team,user.team_id),user.id)
    db.commit()
    return {'enabled':item.enabled}


@router.get('/issues')
def issues(db:DbSession,user:CurrentUser,day:Annotated[date|None,Query(alias='date')]=None):
    day=day or datetime.now(CHINA).date()
    rows=db.scalars(select(BriefIssue).where(BriefIssue.team_id==user.team_id,BriefIssue.issue_date==day)).all()
    return {'date':day,'items':[visible(db,user,x) for x in rows]}


@router.get('/issues/{id}')
def detail(id:uuid.UUID,db:DbSession,user:CurrentUser):
    return visible(db,user,get_issue(db,user,id))


@router.post('/jobs',status_code=202)
def jobs(body:JobRequest,db:DbSession,user:Editor):
    now=datetime.now(CHINA)
    if body.issue_date>now.date():raise HTTPException(422,'不能处理未来日期')
    active=db.scalars(select(BriefJob).where(BriefJob.team_id==user.team_id,
        BriefJob.issue_date==body.issue_date,BriefJob.category==body.category,
        BriefJob.status.in_(['queued','running','waiting']))).first()
    if active and active.action!=body.action:
        names={'produce':'生成稿件','refresh':'重新取数','render':'重新制图','ai':'补跑AI文案'}
        raise HTTPException(409,f"该栏目正在执行{names.get(active.action,active.action)}，请等待当前任务结束后再{names.get(body.action,body.action)}")
    job=enqueue(db,user.team_id,body.issue_date,body.category,action=body.action,
                user_id=user.id,unique=True)
    db.commit();return job_out(job)


@router.post('/manual-compute',status_code=202)
def manual_compute(body:ManualComputeRequest,db:DbSession,user:Editor):
    from app.briefs.manual_compute import normalize

    today=datetime.now(CHINA).date()
    if body.issue_date>today:raise HTTPException(422,'不能处理未来生产日期')
    active=db.scalar(select(BriefJob).where(
        BriefJob.team_id==user.team_id, BriefJob.issue_date==body.issue_date,
        BriefJob.category==body.category,
        BriefJob.status.in_(['queued','running','waiting'])))
    if active:raise HTTPException(409,'该栏目正在生成，请等待当前任务结束后再录入')
    try:
        capture=normalize(body.category,body.data_date,body.issue_date,today,
            {'token':body.token_price,'a100':body.a100,'h100':body.h100,'b200':body.b200},
            {'token':body.token_change_pct,'a100':body.a100_change_pct,
             'h100':body.h100_change_pct,'b200':body.b200_change_pct},
            body.source_note,user.id)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    job=enqueue(db,user.team_id,body.issue_date,body.category,action='produce',
                user_id=user.id,unique=True,manual_capture=capture)
    if job.manual_capture != capture:
        raise HTTPException(409,'该栏目刚开始执行其他任务，请稍后重新提交人工数据')
    db.commit()
    return job_out(job)


@router.get('/jobs/{id}')
def job(id:uuid.UUID,db:DbSession,user:CurrentUser):
    row=db.scalars(select(BriefJob).where(BriefJob.id==id,BriefJob.team_id==user.team_id)).first()
    if not row:raise HTTPException(404,'任务不存在')
    return job_out(row)


@router.get('/jobs/{id}/process')
def job_process(id:uuid.UUID,db:DbSession,user:CurrentUser):
    if user.role==Role.VIEWER:raise HTTPException(403,'仅编辑和管理员可检查生成详情')
    row=db.scalars(select(BriefJob).where(BriefJob.id==id,BriefJob.team_id==user.team_id)).first()
    if not row:raise HTTPException(404,'任务不存在')
    from app.briefs.process_details import for_job
    from app.briefs.service import root_for
    saved=db.scalars(select(BriefVersion).join(BriefIssue).where(BriefIssue.team_id==user.team_id,
        BriefIssue.issue_date==row.issue_date,BriefIssue.category==row.category).order_by(BriefVersion.number.desc())).all()
    payload=next((v.payload for v in saved if (v.payload.get('source_archive') or {}).get('job_id')==str(row.id)),None)
    return {'job':job_out(row),'details':for_job(row,root_for(row.team_id),payload)}


@router.get('/issues/{id}/versions')
def versions(id:uuid.UUID,db:DbSession,user:CurrentUser):
    return visible(db,user,get_issue(db,user,id))['versions']


@router.get('/issues/{id}/skill-preview')
def skill_preview(id:uuid.UUID,db:DbSession,user:CurrentUser,version:int=Query(ge=1)):
    get_issue(db,user,id)
    raise HTTPException(410,'独立AI预览已停用，文案由生成稿件流程统一处理')


@router.post('/issues/{id}/revisions',status_code=201)
def edit(id:uuid.UUID,body:EditRequest,db:DbSession,user:Editor):
    issue=get_issue(db,user,id,True)
    if body.base_version!=issue.version:raise HTTPException(409,'稿件已有新版本，请刷新后再保存')
    if not body.title.strip() or not body.dynamic_body.strip() or '\n' in body.title:
        raise HTTPException(422,'标题须为单行，标题和正文不能为空')
    previous=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==body.base_version)).one()
    payload=dict(previous.payload)
    edited_notes=body.fixed_notes.strip() if body.fixed_notes is not None else payload.get('fixed_notes','')
    if issue.category in ('token_cost','gpu_rental') and body.fixed_notes is not None and not edited_notes:
        raise HTTPException(422,'AI 算力图表必须保留固定注释')
    dynamic_body=(complete_body(issue.category,body.dynamic_body,edited_notes)
                  if issue.category in ('token_cost','gpu_rental') else body.dynamic_body.strip())
    if not dynamic_body:
        raise HTTPException(422,'动态正文不能为空')
    staged=None
    if body.fixed_notes is not None:
        if issue.category in ('token_cost','gpu_rental') and edited_notes != payload.get('chart_notes'):
            try:
                payload,staged=prepare_compute_notes_revision(issue,previous,edited_notes)
            except (ValueError,OSError) as exc:
                raise HTTPException(409,str(exc)) from None
        else:
            payload['fixed_notes']=edited_notes
    try:
        if body.notes_as_default and body.fixed_notes is not None:
            team=db.get(Team,user.team_id,with_for_update=True)
            update_notes(db,team,{issue.category:body.fixed_notes},user.id)
            team.settings={**team.settings,'settings_revision':team.settings.get('settings_revision',0)+1}
        append_version(db,issue,payload,user_id=user.id,kind='edited',
                       title=body.title.strip(),body=dynamic_body)
        db.commit()
    except Exception:
        db.rollback()
        if staged is not None:shutil.rmtree(staged,ignore_errors=True)
        raise
    return issue_out(db,issue)


@router.post('/issues/{id}/confirm')
def confirm(id:uuid.UUID,body:ConfirmRequest,db:DbSession,user:Editor):
    issue=get_issue(db,user,id,True)
    if issue.version!=body.version:raise HTTPException(409,'只能确认当前版本，请刷新核对')
    version=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==body.version)).first()
    if not version or not version.payload.get('valid'):raise HTTPException(409,'数据或图片未通过基础校验')
    for name in version.payload.get('images',[]):
        try:artifact_path(issue,version,name)
        except ValueError as e:raise HTTPException(409,str(e)) from None
    issue.confirmed_version=version.number;issue.confirmed_by=user.id;issue.confirmed_at=utcnow()
    db.add(BriefAudit(issue_id=id,action='confirmed',version=version.number,user_id=user.id));db.commit()
    return issue_out(db,issue)


@router.post('/issues/{id}/unconfirm')
def unconfirm(id:uuid.UUID,db:DbSession,user:Editor):
    issue=get_issue(db,user,id,True)
    if issue.confirmed_version:
        db.add(BriefAudit(issue_id=id,action='unconfirmed',version=issue.confirmed_version,user_id=user.id))
    issue.confirmed_version=None;issue.confirmed_by=None;issue.confirmed_at=None;db.commit()
    return issue_out(db,issue)


@router.get('/issues/{id}/files/{number}/{name}')
def file(id:uuid.UUID,number:int,name:str,db:DbSession,user:CurrentUser):
    issue=get_issue(db,user,id)
    if user.role==Role.VIEWER and number!=issue.confirmed_version:raise HTTPException(403,'只读账号仅可下载已确认稿')
    version=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==number)).first()
    if not version:raise HTTPException(404,'版本不存在')
    try:path,media_type=delivered_image(issue,version,name)
    except ValueError as e:raise HTTPException(404,str(e)) from None
    return FileResponse(path,media_type=media_type,filename=download_name(issue.issue_date,version.title,path.suffix),
                        headers={'Cache-Control':'private, no-cache'})
