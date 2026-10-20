from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.briefs.delivery import default_notes
from app.briefs.policy import CATEGORIES
from app.briefs.skill_library import (
    MAX_INSTRUCTIONS, SkillConflict, SkillNotFound, list_skills, update_skill,
)
from app.db.models import BriefConfig, Role, Team, utcnow
from app.deps import CurrentUser, DbSession, Editor
from app.services.team_settings import cipher, provider, update_notes, chat2api_supported
from app.services.live_catalog import catalog

router = APIRouter(prefix='/api/settings', tags=['team settings'])


class AIConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str | None = Field(default=None, min_length=8, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[\w./:-]+$')
    enabled: bool | None = None
    use_server_key: bool = False


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    notes: dict[Literal[*CATEGORIES], str] | None = None
    schedule_enabled: bool | None = None
    ai: dict[Literal['jev', 'deepseek', 'chat2api', 'openai'], AIConfig] | None = None
    ai_provider: Literal['deepseek', 'chat2api', 'openai'] | None = None
    live_url: str | None = Field(default=None, max_length=2048)
    live_urls: dict[str,str] | None = None

    @field_validator('live_urls')
    @classmethod
    def validate_live_urls(cls,value):
        if value is None:return value
        if len(value)>40:raise ValueError('直播数量超过限制')
        for key,url in value.items():
            if len(key)>40 or len(url)>2048:raise ValueError('直播链接格式不正确')
            cls.validate_live_url(url)
        return {key:url.strip() for key,url in value.items()}

    @field_validator('live_url')
    @classmethod
    def validate_live_url(cls, value):
        if value is None or not value.strip():
            return value
        url = HttpUrl(value.strip())
        if url.scheme != 'https' or url.username or url.password:
            raise ValueError('直播链接必须是无账号密码的 HTTPS 地址')
        return str(url)


def output(db, team, user):
    config = db.get(BriefConfig, team.id)
    saved = team.settings or {}
    ai = {name: {key: value for key, value in provider(team, name).items() if key != 'key'}
          for name in ('jev', 'deepseek', 'chat2api', 'openai')}
    return {'revision': saved.get('settings_revision', 0), 'can_edit': user.role != Role.VIEWER,
            'can_admin': user.role == Role.ADMIN, 'schedule_enabled': bool(config and config.enabled),
            'categories': [{'id': key, 'label': label, 'notes': (config.notes or {}).get(key, default_notes(key)) if config else default_notes(key)}
                           for key, label in CATEGORIES.items()],
            'note_revisions': saved.get('brief_note_revisions', []), 'ai': ai,
            'ai_provider': saved.get('ai_provider', 'deepseek'),
            'live_url': saved.get('live_url', ''), 'live_catalog': catalog(team),
            'live_urls':saved.get('live_urls',{})}


@router.get('')
def read(db: DbSession, user: CurrentUser):
    return output(db, db.get(Team, user.team_id), user)


@router.put('')
def update(body: Update, db: DbSession, user: Editor):
    if user.role != Role.ADMIN:
        if body.schedule_enabled is not None:
            raise HTTPException(403, '仅管理员可修改排期')
        for change in (body.ai or {}).values():
            if change.key is not None or change.use_server_key or change.enabled is not None:
                raise HTTPException(403, '仅管理员可修改 AI Key 或启用状态')
    if body.notes and any(len(value) > 2000 for value in body.notes.values()):
        raise HTTPException(422, '每个栏目注释最多2000字')
    team = db.get(Team, user.team_id, with_for_update=True)
    if (team.settings or {}).get('settings_revision', 0) != body.revision:
        raise HTTPException(409, '设置已由其他成员更新，请刷新后重试')
    if body.notes is not None:
        update_notes(db, team, body.notes, user.id)
    if body.schedule_enabled is not None:
        config = db.get(BriefConfig, team.id)
        if config is None:
            config = BriefConfig(team_id=team.id, notes={})
            db.add(config)
        config.enabled = body.schedule_enabled
        config.updated_at = utcnow()
    saved = dict(team.settings or {})
    if body.live_urls is not None:
        known={item['id'] for item in catalog(team)['channels'] if item['id']!='custom'}
        if not set(body.live_urls)<=known:raise HTTPException(422,'直播条目不正确')
        saved['live_urls']=body.live_urls
    if body.live_url is not None:
        saved['live_url'] = body.live_url.strip()
    if body.ai_provider is not None:
        saved['ai_provider'] = body.ai_provider
    ai = dict(saved.get('ai', {}))
    for name, change in (body.ai or {}).items():
        current = dict(ai.get(name, {}))
        if change.use_server_key and change.key is not None:
            raise HTTPException(422, '不能同时替换 Key 和使用服务器 Key')
        if change.use_server_key:
            current.pop('token', None)
        if change.key is not None:
            key = change.key.strip()
            if len(key) < 8 or any(ch.isspace() for ch in key):
                raise HTTPException(422, 'Key 格式不正确')
            current['token'] = cipher().encrypt(key.encode()).decode()
            if not current.get('model') and not provider(team, name)['model']:
                current['model'] = {'deepseek': 'deepseek-chat', 'jev': 'jev-latest',
                                    'chat2api': 'gpt-4o-mini', 'openai': 'gpt-4o-mini'}.get(name, '')
        if change.model is not None:
            if name == 'chat2api' and not chat2api_supported(change.model):
                raise HTTPException(422, '当前 chat2api 节点不支持该模型，请选择 gpt-4o-mini、gpt-4o、o1、o3 等已支持模型')
            current['model'] = change.model
        if change.enabled is not None:
            current['enabled'] = change.enabled
        ai[name] = current
    team.settings = {**saved, 'ai': ai, 'settings_revision': body.revision + 1}
    db.commit()
    return output(db, team, user)


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    enabled: bool
    instructions: str = Field(default='', max_length=MAX_INSTRUCTIONS)


@router.get('/skills')
def skills(db: DbSession, user: CurrentUser):
    return {'skills': list_skills(db.get(Team, user.team_id))}


@router.get('/skills/{category}')
def skill_detail(category: str, db: DbSession, user: CurrentUser):
    from app.briefs.skill_library import SkillNotFound, effective
    try:
        return {'skill': effective(db.get(Team, user.team_id), category)}
    except SkillNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put('/skills/{category}')
def skill_update(category: str, body: SkillUpdate, db: DbSession, user: Editor):
    if user.role != Role.ADMIN:
        raise HTTPException(403, '仅管理员可修改 Skill 规范')
    team = db.get(Team, user.team_id, with_for_update=True)
    try:
        skill = update_skill(db, team, user, category, revision=body.revision,
                             enabled=body.enabled, instructions=body.instructions)
    except SkillNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SkillConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {'skill': skill}
