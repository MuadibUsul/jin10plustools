"""Append-only compute revisions and secret-free business settings history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid
import json
import hashlib

revision='0015'
down_revision='0014'
branch_labels=None
depends_on=None


def upgrade():
    payload=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.create_table('ai_compute_observation_revisions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id',ondelete='CASCADE'),nullable=False),
        sa.Column('observation_id',sa.Uuid(),sa.ForeignKey('ai_compute_observations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('date',sa.Date(),nullable=False),sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),sa.Column('values',payload,nullable=False),
        sa.Column('source',sa.String(200),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('uq_ai_compute_revision','ai_compute_observation_revisions',['observation_id','revision'],unique=True)
    op.create_index('ix_ai_compute_observation_revisions_team_id','ai_compute_observation_revisions',['team_id'])
    op.create_index('ix_ai_compute_observation_revisions_date','ai_compute_observation_revisions',['date'])
    op.create_table('team_business_revisions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id',ondelete='CASCADE'),nullable=False),
        sa.Column('snapshot',payload,nullable=False),sa.Column('digest',sa.String(64),nullable=False),
        sa.Column('created_by',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_team_business_revisions_team_id','team_business_revisions',['team_id'])
    op.create_index('ix_team_business_revisions_created_at','team_business_revisions',['created_at'])
    # Preserve the currently stored revision as a baseline; do not invent lost past values.
    connection=op.get_bind()
    metadata=sa.MetaData()
    observations=sa.Table('ai_compute_observations',metadata,autoload_with=connection)
    history=sa.Table('ai_compute_observation_revisions',metadata,autoload_with=connection)
    for row in connection.execute(sa.select(observations)).mappings():
        connection.execute(history.insert().values(id=uuid.uuid4(),team_id=row['team_id'],
            observation_id=row['id'],date=row['date'],kind=row['kind'],revision=row['revision'],
            values={k:str(row[k]) if row[k] is not None else None for k in ('token_price','a100','h100','b200')},
            source=row['source'],created_at=row['updated_at']))
    teams=sa.Table('teams',metadata,autoload_with=connection)
    configs=sa.Table('brief_configs',metadata,autoload_with=connection)
    business=sa.Table('team_business_revisions',metadata,autoload_with=connection)
    from datetime import datetime,timezone
    for row in connection.execute(sa.select(teams)).mappings():
        saved=row['settings'] or {}
        config=connection.execute(sa.select(configs).where(configs.c.team_id==row['id'])).mappings().first()
        snapshot={key:saved.get(key,{} if key in ('skills','brief_templates') else [])
                  for key in ('skills','brief_templates','brief_note_revisions','retired_issue_notes')}
        snapshot.update(notes=(config['notes'] or {}) if config else {},schedule_enabled=bool(config and config['enabled']),
            ai_provider=saved.get('ai_provider','deepseek'),models={k:v.get('model') for k,v in saved.get('ai',{}).items()})
        connection.execute(business.insert().values(id=uuid.uuid4(),team_id=row['id'],snapshot=snapshot,
            digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),created_at=datetime.now(timezone.utc)))


def downgrade():
    pass  # Business history is retained on application rollback.
