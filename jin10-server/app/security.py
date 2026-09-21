"""密码哈希（标准库 scrypt）与访问令牌（JWT）"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import uuid

import jwt

from app.config import get_settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: uuid.UUID, team_id: uuid.UUID) -> tuple[str, dt.datetime]:
    settings = get_settings()
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(hours=settings.jwt_expire_hours)
    token = jwt.encode(
        {"sub": str(user_id), "team": str(team_id), "exp": expires}, settings.jwt_secret, algorithm=_ALGORITHM
    )
    return token, expires


def decode_access_token(token: str) -> uuid.UUID:
    """返回用户 ID；令牌无效或过期抛出 jwt.PyJWTError"""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
    return uuid.UUID(payload["sub"])
