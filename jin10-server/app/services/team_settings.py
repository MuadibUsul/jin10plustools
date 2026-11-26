"""Team configuration; provider secrets are encrypted and never serialized to clients."""
import base64
import hashlib
import os
import secrets
import tempfile

from functools import lru_cache
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.db.models import BriefConfig, utcnow
from app.briefs.delivery import default_notes

MAX_NOTE_REVISIONS = 50


@lru_cache(maxsize=8)
def _fernet_for(secret_str: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'plus-team-ai-v1\0' + secret_str.encode()).digest()))


@lru_cache(maxsize=8)
def _read_key_file(path_str: str, mtime_ns: int) -> str:
    from pathlib import Path
    secret = Path(path_str).read_text(encoding='ascii').strip()
    if len(secret) != 64:
        raise ValueError('Invalid persistent encryption key')
    return secret


def cipher(secret=None):
    settings = get_settings()
    if secret is None:
        secret = settings.team_key_secret
        if not secret:
            # This key belongs to the persistent data volume, not to login sessions.
            path = settings.storage_dir / '.team-ai-encryption-key'
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='.team-key-', dir=path.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='ascii') as output:
                        output.write(secrets.token_hex(32))
                    os.chmod(name, 0o600)
                    try:
                        os.link(name, path)  # First complete writer wins across processes.
                    except (FileExistsError, OSError):
                        if not path.exists():
                            os.replace(name, path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            stat = path.stat()
            secret = _read_key_file(str(path), stat.st_mtime_ns)
    return _fernet_for(secret)


def decrypt_token(token):
    try:return cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        # Read compatibility only; deployment migrates these before a JWT rotation.
        return cipher(get_settings().jwt_secret).decrypt(token.encode()).decode()


def migrate_tokens(db):
    from sqlalchemy import select
    from app.db.models import Team
    migrated=0;unreadable=0
    for team in db.scalars(select(Team).with_for_update()):
        ai={name:dict(value) for name,value in (team.settings or {}).get('ai',{}).items()}
        changed=False
        for value in ai.values():
            if not value.get('token'):continue
            try:cipher().decrypt(value['token'].encode())
            except InvalidToken:
                try:key=decrypt_token(value['token'])
                except (InvalidToken,ValueError,UnicodeError):
                    unreadable+=1;continue
                value['token']=cipher().encrypt(key.encode()).decode();changed=True;migrated+=1
        if changed:team.settings={**team.settings,'ai':ai}
    db.commit()
    return {'migrated':migrated,'unreadable':unreadable}


AI_PROVIDERS = ('deepseek', 'chat2api', 'openai')
DEFAULT_MODELS = {'jev': 'jev-latest', 'deepseek': 'deepseek-chat', 'chat2api': 'gpt-4o-mini', 'openai': 'gpt-4o-mini'}
# The node's ChatService maps unknown models to gpt-4o silently; only accept what it really routes.
CHAT2API_MODELS = ('gpt-3.5', 'gpt-4', 'gpt-4.5o', 'gpt-4o-canmore', 'gpt-4-mobile', 'o1', 'o3', 'auto')
CHAT2API_MODEL_IDS = frozenset((*CHAT2API_MODELS, 'gpt-3.5-turbo', 'gpt-4o', 'gpt-4o-mini', 'o1-mini', 'o3-mini'))


def chat2api_supported(model):
    model=(model or '').strip()
    return model in CHAT2API_MODEL_IDS


def chat2api_effective(model):
    return model if chat2api_supported(model) else 'gpt-4o'


def provider(team, name):
    settings = get_settings()
    saved = ((team.settings or {}).get('ai', {}).get(name, {}) if team else {})
    if name == 'jev':
        fallback_key, fallback_model = settings.jev_api_key, settings.jev_model
    elif name == 'chat2api':
        fallback_key, fallback_model = settings.chat2api_key, settings.chat2api_model
    elif name == 'openai':
        fallback_key, fallback_model = settings.openai_api_key, settings.openai_model
    else:
        fallback_key, fallback_model = settings.briefs_deepseek_api_key, settings.briefs_deepseek_model
    enabled=saved.get('enabled',True);error=None
    key=''
    if enabled:
        try:key=decrypt_token(saved['token']) if saved.get('token') else fallback_key
        except (InvalidToken,ValueError,OSError,UnicodeError):error='saved_key_unreadable'
    requested = saved.get('model', fallback_model)
    model = chat2api_effective(requested) if name == 'chat2api' else requested
    result = {'key': key if saved.get('enabled', True) else '',
              'model': model, 'enabled': saved.get('enabled', True),
              'configured': bool(saved.get('token') or fallback_key),
              'source': 'team' if saved.get('token') else 'server' if fallback_key else 'none'}
    if error:result['configuration_error']=error
    if name == 'chat2api':
        result.update({'requested_model': requested, 'model_supported': chat2api_supported(requested)})
    return result


def briefs_ai(team):
    """The admin-selected engine for fixed-brief wording, with its endpoint."""
    settings = get_settings()
    name = (team.settings or {}).get('ai_provider', 'deepseek') if team else 'deepseek'
    if name not in AI_PROVIDERS:
        name = 'deepseek'
    selected = provider(team, name)
    prefix = settings.chat2api_prefix.strip().strip('/')
    base_url = ('https://api.deepseek.com' if name == 'deepseek'
                else 'https://api.openai.com/v1' if name == 'openai'
                else f"{settings.chat2api_url.rstrip('/')}/{prefix}/v1" if prefix else '')
    return {**selected, 'provider': name, 'base_url': base_url}


def update_notes(db, team, notes, user_id, *, record_history: bool = True):
    config = db.get(BriefConfig, team.id)
    if config is None:
        config = BriefConfig(team_id=team.id, enabled=False, notes={})
        db.add(config)
    previous = dict(config.notes or {})
    effective = {**previous, **{key: previous.get(key, default_notes(key)) for key in notes}}
    changes = {key: text.strip() for key, text in notes.items() if effective.get(key) != text.strip()}
    if changes:
        saved = dict(team.settings or {})
        history = list(saved.get('brief_note_revisions', []))
        next_number = (int(history[-1].get('number', len(history))) + 1) if history else 1
        history.append({'number': next_number, 'at': utcnow().isoformat(), 'by': str(user_id),
                        'before': effective, 'after': {**effective, **changes}})
        team.settings = {**saved, 'brief_note_revisions': history[-MAX_NOTE_REVISIONS:]}
        config.notes = {**previous, **changes}
        config.updated_at = utcnow()
        if record_history:
            from app.services.business_history import record
            record(db, team, user_id)
    return config
