from __future__ import annotations

import binascii
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Material, utcnow
from app.deps import CurrentUser, DbSession, Editor
from app.schemas import ChangesResponse, MaterialOut, SyncPushRequest, SyncPushResponse
from app.services import material_sync

router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.post("/sync", response_model=SyncPushResponse)
def push(body: SyncPushRequest, db: DbSession, user: Editor) -> SyncPushResponse:
    """批量推送本地素材；服务端去重合并后返回每条的最终版本"""
    limit = get_settings().max_sync_batch
    if len(body.materials) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=f"单次最多同步 {limit} 条")
    results = material_sync.push(db, user, body.materials)
    db.commit()
    return SyncPushResponse(results=results)


@router.get("/changes", response_model=ChangesResponse)
def changes(
    db: DbSession,
    user: CurrentUser,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ChangesResponse:
    """增量拉取团队素材变更（含删除），用返回的 next_cursor 继续"""
    try:
        rows, next_cursor, has_more = material_sync.changes(db, user.team_id, cursor, limit)
    except (ValueError, binascii.Error):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="无效的同步游标") from None
    return ChangesResponse(
        items=[MaterialOut.model_validate(m) for m in rows], next_cursor=next_cursor, has_more=has_more
    )


def _get_team_material(db: DbSession, user: CurrentUser, material_id: uuid.UUID) -> Material:
    material = db.get(Material, material_id)
    if material is None or material.team_id != user.team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="素材不存在")
    return material


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: uuid.UUID, db: DbSession, user: CurrentUser) -> MaterialOut:
    return MaterialOut.model_validate(_get_team_material(db, user, material_id))


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class BulkDeleteResponse(BaseModel):
    deleted: int


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
def bulk_delete(body: BulkDeleteRequest, db: DbSession, user: Editor) -> BulkDeleteResponse:
    """一键清空：软删除本团队的一批素材（重新采集同一条快讯会自动恢复）"""
    now = utcnow()
    rows = db.scalars(
        select(Material).where(
            Material.team_id == user.team_id, Material.id.in_(body.ids), Material.deleted_at.is_(None)
        )
    ).all()
    for material in rows:
        material.deleted_at = now
        material.updated_at = now
    db.commit()
    return BulkDeleteResponse(deleted=len(rows))


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(material_id: uuid.UUID, db: DbSession, user: Editor) -> None:
    material = _get_team_material(db, user, material_id)
    if material.deleted_at is None:
        now = utcnow()
        material.deleted_at = now
        material.updated_at = now
        db.commit()


@router.post("/{material_id}/analyze", response_model=MaterialOut)
def reanalyze(material_id: uuid.UUID, db: DbSession, user: Editor) -> MaterialOut:
    """重新排队进行 AI 分析（优先处理）"""
    material = _get_team_material(db, user, material_id)
    material.ai_status, material.ai_attempts, material.ai_error = "pending", 0, None
    material.analyze_requested = True
    material.updated_at = utcnow()
    db.commit()
    return MaterialOut.model_validate(material)
