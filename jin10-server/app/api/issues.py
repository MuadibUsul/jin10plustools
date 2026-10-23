from __future__ import annotations

import base64
import datetime as dt
import uuid
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Asset, Issue, Material, utcnow
from app.deps import CurrentUser, DbSession, Editor
from app.schemas import GenerateRequest, IssueOut, MaterialEdits, MaterialOut, MaterialPatch, PreviewOut
from app.services.issue_builder import IssueBuildError, build_issue, preview_image_zip, preview_issue

router = APIRouter(prefix="/api", tags=["issues"])

BEIJING = dt.timezone(dt.timedelta(hours=8))


def _issue_out(issue: Issue) -> IssueOut:
    return IssueOut.model_validate({**issue.__dict__, "has_pdf": issue.pdf_path is not None})


@router.get("/pool", response_model=list[MaterialOut])
def pool(
    db: DbSession,
    user: CurrentUser,
    day: dt.date | None = None,
    days: Annotated[int, Query(ge=1, le=14)] = 1,
) -> list[MaterialOut]:
    """待选素材池：某日（北京时间）起往前 days 天内采集的素材，按综合分从高到低"""
    end_day = day or dt.datetime.now(BEIJING).date()
    # 统一换算为 UTC 比较（SQLite 存储时不保留时区）
    start = dt.datetime.combine(end_day - dt.timedelta(days=days - 1), dt.time.min, BEIJING).astimezone(dt.UTC)
    end = dt.datetime.combine(end_day + dt.timedelta(days=1), dt.time.min, BEIJING).astimezone(dt.UTC)
    rows = db.scalars(
        select(Material)
        .where(
            Material.team_id == user.team_id,
            Material.deleted_at.is_(None),
            Material.collect_time >= start,
            Material.collect_time < end,
        )
        .order_by(Material.final_score.desc(), Material.collect_time.desc())
    )
    return [MaterialOut.model_validate(m) for m in rows]


@router.patch("/materials/{material_id}", response_model=MaterialOut)
def patch_material(material_id: uuid.UUID, body: MaterialPatch, db: DbSession, user: Editor) -> MaterialOut:
    material = db.get(Material, material_id)
    if material is None or material.team_id != user.team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="素材不存在")
    material.category = body.category
    material.category_locked = True
    material.updated_at = utcnow()
    db.commit()
    return MaterialOut.model_validate(material)


def _team_material(db: DbSession, user: CurrentUser, material_id: uuid.UUID) -> Material:
    material = db.get(Material, material_id)
    if material is None or material.team_id != user.team_id or material.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="素材不存在")
    return material


def _editable_material(db, user, material_id, match):
    material=db.scalar(select(Material).where(Material.id==material_id,
        Material.team_id==user.team_id,Material.deleted_at.is_(None)).with_for_update())
    if material is None:raise HTTPException(404,'素材不存在')
    if not match:raise HTTPException(428,'请刷新素材后再保存修改')
    try:stamp=dt.datetime.fromisoformat(match.strip('"').replace('Z','+00:00'))
    except ValueError:raise HTTPException(422,'素材版本格式不正确') from None
    def utc(value):return (value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC))
    if utc(stamp)!=utc(material.updated_at):raise HTTPException(409,'素材已由其他成员更新，请刷新核对后再保存')
    return material


@router.put("/materials/{material_id}/edits", response_model=MaterialOut)
def save_edits(material_id: uuid.UUID, body: MaterialEdits, db: DbSession, user: Editor,
               if_match: Annotated[str | None, Header()] = None) -> MaterialOut:
    """保存编辑版本（标题 / 新闻 / 解读 / 配图）；不改动采集原文"""
    material = _editable_material(db, user, material_id, if_match)
    if body.images:
        have = set(db.scalars(select(Asset.id).where(Asset.team_id == user.team_id, Asset.id.in_(body.images))))
        missing = [i for i in body.images if i not in have]
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="图片尚未上传：" + ", ".join(missing))
    edits = body.model_dump(exclude_none=True)
    material.edits = edits or None
    material.updated_at = utcnow()
    db.commit()
    return MaterialOut.model_validate(material)


