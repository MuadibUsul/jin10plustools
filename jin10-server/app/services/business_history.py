"""Version only exportable business configuration, never provider credentials."""
import copy
import hashlib
import json
from sqlalchemy import select
from app.db.models import BriefConfig, TeamBusinessRevision


def record(db, team, user_id=None):
    saved=team.settings or {}
    config=db.get(BriefConfig,team.id)
    # Explicit allowlist: never copy the Team.settings dictionary containing AI keys.
    snapshot={key:copy.deepcopy(saved.get(key,{} if key in ('skills','brief_templates') else []))
              for key in ('skills','brief_templates','brief_note_revisions','retired_issue_notes')}
    snapshot['notes']=dict(config.notes or {}) if config else {}
    snapshot['schedule_enabled']=bool(config and config.enabled)
    snapshot['ai_provider']=saved.get('ai_provider','deepseek')
    snapshot['models']={name:entry.get('model') for name,entry in saved.get('ai',{}).items()}
    digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    latest=db.scalar(select(TeamBusinessRevision).where(TeamBusinessRevision.team_id==team.id)
        .order_by(TeamBusinessRevision.created_at.desc()).limit(1))
    if latest and latest.digest==digest:return latest
    row=TeamBusinessRevision(team_id=team.id,snapshot=snapshot,digest=digest,created_by=user_id)
    db.add(row);db.flush();return row
