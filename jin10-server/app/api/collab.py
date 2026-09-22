"""协作辅助：谁正在编辑哪条素材；插件解析异常的上报与汇总。"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import ParseReport, utcnow
from app.deps import CurrentUser, DbSession

router = APIRouter(prefix="/api", tags=["collab"])

# ---------- 正在编辑（单进程内存即可：重启后几秒内由心跳恢复） ----------

PRESENCE_TTL = 12.0
_presence: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, str, float]] = {}
_lock = threading.Lock()


class PresenceIn(BaseModel):
    material_id: str | None = None


class PresenceOut(BaseModel):
    material_id: str
    user_name: str


@router.post("/presence", response_model=list[PresenceOut])
def heartbeat(body: PresenceIn, user: CurrentUser) -> list[PresenceOut]:
    """前端每隔几秒上报自己正在编辑的素材（无则为 null），返回同事的编辑状态"""
    now = time.monotonic()
    with _lock:
        key = (user.team_id, user.id)
        if body.material_id:
            _presence[key] = (body.material_id, user.name, now)
        else:
            _presence.pop(key, None)
        for k in [k for k, v in _presence.items() if now - v[2] > PRESENCE_TTL]:
            del _presence[k]
        return [
            PresenceOut(material_id=mid, user_name=name)
            for (team, uid), (mid, name, _) in _presence.items()
            if team == user.team_id and uid != user.id
        ]


# ---------- 解析异常 ----------

REPORT_WINDOW = dt.timedelta(hours=24)


class ReportIn(BaseModel):
    kind: str = Field(max_length=40)
    source_item_id: str | None = Field(default=None, max_length=64)
    detail: str = Field(default="", max_length=2000)
    html: str = Field(default="", max_length=30_000)
    extension_version: str = Field(default="", max_length=20)


class ReportOut(BaseModel):
    kind: str
    source_item_id: str | None
    detail: str
    extension_version: str
    user_name: str | None
    created_at: dt.datetime


class ReportSummary(BaseModel):
    total: int
    by_kind: dict[str, int]
    latest: list[ReportOut]


@router.post("/parse-reports", status_code=status.HTTP_204_NO_CONTENT)
def report(body: ReportIn, db: DbSession, user: CurrentUser) -> None:
    db.add(ParseReport(team_id=user.team_id, user_id=user.id, **body.model_dump()))
    db.commit()


@router.get("/parse-reports/summary", response_model=ReportSummary)
def summary(db: DbSession, user: CurrentUser) -> ReportSummary:
    """最近 24 小时的解析异常（选稿页据此提示金十可能改版）"""
    rows = db.scalars(
        select(ParseReport)
        .where(ParseReport.team_id == user.team_id, ParseReport.created_at >= utcnow() - REPORT_WINDOW)
        .order_by(ParseReport.created_at.desc())
    ).all()
    names = {u.id: u.name for u in user.team.users}
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    latest = [
        ReportOut(
            kind=r.kind,
            source_item_id=r.source_item_id,
            detail=r.detail,
            extension_version=r.extension_version,
            user_name=names.get(r.user_id) if r.user_id else None,
            created_at=r.created_at,
        )
        for r in rows[:20]
    ]
    return ReportSummary(total=len(rows), by_kind=by_kind, latest=latest)
