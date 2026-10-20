"""Team configuration; provider secrets are encrypted and never serialized to clients."""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import get_settings
from app.db.models import BriefConfig, utcnow
from app.briefs.delivery import default_notes


def cipher():
    secret = get_settings().jwt_secret.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'plus-team-ai-v1\0' + secret).digest()))


AI_PROVIDERS = ('deepseek', 'chat2api', 'openai')
DEFAULT_MODELS = {'jev': 'jev-latest', 'deepseek': 'deepseek-chat', 'chat2api': 'gpt-4o-mini', 'openai': 'gpt-4o-mini'}
# The node's ChatService maps unknown models to gpt-4o silently; only accept what it really routes.
CHAT2API_MODELS = ('gpt-3.5', 'gpt-4', 'gpt-4.5o', 'gpt-4o-canmore', 'gpt-4-mobile', 'o1', 'o3', 'auto')


def chat2api_supported(model):
    model=(model or '').strip()
    return bool(model) and any(model.startswith(prefix) for prefix in CHAT2API_MODELS)


def chat2api_effective(model):
    return model if chat2api_supported(model) else 'gpt-4o-mini'


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
    key = cipher().decrypt(saved['token'].encode()).decode() if saved.get('token') else fallback_key
    requested = saved.get('model', fallback_model)
    model = chat2api_effective(requested) if name == 'chat2api' else requested
    result = {'key': key if saved.get('enabled', True) else '',
              'model': model, 'enabled': saved.get('enabled', True),
              'configured': bool(key), 'source': 'team' if saved.get('token') else 'server' if key else 'none'}
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


def update_notes(db, team, notes, user_id):
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
        history.append({'number': len(history) + 1, 'at': utcnow().isoformat(), 'by': str(user_id),
                        'before': effective, 'after': {**effective, **changes}})
        team.settings = {**saved, 'brief_note_revisions': history}
        config.notes = {**previous, **changes}
        config.updated_at = utcnow()
    return config
