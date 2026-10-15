from __future__ import annotations
import io
import json
import uuid
import zipfile
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import BriefAudit, BriefConfig, BriefIssue, BriefJob, BriefVersion, Role, utcnow
from app.deps import CurrentUser, DbSession, Editor
from app.briefs.policy import CATEGORIES, CHINA, production_window
from app.briefs.service import owned, enqueue, issue_out, job_out, append_version, artifact_path
from app.briefs.delivery import complete_body

router=APIRouter(prefix='/api/briefs',tags=['fixed briefs'])


class JobRequest(BaseModel):
    category: Literal['volume','turnover','valuation','liquidity','oil_etf']
    issue_date: date
    action: Literal['produce','refresh','render','ai']='produce'
    preview: bool=False


class EditRequest(BaseModel):
    base_version: int=Field(ge=1)
    title: str=Field(min_length=1,max_length=300)
    dynamic_body: str=Field(min_length=1,max_length=20000)


class ConfirmRequest(BaseModel):
    version: int=Field(ge=1)
    acknowledge_warnings: bool=False


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
    item.enabled=body.enabled;item.updated_at=utcnow();db.commit()
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
    if body.issue_date.weekday()>=5:raise HTTPException(422,'周末不生产固定快讯')
    if not body.preview and (body.issue_date!=now.date() or not production_window(now)):
        raise HTTPException(409,'生产仅在当天工作日08:00—10:00运行；其他时间请使用测试运行')
    job=enqueue(db,user.team_id,body.issue_date,body.category,action=body.action,
                preview=body.preview,user_id=user.id,unique=True)
    db.commit();return job_out(job)


@router.get('/jobs/{id}')
def job(id:uuid.UUID,db:DbSession,user:CurrentUser):
    row=db.scalars(select(BriefJob).where(BriefJob.id==id,BriefJob.team_id==user.team_id)).first()
    if not row:raise HTTPException(404,'任务不存在')
    return job_out(row)


@router.get('/issues/{id}/versions')
def versions(id:uuid.UUID,db:DbSession,user:CurrentUser):
    return visible(db,user,get_issue(db,user,id))['versions']


@router.post('/issues/{id}/revisions',status_code=201)
def edit(id:uuid.UUID,body:EditRequest,db:DbSession,user:Editor):
    issue=get_issue(db,user,id,True)
    if body.base_version!=issue.version:raise HTTPException(409,'稿件已有新版本，请刷新后再保存')
    if not body.title.strip() or not body.dynamic_body.strip() or '\n' in body.title:
        raise HTTPException(422,'标题须为单行，标题和正文不能为空')
    previous=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==body.base_version)).one()
    # An immutable payload snapshot retains exact images and source warnings.
    append_version(db,issue,dict(previous.payload),user_id=user.id,kind='edited',
                   title=body.title.strip(),body=body.dynamic_body.strip())
    db.commit();return issue_out(db,issue)


@router.post('/issues/{id}/confirm')
def confirm(id:uuid.UUID,body:ConfirmRequest,db:DbSession,user:Editor):
    issue=get_issue(db,user,id,True)
    if issue.version!=body.version:raise HTTPException(409,'只能确认当前版本，请刷新核对')
    version=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==body.version)).first()
    if not version or not version.payload.get('valid'):raise HTTPException(409,'数据或图片未通过基础校验')
    if version.payload.get('warnings') and not body.acknowledge_warnings:
        raise HTTPException(409,'请先确认已阅读来源口径及数据缺口说明')
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
    try:path=artifact_path(issue,version,name)
    except ValueError as e:raise HTTPException(404,str(e)) from None
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'private, no-cache'})


@router.get('/issues/{id}/export')
def export(id:uuid.UUID,db:DbSession,user:CurrentUser,format:Literal['zip','text']='zip',version:int|None=None):
    issue=get_issue(db,user,id)
    number=version or issue.confirmed_version
    if not number:raise HTTPException(409,'尚无确认稿；编辑可指定版本导出测试稿')
    if user.role==Role.VIEWER and number!=issue.confirmed_version:raise HTTPException(403,'仅可导出已确认版本')
    item=db.scalars(select(BriefVersion).where(BriefVersion.issue_id==id,BriefVersion.number==number)).first()
    if not item:raise HTTPException(404,'版本不存在')
    body=complete_body(issue.category,item.dynamic_body);text=f'# {item.title}\n\n{body}\n'
    if format=='text':return Response(text,media_type='text/plain; charset=utf-8')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('标题.txt',item.title);archive.writestr('正文.txt',body);archive.writestr('发布稿.md',text)
        archive.writestr('核验信息.json',json.dumps({'data_date':item.payload.get('data_date'),
            'warnings':item.payload.get('warnings',[]),'confirmed':number==issue.confirmed_version,
            'version':number,'published':False},ensure_ascii=False,indent=2))
        for name in item.payload.get('images',[]):
            try:path=artifact_path(issue,item,name)
            except ValueError as e:raise HTTPException(409,str(e)) from None
            archive.write(path,name)
    return Response(buffer.getvalue(),media_type='application/zip',
        headers={'Content-Disposition':f'attachment; filename="brief-{issue.category}-{issue.issue_date}-v{number}.zip"'})
