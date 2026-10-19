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


def provider(team, name):
    settings = get_settings()
    saved = ((team.settings or {}).get('ai', {}).get(name, {}) if team else {})
    fallback_key = settings.jev_api_key if name == 'jev' else settings.briefs_deepseek_api_key
    fallback_model = settings.jev_model if name == 'jev' else settings.briefs_deepseek_model
    key = cipher().decrypt(saved['token'].encode()).decode() if saved.get('token') else fallback_key
    return {'key': key if saved.get('enabled', True) else '',
            'model': saved.get('model', fallback_model), 'enabled': saved.get('enabled', True),
            'configured': bool(key), 'source': 'team' if saved.get('token') else 'server' if key else 'none'}


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
