"""选中的素材 → 日报（PPTX + PDF）。

素材文字原样进入 PPT（编辑在选稿页修改过的，以编辑版本为准）：
- 标题：素材展示标题（有 PLUS 注解时为 PLUS 标题）
- 【新闻】：快讯正文
- 【解读】：PLUS 注解各模块的段落（依次排列）
- 配图：快讯配图 + 注解配图（服务端已存有文件的）；编辑指定了配图则用编辑的

预览与正式生成走同一条流程（render_issue），预览图由最终 PDF 渲染，两者完全一致。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import tempfile
from collections import OrderedDict
import uuid
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, Issue, Material, User
from app.ppt.generator import CoverLayoutError, generate
from app.ppt.models import DailyIssue, DailyItem, DailySection, ImageRef, SectionId
from app.ppt.pdf import PdfConversionError, pptx_to_pdf
from app.schemas import GenerateRequest

from .storage import asset_path
from .cover_selection import recommend_cover_ids
from .issue_images import ImageSizeError, image_zip
from .issue_layout import apply_layout, editable_elements


class IssueBuildError(ValueError):
    pass


def original_asset_ids(material: Material) -> list[str]:
    """采集时带来的配图（快讯配图 + 注解配图），去重保序"""
    images: list[dict[str, Any]] = list(material.images)
    for module in (material.interpretation or {}).get("modules", []):
        images.extend(module.get("images", []))
    ids: list[str] = []
    for img in images:
        aid = img.get("asset_id")
        if aid and aid not in ids:
            ids.append(aid)
    return ids


def _image_refs(material: Material, available: set[str]) -> list[ImageRef]:
    edits = material.edits or {}
    ids = edits["images"] if edits.get("images") is not None else original_asset_ids(material)
    return [ImageRef(path=asset_path(material.team_id, aid)) for aid in ids if aid in available]


def to_daily_item(material: Material, available_assets: set[str]) -> DailyItem:
    edits = material.edits or {}
    paragraphs = [
        p for module in (material.interpretation or {}).get("modules", []) for p in module.get("paragraphs", []) if p
    ]
    if edits.get("interpretation") is not None:
        paragraphs = [p for p in edits["interpretation"] if p.strip()]
    news = edits["news"] if edits.get("news") is not None else material.content
    return DailyItem(
        title=edits.get("title") or material.title,
        news=news or None,
        interpretation=paragraphs,
        images=_image_refs(material, available_assets),
    )


def issue_dir(team_id: uuid.UUID, issue_id: uuid.UUID) -> Path:
    return get_settings().storage_dir / str(team_id) / "issues" / str(issue_id)


def _daily_issue(db: Session, user: User, req: GenerateRequest) -> DailyIssue:
    ids = [mid for s in req.sections for mid in s.material_ids]
    if len({s.id for s in req.sections}) != len(req.sections):
        raise IssueBuildError("同一个栏目不能出现两次")
    if len(set(ids)) != len(ids):
        raise IssueBuildError("同一条素材不能出现两次")
    rows = {
        m.id: m
        for m in db.scalars(
            select(Material).where(Material.team_id == user.team_id, Material.id.in_(ids), Material.deleted_at.is_(None))
        )
    }
    missing = [str(i) for i in ids if i not in rows]
    if missing:
        raise IssueBuildError(f"素材不存在或已删除：{', '.join(missing)}")

    asset_ids = set(db.scalars(select(Asset.id).where(Asset.team_id == user.team_id)))
    # 与正文的固定栏目顺序一致，首页不按推荐分数重新排序。
    ordered_ids = [mid for sid in SectionId for s in req.sections if s.id == sid for mid in s.material_ids]
    cover_ids = ([str(mid) for mid in req.cover_material_ids] if req.cover_material_ids is not None
                 else recommend_cover_ids(rows.values()))
    if len(set(cover_ids)) != len(cover_ids) or not set(cover_ids) <= {str(mid) for mid in ids}:
        raise IssueBuildError("首页精选不可重复，且必须来自本期已选素材")
    return DailyIssue(
        date=req.issue_date,
        cover_indices=[index for index, mid in enumerate(ordered_ids) if str(mid) in cover_ids],
        sections=[
            DailySection(id=SectionId(s.id), items=[to_daily_item(rows[mid], asset_ids) for mid in s.material_ids])
            for s in req.sections
        ],
    )


def render_issue(
    db: Session, user: User, req: GenerateRequest, out_dir: Path
) -> tuple[Path, Path | None, int, list[str]]:
    """生成 PPTX 与 PDF（预览与正式生成共用），返回 (pptx, pdf 或 None, 页数, 提示)"""
    try:
        report = generate(_daily_issue(db, user, req), out_dir / f"PLUS日报-{req.issue_date:%Y-%m-%d}.pptx")
    except CoverLayoutError as err:
        raise IssueBuildError(str(err)) from None
    try:
        apply_layout(report.output, req.layout_overrides)
    except ValueError as err:
        raise IssueBuildError(str(err)) from None
    warnings = list(report.warnings)
    try:
        pdf: Path | None = pptx_to_pdf(report.output, out_dir)
    except PdfConversionError as err:
        pdf = None
        warnings.append(f"PDF 生成失败（可下载 PPT）：{err}")
    return report.output, pdf, report.page_count, warnings


def build_issue(db: Session, user: User, req: GenerateRequest) -> Issue:
    issue = Issue(
        id=uuid.uuid4(),
        team_id=user.team_id,
        issue_date=req.issue_date,
        sections=[s.model_dump(mode="json") for s in req.sections],
        created_by=user.id,
    )
    storage = get_settings().storage_dir
    pptx, pdf, pages, warnings = render_issue(db, user, req, issue_dir(user.team_id, issue.id))
    issue.pptx_path = str(pptx.relative_to(storage))
    issue.pdf_path = str(pdf.relative_to(storage)) if pdf else None
    issue.page_count = pages
    issue.warnings = warnings
    db.add(issue)
    db.commit()
    return issue


def default_issue_date() -> dt.date:
    """北京时间今天"""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=8)).date()


_PREVIEW_CACHE: OrderedDict[str, tuple[list[bytes], int, list[str], list[list[dict]]]] = OrderedDict()
_PREVIEW_CACHE_SIZE = 4
PREVIEW_DPI = 96


def _preview_key(db: Session, user: User, req: GenerateRequest, dpi: int) -> str:
    """选稿、顺序与素材内容（updated_at 覆盖文字 / 配图编辑）都没变时，预览结果可直接复用"""
    ids = [uuid.UUID(str(i)) for s in req.sections for i in s.material_ids]
    stamps = sorted(
        (str(m.id), m.updated_at.isoformat())
        for m in db.scalars(select(Material).where(Material.team_id == user.team_id, Material.id.in_(ids)))
    )
    raw = json.dumps([str(user.team_id), req.model_dump(mode="json"), stamps, dpi], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def preview_issue(db: Session, user: User, req: GenerateRequest, dpi: int = PREVIEW_DPI) -> tuple[list[bytes], int, list[str], list[list[dict]]]:
    """生成一份临时 PDF 并逐页渲染为 PNG（与正式生成同一流程，所见即所得）；内容未变时直接返回缓存"""
    key = _preview_key(db, user, req, dpi)
    if key in _PREVIEW_CACHE:
        _PREVIEW_CACHE.move_to_end(key)
        return _PREVIEW_CACHE[key]
    result = _render_preview(db, user, req, dpi)
    _PREVIEW_CACHE[key] = result
    while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_SIZE:
        _PREVIEW_CACHE.popitem(last=False)
    return result


def _render_preview(db: Session, user: User, req: GenerateRequest, dpi: int) -> tuple[list[bytes], int, list[str], list[list[dict]]]:
    with tempfile.TemporaryDirectory(prefix="plus-preview-") as tmp:
        pptx, pdf, pages, warnings = render_issue(db, user, req, Path(tmp))
        if pdf is None:
            raise IssueBuildError("预览生成失败：" + "；".join(warnings))
        elements = editable_elements(pptx)
        with pymupdf.open(pdf) as doc:
            images = [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    return images, pages, warnings, elements


def preview_image_zip(db: Session, user: User, req: GenerateRequest) -> bytes:
    """Render the same PDF as preview and package its pages at delivery resolution."""
    with tempfile.TemporaryDirectory(prefix='plus-page-images-') as tmp:
        _pptx, pdf, _pages, warnings = render_issue(db, user, req, Path(tmp))
        if pdf is None:
            raise IssueBuildError('图片生成失败：' + '；'.join(warnings))
        try:
            return image_zip(pdf)
        except ImageSizeError as err:
            raise IssueBuildError(str(err)) from err
