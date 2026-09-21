from __future__ import annotations

import hashlib
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.models import Asset
from app.deps import CurrentUser, DbSession, Editor
from app.schemas import AssetExistsRequest, AssetExistsResponse
from app.services.storage import asset_path, write_atomic

router = APIRouter(prefix="/api/assets", tags=["assets"])

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def expected_asset_id(source_url: str) -> str:
    """与插件 assetIdForUrl 一致：sha256(原图地址) 前 32 位"""
    return hashlib.sha256(source_url.encode()).hexdigest()[:32]


@router.post("/exists", response_model=AssetExistsResponse)
def exists(body: AssetExistsRequest, db: DbSession, user: CurrentUser) -> AssetExistsResponse:
    """批量查询哪些图片服务端还没有，插件只上传缺失的"""
    ids = set(body.ids)
    have = set(db.scalars(select(Asset.id).where(Asset.team_id == user.team_id, Asset.id.in_(ids))))
    return AssetExistsResponse(missing=sorted(ids - have))


@router.put("/{asset_id}", status_code=status.HTTP_201_CREATED)
async def upload(
    asset_id: str,
    request: Request,
    db: DbSession,
    user: Editor,
    content_type: Annotated[str, Header()],
    x_source_url: Annotated[str, Header()],
) -> Response:
    """上传图片原始字节（幂等：已存在则直接返回 200）"""
    source_url = unquote(x_source_url)
    if asset_id != expected_asset_id(source_url):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="图片 ID 与原图地址不匹配")
    mime = content_type.split(";")[0].strip().lower()
    if mime not in _ALLOWED_MIME:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"不支持的图片类型 {mime}")

    if db.get(Asset, (user.team_id, asset_id)) is not None:
        return Response(status_code=status.HTTP_200_OK)

    limit = get_settings().max_asset_bytes
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="图片过大")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="图片内容为空")

    path = asset_path(user.team_id, asset_id)
    write_atomic(path, bytes(data))
    db.add(
        Asset(
            team_id=user.team_id,
            id=asset_id,
            source_url=source_url,
            mime=mime,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            path=str(path.relative_to(get_settings().storage_dir)),
            uploaded_by=user.id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # 同事同时上传了同一张图：内容相同，视为成功
        db.rollback()
        return Response(status_code=status.HTTP_200_OK)
    return Response(status_code=status.HTTP_201_CREATED)


@router.get("/{asset_id}")
def download(asset_id: str, db: DbSession, user: CurrentUser) -> FileResponse:
    asset = db.get(Asset, (user.team_id, asset_id))
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="图片不存在")
    return FileResponse(
        asset_path(user.team_id, asset_id), media_type=asset.mime, headers={"Cache-Control": "private, max-age=31536000, immutable"}
    )
