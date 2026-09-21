"""素材同步：团队内去重合并与增量拉取。

合并规则与插件 materialService.mergeMaterial 一致：
- 同一团队内，按 素材 ID → 金十条目 ID → 内容指纹 查找既有素材
- 已有解读（fetched / manual）不覆盖；否则采用新结果
- 图片按 URL 合并，补齐缺失的 asset_id
- analyze_requested 只会由 false 变为 true
- 收藏人去重累加（团队热度）
- AI 与编辑字段由服务端维护，客户端推送不会改写
"""

from __future__ import annotations

import base64
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.analysis import ScoringConfig, refresh_final_score
from app.db.models import Material, Team, User, utcnow
from app.schemas import MaterialIn, MaterialOut, SyncOutcome, SyncResult

_FINAL_INTERPRETATION = {"fetched", "manual"}

#: 客户端可写入的采集字段（创建或恢复素材时使用）
_CONTENT_FIELDS = (
    "source",
    "source_item_id",
    "fingerprint",
    "item_kind",
    "locked",
    "tags",
    "detail_url",
    "title",
    "flash_title",
    "content",
    "content_raw",
    "plus_title",
    "has_plus_link",
    "publish_time",
    "url",
    "raw_html",
    "collect_time",
    "collect_method",
    "interpretation_status",
    "analyze_requested",
)


def _dump_images(images: list[Any]) -> list[dict[str, Any]]:
    return [img.model_dump(mode="json") for img in images]


def _dump_interpretation(item: MaterialIn) -> dict[str, Any] | None:
    return item.interpretation.model_dump(mode="json") if item.interpretation else None


def _find_existing(db: Session, team_id: uuid.UUID, item: MaterialIn) -> Material | None:
    conditions = [Material.id == item.id, Material.fingerprint == item.fingerprint]
    if item.source_item_id:
        conditions.append(Material.source_item_id == item.source_item_id)
    candidates = db.scalars(select(Material).where(Material.team_id == team_id, or_(*conditions))).all()
    # 优先级：同一 ID > 同一金十条目 > 同一指纹
    for match in (
        lambda m: m.id == item.id,
        lambda m: item.source_item_id is not None and m.source_item_id == item.source_item_id,
        lambda m: m.fingerprint == item.fingerprint,
    ):
        for m in candidates:
            if match(m):
                return m
    return None


def _apply_content(material: Material, item: MaterialIn) -> None:
    for field in _CONTENT_FIELDS:
        setattr(material, field, getattr(item, field))
    material.images = _dump_images(item.images)
    material.interpretation = _dump_interpretation(item)


def _merge_images(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url = {img["url"]: dict(img) for img in existing}
    for img in incoming:
        prev = by_url.get(img["url"])
        if prev is None:
            by_url[img["url"]] = img
        elif not prev.get("asset_id") and img.get("asset_id"):
            prev["asset_id"] = img["asset_id"]
    return list(by_url.values())


def _merge_into(material: Material, item: MaterialIn, user: User) -> set[str]:
    """合并推送内容，返回发生变化的方面：interpretation / images / analyze / collectors"""
    changes: set[str] = set()

    if material.interpretation_status not in _FINAL_INTERPRETATION:
        new_interp = _dump_interpretation(item)
        if item.interpretation_status != material.interpretation_status or new_interp != material.interpretation:
            material.interpretation_status = item.interpretation_status
            material.interpretation = new_interp
            changes.add("interpretation")
    elif material.interpretation and item.interpretation:
        # 解读正文不变，仅补齐配图文件
        incoming = {
            img.url: img.asset_id for m in item.interpretation.modules for img in m.images if img.asset_id
        }
        modules = []
        for module in material.interpretation.get("modules", []):
            images = [
                {**img, "asset_id": img.get("asset_id") or incoming.get(img["url"])} for img in module.get("images", [])
            ]
            modules.append({**module, "images": images})
        if modules != material.interpretation.get("modules"):
            material.interpretation = {**material.interpretation, "modules": modules}
            changes.add("images")

    images = _merge_images(material.images, _dump_images(item.images))
    if images != material.images:
        material.images = images
        changes.add("images")

    if item.analyze_requested and not material.analyze_requested:
        material.analyze_requested = True
        changes.add("analyze")

    if str(user.id) not in material.collected_by:
        material.collected_by = [*material.collected_by, str(user.id)]
        changes.add("collectors")

    return changes


def _queue_analysis(material: Material) -> None:
    material.ai_status = "pending"
    material.ai_attempts = 0
    material.ai_error = None


def _push_one(db: Session, user: User, item: MaterialIn) -> SyncResult:
    existing = _find_existing(db, user.team_id, item)
    now = utcnow()

    if item.deleted_at is not None:
        if existing and existing.deleted_at is None:
            existing.deleted_at = now
            existing.updated_at = now
        return SyncResult(
            client_id=item.id, outcome="deleted", material=MaterialOut.model_validate(existing) if existing else None
        )

    outcome: SyncOutcome
    if existing is None:
        material = Material(
            id=item.id,
            team_id=user.team_id,
            created_by=user.id,
            collected_by=[str(user.id)],
            created_at=now,
            updated_at=now,
        )
        _apply_content(material, item)
        _queue_analysis(material)
        db.add(material)
        outcome = "created"
    elif existing.deleted_at is not None:
        # 删除后重新采集：沿用原 ID 恢复
        material = existing
        _apply_content(material, item)
        material.deleted_at = None
        material.collected_by = [str(user.id)]
        material.updated_at = now
        _queue_analysis(material)
        outcome = "created"
    else:
        material = existing
        changes = _merge_into(material, item, user)
        if changes:
            if "interpretation" in changes:
                _queue_analysis(material)  # 解读补全后判断依据变了，重新分析
            elif "collectors" in changes:
                team = db.get(Team, material.team_id)
                refresh_final_score(material, ScoringConfig.from_team_settings(team.settings if team else None), now)
            material.updated_at = now
            outcome = "merged"
        else:
            outcome = "unchanged"

    db.flush()
    return SyncResult(client_id=item.id, outcome=outcome, material=MaterialOut.model_validate(material))


def push(db: Session, user: User, items: list[MaterialIn]) -> list[SyncResult]:
    results: list[SyncResult] = []
    for item in items:
        # 每条素材独立的保存点：并发下唯一约束冲突时重试一次（此时会命中对方刚写入的记录并合并）
        for attempt in range(2):
            try:
                with db.begin_nested():
                    results.append(_push_one(db, user, item))
                break
            except IntegrityError:
                if attempt == 1:
                    raise
    return results


# ---------- 增量拉取 ----------


def encode_cursor(updated_at: dt.datetime, material_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{material_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts, mid = raw.split("|")
    parsed = dt.datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed, uuid.UUID(mid)


def changes(
    db: Session, team_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[Material], str | None, bool]:
    """按 (updated_at, id) 顺序返回游标之后的变更（含已删除的墓碑）"""
    query = select(Material).where(Material.team_id == team_id)
    if cursor:
        ts, mid = decode_cursor(cursor)
        query = query.where(or_(Material.updated_at > ts, and_(Material.updated_at == ts, Material.id > mid)))
    rows = list(db.scalars(query.order_by(Material.updated_at, Material.id).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].updated_at, rows[-1].id) if rows else cursor
    return rows, next_cursor, has_more
