"""FastAPI 依赖：当前用户与权限"""

from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.models import Role, User
from app.db.session import get_db
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized("未登录")
    try:
        user_id: uuid.UUID = decode_access_token(credentials.credentials)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauthorized("登录已失效，请重新登录") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("账号不存在或已停用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_editor(user: CurrentUser) -> User:
    if user.role not in (Role.ADMIN, Role.EDITOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只读账号不能修改素材")
    return user


Editor = Annotated[User, Depends(require_editor)]
