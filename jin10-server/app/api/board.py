"""团队共享选稿板：多个账号看到同一份已选素材与手动排序。

前端以增量操作提交（勾选 / 取消 / 某栏目的新顺序），服务端加行锁逐条合并，
两人同时操作时互不覆盖。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Board, User, utcnow
from app.deps import CurrentUser, DbSession, Editor

router = APIRouter(prefix="/api/board", tags=["board"])

MAX_IDS = 2000


class BoardOut(BaseModel):
    day: dt.date
    picked: list[str]
    order: dict[str, list[str]]
    version: int
    updated_by: str | None = None
    updated_at: dt.datetime | None = None


class BoardOps(BaseModel):
    """按下列顺序应用：pick_set → pick_add / pick_remove → order_reset → order"""

    #: 整体替换已选（自动选稿、清空选择）
    pick_set: list[str] | None = Field(default=None, max_length=MAX_IDS)
    pick_add: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    pick_remove: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    #: 清空所有手动排序（恢复 AI 排版）
    order_reset: bool = False
    #: 替换指定栏目的顺序，其他栏目不变
    order: dict[str, list[str]] = Field(default_factory=dict)


def _out(board: Board | None, day: dt.date, db: Session) -> BoardOut:
    if board is None:
        return BoardOut(day=day, picked=[], order={}, version=0)
    user = db.get(User, board.updated_by) if board.updated_by else None
    return BoardOut(
        day=day,
        picked=list(board.picked),
        order=dict(board.order),
        version=board.version,
        updated_by=user.name if user else None,
        updated_at=board.updated_at,
    )


def _find(db: Session, team_id: uuid.UUID, day: dt.date, *, lock: bool = False) -> Board | None:
    query = select(Board).where(Board.team_id == team_id, Board.day == day)
    if lock and db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    return db.scalars(query).first()


def _locked_board(db: Session, team_id: uuid.UUID, day: dt.date) -> Board:
    board = _find(db, team_id, day, lock=True)
    if board is not None:
        return board
    try:
        with db.begin_nested():
            db.add(Board(team_id=team_id, day=day, picked=[], order={}, version=0))
    except IntegrityError:
        pass  # 并发创建：另一请求已插入
    board = _find(db, team_id, day, lock=True)
    assert board is not None
    return board


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


@router.get("", response_model=BoardOut)
def get_board(day: Annotated[dt.date, Query()], db: DbSession, user: CurrentUser) -> BoardOut:
    return _out(_find(db, user.team_id, day), day, db)


@router.post("/ops", response_model=BoardOut)
def apply_ops(ops: BoardOps, day: Annotated[dt.date, Query()], db: DbSession, user: Editor) -> BoardOut:
    board = _locked_board(db, user.team_id, day)
    picked = list(board.picked) if ops.pick_set is None else _dedupe(ops.pick_set)
    removed = set(ops.pick_remove)
    picked = _dedupe([p for p in picked if p not in removed] + [p for p in ops.pick_add if p not in removed])
    order = {} if ops.order_reset else dict(board.order)
    for section, ids in ops.order.items():
        order[section] = _dedupe(ids)[:MAX_IDS]
        # 一条素材只属于一个栏目的顺序
        moved = set(order[section])
        for other in order:
            if other != section:
                order[other] = [x for x in order[other] if x not in moved]
    board.picked = picked
    board.order = order
    board.version += 1
    board.updated_by = user.id
    board.updated_at = utcnow()
    db.commit()
    return _out(board, day, db)