@router.delete("/materials/{material_id}/edits", response_model=MaterialOut)
def clear_edits(material_id: uuid.UUID, db: DbSession, user: Editor,
                if_match: Annotated[str | None, Header()] = None) -> MaterialOut:
    """恢复采集原文"""
    material = _editable_material(db, user, material_id, if_match)
    material.edits = None
    material.updated_at = utcnow()
    db.commit()
    return MaterialOut.model_validate(material)


@router.post("/pool/reset-ai", response_model=list[MaterialOut])
def reset_ai_layout(
    db: DbSession,
    user: Editor,
    day: dt.date | None = None,
    days: Annotated[int, Query(ge=1, le=14)] = 1,
) -> list[MaterialOut]:
    """恢复 AI 初始排版：取消编辑手动指定的栏目，回到 JEV 分类结果"""
    rows = pool(db, user, day, days)
    now = utcnow()
    for out in rows:
        material = db.get(Material, out.id)
        if material is None or not material.category_locked:
            continue
        ai_choice = ((material.ai_details or {}).get("section") or {}).get("choice")
        material.category_locked = False
        material.category = ai_choice
        material.updated_at = now
    db.commit()
    return pool(db, user, day, days)


@router.post("/issues/preview", response_model=PreviewOut)
def preview(body: GenerateRequest, db: DbSession, user: CurrentUser) -> PreviewOut:
    """实时预览：与正式生成同一流程生成 PDF，逐页转为图片"""
    try:
        images, pages, warnings, elements = preview_issue(db, user, body)
    except IssueBuildError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(err)) from None
    return PreviewOut(
        page_count=pages,
        warnings=warnings,
        pages=["data:image/png;base64," + base64.b64encode(png).decode() for png in images],
        elements=elements,
    )


@router.post('/issues/preview/images')
def preview_images(body: GenerateRequest, db: DbSession, user: CurrentUser) -> Response:
    """Download full-resolution preview pages as one ZIP, without creating an issue."""
    try:
        content = preview_image_zip(db, user, body)
    except IssueBuildError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(err)) from None
    filename = f'PLUS日报-{body.issue_date:%Y-%m-%d}-图片.zip'
    return Response(content, media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="plus-daily-images.zip"; filename*=UTF-8\'\'{quote(filename)}',
        'Cache-Control': 'no-store',
    })


@router.post("/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED)
def create_issue(body: GenerateRequest, db: DbSession, user: Editor) -> IssueOut:
    """用选中的素材生成日报（PPTX + PDF）"""
    try:
        issue = build_issue(db, user, body)
    except IssueBuildError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(err)) from None
    return _issue_out(issue)


@router.get("/issues", response_model=list[IssueOut])
def list_issues(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IssueOut]:
    rows = db.scalars(
        select(Issue).where(Issue.team_id == user.team_id).order_by(Issue.created_at.desc()).offset(offset).limit(limit)
    )
    return [_issue_out(i) for i in rows]


@router.get("/issues/{issue_id}/file")
def download(
    issue_id: uuid.UUID, db: DbSession, user: CurrentUser, format: Literal["pdf", "pptx"] = "pdf"
) -> FileResponse:
    issue = db.get(Issue, issue_id)
    if issue is None or issue.team_id != user.team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="日报不存在")
    rel = issue.pdf_path if format == "pdf" else issue.pptx_path
    if rel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="该日报没有 PDF，请下载 PPT")
    path = get_settings().storage_dir / rel
    filename = f"PLUS日报-{issue.issue_date:%Y-%m-%d}.{format}"
    media = "application/pdf" if format == "pdf" else (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    return FileResponse(
        path, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    )
