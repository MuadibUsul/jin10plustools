"""使用者反馈：任何登录成员都可以提交，可附一张示意图，按团队隔离。"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.models import Feedback, FeedbackNotificationRead, Role, Team, User, utcnow
from app.deps import CurrentUser, DbSession
from app.services.storage import feedback_path, write_atomic

router = APIRouter(prefix='/api/feedback', tags=['feedback'])

MAX_TITLE = 200
MAX_CONTENT = 5000
MAX_NAME = 100


def sniff_image(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', 'jpg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    return None


def out(row: Feedback, accounts: dict[uuid.UUID, str], user: User) -> dict:
    return {
        'id': str(row.id), 'title': row.title, 'content': row.content, 'name': row.name,
        'account': accounts.get(row.user_id, '') if row.user_id else '',
        'created_at': row.created_at.isoformat(), 'mine': row.user_id == user.id,
        'has_image': bool(row.image_path), 'image_mime': row.image_mime,
        'image_bytes': row.image_bytes,
        **workflow_fields(row),
    }


def workflow_fields(row: Feedback) -> dict:
    status = 'completed' if row.completed_at else 'pending'
    if not row.completed_at and row.claim_until and row.claim_until.replace(tzinfo=None) > utcnow().replace(tzinfo=None):
        status = 'processing'
    return {
        'status': status, 'resolution': row.resolution,
        'completed_at': row.completed_at.isoformat() if row.completed_at else None,
    }


def feedback_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def admin_team(db: DbSession, user: CurrentUser) -> Team:
    if user.role != Role.ADMIN:
        raise HTTPException(403, '只有管理员可以管理反馈 MCP 凭据')
    return db.get(Team, user.team_id)


@router.get('/agent-token')
def token_status(db: DbSession, user: CurrentUser):
    return {'enabled': bool(admin_team(db, user).feedback_mcp_token_hash)}


@router.post('/agent-token')
def issue_token(db: DbSession, user: CurrentUser):
    team = admin_team(db, user)
    token = 'plus_feedback_' + secrets.token_urlsafe(32)
    team.feedback_mcp_token_hash = feedback_token_hash(token)
    db.commit()
    return {'token': token}


@router.delete('/agent-token', status_code=204)
def revoke_token(db: DbSession, user: CurrentUser):
    admin_team(db, user).feedback_mcp_token_hash = None
    db.commit()


@router.get('/notifications')
def notifications(db: DbSession, user: CurrentUser):
    read = select(FeedbackNotificationRead.feedback_id).where(
        FeedbackNotificationRead.user_id == user.id,
        FeedbackNotificationRead.feedback_id == Feedback.id,
    ).exists()
    rows = db.scalars(select(Feedback).where(
        Feedback.team_id == user.team_id, Feedback.completed_at.is_not(None), ~read,
    ).order_by(Feedback.completed_at.desc(), Feedback.id).limit(21)).all()
    return {'items': [{'id': str(row.id), 'title': row.title, **workflow_fields(row)}
                      for row in rows[:20]], 'has_more': len(rows) > 20}


@router.post('/notifications/{feedback_id}/read', status_code=204)
def read_notification(feedback_id: uuid.UUID, db: DbSession, user: CurrentUser):
    row = db.scalar(select(Feedback.id).where(
        Feedback.id == feedback_id, Feedback.team_id == user.team_id, Feedback.completed_at.is_not(None),
    ))
    if row is None:
        raise HTTPException(404, '未找到完成通知')
    if db.get(FeedbackNotificationRead, (row, user.id)) is None:
        try:
            with db.begin_nested():
                db.add(FeedbackNotificationRead(feedback_id=row, user_id=user.id))
                db.flush()
        except IntegrityError:
            if db.get(FeedbackNotificationRead, (row, user.id)) is None:
                raise
    db.commit()


def accounts_of(db: DbSession, team_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {member.id: member.name for member in db.scalars(select(User).where(User.team_id == team_id))}


@router.get('')
def items(db: DbSession, user: CurrentUser):
    rows = db.scalars(select(Feedback).where(Feedback.team_id == user.team_id)
                      .order_by(Feedback.created_at.desc()).limit(50)).all()
    accounts = accounts_of(db, user.team_id)
    return {'items': [out(row, accounts, user) for row in rows]}


@router.post('', status_code=201)
async def submit(db: DbSession, user: CurrentUser,
                 title: Annotated[str, Form()] = '',
                 content: Annotated[str, Form()] = '',
                 name: Annotated[str, Form()] = '',
                 image: Annotated[UploadFile | None, File()] = None):
    title, content, name = title.strip(), content.strip(), name.strip()
    if not title or len(title) > MAX_TITLE:
        raise HTTPException(422, f'标题不能为空且不超过 {MAX_TITLE} 字')
    if not content or len(content) > MAX_CONTENT:
        raise HTTPException(422, f'内容不能为空且不超过 {MAX_CONTENT} 字')
    if not name or len(name) > MAX_NAME:
        raise HTTPException(422, f'提交人姓名不能为空且不超过 {MAX_NAME} 字')
    data, sniffed = b'', None
    if image is not None and image.filename:
        limit = get_settings().max_feedback_bytes
        data = await image.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, '示意图过大，请压缩到 5MB 以内')
        sniffed = sniff_image(data)
        if sniffed is None:
            raise HTTPException(415, '示意图仅支持 PNG、JPEG 或 WebP')
    record = Feedback(team_id=user.team_id, user_id=user.id, name=name, title=title, content=content)
    db.add(record)
    db.flush()
    if sniffed:
        path = feedback_path(user.team_id, record.id, sniffed[1])
        write_atomic(path, data)
        record.image_path = str(path.relative_to(get_settings().storage_dir))
        record.image_mime = sniffed[0]
        record.image_bytes = len(data)
        record.image_sha256 = hashlib.sha256(data).hexdigest()
    db.commit()
    return out(record, accounts_of(db, user.team_id), user)


@router.get('/{feedback_id}/image')
def image(feedback_id: uuid.UUID, db: DbSession, user: CurrentUser):
    row = db.scalars(select(Feedback).where(Feedback.id == feedback_id,
                                            Feedback.team_id == user.team_id)).first()
    path = image_file(row)
    return FileResponse(path, media_type=row.image_mime or 'application/octet-stream',
                        headers={'Cache-Control': 'private, max-age=3600'})


def image_file(row: Feedback | None):
    if row is None or not row.image_path:
        raise HTTPException(404, '未找到示意图')
    root = get_settings().storage_dir.resolve()
    path = (root / row.image_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, '未找到示意图')
    return path
