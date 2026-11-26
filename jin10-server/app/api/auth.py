from __future__ import annotations

import threading
import time

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.db.models import User
from app.deps import CurrentUser, DbSession
from app.schemas import LoginRequest, LoginResponse, MeResponse, TeamOut, UserOut
from app.security import DUMMY_PASSWORD_HASH, create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_LOGIN_WINDOW_SECONDS = 60.0
_MAX_FAILED_ATTEMPTS = 15
_LOGIN_LOCK = threading.Lock()
_FAILED_ATTEMPTS: dict[str, list[float]] = {}


def _rate_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "local"
    return f"{host}:{email.lower()}"


def _check_login_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        recent = [t for t in _FAILED_ATTEMPTS.get(key, ()) if now - t < _LOGIN_WINDOW_SECONDS]
        if recent:
            _FAILED_ATTEMPTS[key] = recent
        else:
            _FAILED_ATTEMPTS.pop(key, None)
        if len(recent) >= _MAX_FAILED_ATTEMPTS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="登录尝试过于频繁，请一分钟后再试")


def _record_login_result(key: str, *, success: bool) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        if success:
            _FAILED_ATTEMPTS.pop(key, None)
            return
        if len(_FAILED_ATTEMPTS) > 2048:
            cutoff = now - _LOGIN_WINDOW_SECONDS
            stale = [k for k, stamps in _FAILED_ATTEMPTS.items() if not stamps or stamps[-1] < cutoff]
            for k in stale[:1024]:
                _FAILED_ATTEMPTS.pop(k, None)
        stamps = [t for t in _FAILED_ATTEMPTS.get(key, ()) if now - t < _LOGIN_WINDOW_SECONDS]
        stamps.append(now)
        _FAILED_ATTEMPTS[key] = stamps


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: DbSession) -> LoginResponse:
    key = _rate_key(request, body.email)
    _check_login_rate_limit(key)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    target_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(body.password, target_hash)
    if user is None or not user.is_active or not password_ok:
        _record_login_result(key, success=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    _record_login_result(key, success=True)
    token, expires = create_access_token(user.id, user.team_id, user.auth_version)
    return LoginResponse(
        access_token=token,
        expires_at=expires,
        user=UserOut.model_validate(user),
        team=TeamOut.model_validate(user.team),
    )


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser) -> MeResponse:
    members = [UserOut.model_validate(u) for u in user.team.users if u.is_active]
    return MeResponse(user=UserOut.model_validate(user), team=TeamOut.model_validate(user.team), members=members)
