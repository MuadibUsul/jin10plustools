"""Admin settings for the chat2api OpenAI-compatible node."""
from __future__ import annotations

import os
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.models import Role
from app.deps import CurrentUser

router = APIRouter(prefix='/api/chat2api', tags=['chat2api'])
TOKEN_COUNT = re.compile(r'当前可用 Tokens 数量：<span class="text-blue-600">(\d+)</span>')


def require_admin(user) -> None:
    if user.role != Role.ADMIN:
        raise HTTPException(403, '仅管理员可以管理 chat2api 登录')


def endpoint() -> str:
    base = os.getenv('PLUS_CHAT2API_URL', 'http://chat2api:5005').rstrip('/')
    prefix = os.getenv('PLUS_CHAT2API_PREFIX', '').strip().strip('/')
    if not prefix:
        raise HTTPException(503, 'chat2api 节点尚未配置')
    return f'{base}/{prefix}'


def request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=10) as client:
            response = client.request(method, endpoint() + path, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPError:
        raise HTTPException(502, 'chat2api 节点暂不可用') from None


class TokensIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@router.get('')
def status(user: CurrentUser):
    require_admin(user)
    if not os.getenv('PLUS_CHAT2API_PREFIX', '').strip():
        return {'configured': False, 'online': False, 'tokens_count': None}
    try:
        response = request('GET', '/tokens')
    except HTTPException as error:
        if error.status_code == 503:
            raise
        return {'configured': True, 'online': False, 'tokens_count': None}
    match = TOKEN_COUNT.search(response.text)
    return {'configured': True, 'online': True, 'tokens_count': int(match.group(1)) if match else None}


@router.post('/tokens')
def upload(body: TokensIn, user: CurrentUser):
    require_admin(user)
    data = request('POST', '/tokens/upload', data={'text': body.text}).json()
    return {'tokens_count': data.get('tokens_count')}


@router.post('/clear')
def clear(user: CurrentUser):
    require_admin(user)
    data = request('POST', '/tokens/clear').json()
    return {'tokens_count': data.get('tokens_count')}


@router.get('/errors')
def errors(user: CurrentUser):
    require_admin(user)
    data = request('POST', '/tokens/error').json()
    return {'error_tokens': data.get('error_tokens', [])}
