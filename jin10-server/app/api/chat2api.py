"""Admin settings for the chat2api OpenAI-compatible node."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.models import Role
from app.deps import CurrentUser

router = APIRouter(prefix='/api/chat2api', tags=['chat2api'])
TOKEN_COUNT = re.compile(r'当前可用 Tokens 数量：<span class="text-blue-600">(\d+)</span>')
JWT = re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
REFRESH = re.compile(r'v1\.[A-Za-z0-9._-]{10,}')
COOKIE = re.compile(r'(?:__Secure-next-auth|next-auth)\.session-token=([^;\s"\']+)')
BEARER = re.compile(r'Bearer\s+([A-Za-z0-9._-]+)', re.I)
TOKEN_KEYS = {'accesstoken', 'refreshtoken'}


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


def extract_tokens(text: str) -> list[str]:
    """Keep only credentials from a whole session JSON, cookie line or token list."""
    found: list[str] = []
    seen: set[str] = set()

    def add(value):
        if not isinstance(value, str):
            return
        token = unquote(value.strip().strip('"\''))
        if token and token not in seen:
            seen.add(token)
            found.append(token)

    parsed = False
    try:
        data = json.loads(text)
        parsed = True
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        stack = [(data, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 6:
                continue
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and key.lower() in TOKEN_KEYS:
                        if value.startswith(('eyJ', 'v1.')):
                            add(value)
                    elif isinstance(value, (dict, list)):
                        stack.append((value, depth + 1))
            elif isinstance(node, list):
                stack.extend((item, depth + 1) for item in node)
    for pattern in (JWT, REFRESH, COOKIE, BEARER):
        for match in pattern.finditer(text):
            add(match.group(1) if match.groups() else match.group(0))
    if not found and not parsed:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                add(line)
    return found[:100]


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
    tokens = extract_tokens(body.text)
    if not tokens:
        raise HTTPException(422, '没有识别到 AccessToken 或 RefreshToken')
    data = request('POST', '/tokens/upload', data={'text': '\n'.join(tokens)}).json()
    return {'tokens_count': data.get('tokens_count'), 'extracted': len(tokens)}


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
