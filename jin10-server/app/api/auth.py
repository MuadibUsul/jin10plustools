from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.db.models import User
from app.deps import CurrentUser, DbSession
from app.schemas import LoginRequest, LoginResponse, MeResponse, TeamOut, UserOut
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: DbSession) -> LoginResponse:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    token, expires = create_access_token(user.id, user.team_id)
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
