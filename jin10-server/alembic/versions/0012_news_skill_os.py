"""News Skill OS：团队隔离的事件事实链、草稿修订、Skill 版本与工作流运行。

Additive only.  Existing daily/brief tables are untouched.  No downgrade drops
business data silently; rolling back the application should keep these tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.create_table('news_events',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_key', sa.String(length=200), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('current_revision', sa.Integer(), nullable=False),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint("status IN ('open','closed','archived')", name='ck_news_events_status'),
    )
    op.create_index('ix_news_events_team_id', 'news_events', ['team_id'], unique=False)
    op.create_index('uq_news_events_team_key', 'news_events', ['team_id', 'event_key'], unique=True,
                    postgresql_where=sa.text('event_key IS NOT NULL'),
                    sqlite_where=sa.text('event_key IS NOT NULL'))
    op.create_index('ix_news_events_team_time', 'news_events', ['team_id', 'event_time'], unique=False)

    op.create_table('news_event_revisions',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('snapshot', JSON, nullable=False),
        sa.Column('snapshot_sha256', sa.String(length=64), nullable=False),
        sa.Column('audit_reason', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_news_event_revisions_team_id', 'news_event_revisions', ['team_id'], unique=False)
    op.create_index('ix_news_event_revisions_event_id', 'news_event_revisions', ['event_id'], unique=False)
    op.create_index('uq_news_event_revision', 'news_event_revisions', ['event_id', 'number'], unique=True)
    op.create_index('ix_news_event_revisions_team_time', 'news_event_revisions', ['team_id', 'created_at'], unique=False)

    op.create_table('news_event_evidence',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('evidence_key', sa.String(length=200), nullable=False),
        sa.Column('source_key', sa.String(length=200), nullable=False),
        sa.Column('source_name', sa.Text(), nullable=False),
        sa.Column('source_kind', sa.String(length=30), nullable=False),
        sa.Column('source_original_id', sa.String(length=300), nullable=False),
        sa.Column('source_published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('excerpt', sa.Text(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('locator', sa.String(length=1024), nullable=True),
        sa.Column('source_snapshot_hash', sa.String(length=64), nullable=True),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['revision_id'], ['news_event_revisions.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_news_event_evidence_team_id', 'news_event_evidence', ['team_id'], unique=False)
    op.create_index('ix_news_event_evidence_event_id', 'news_event_evidence', ['event_id'], unique=False)
    op.create_index('ix_news_event_evidence_revision_id', 'news_event_evidence', ['revision_id'], unique=False)
    op.create_index('uq_news_evidence_revision_key', 'news_event_evidence', ['revision_id', 'evidence_key'], unique=True)
    op.create_index('ix_news_evidence_team_event', 'news_event_evidence', ['team_id', 'event_id', 'captured_at'], unique=False)

    op.create_table('news_event_claims',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('claim_key', sa.String(length=200), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('predicate', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('numeric', JSON, nullable=True),
        sa.Column('attributed_to', sa.Text(), nullable=True),
        sa.Column('superseded_by', sa.String(length=200), nullable=True),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['revision_id'], ['news_event_revisions.id'], ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('unverified','corroborated','verified','disputed','rejected','superseded')",
                           name='ck_news_claim_status'),
    )
    op.create_index('ix_news_event_claims_team_id', 'news_event_claims', ['team_id'], unique=False)
    op.create_index('ix_news_event_claims_event_id', 'news_event_claims', ['event_id'], unique=False)
    op.create_index('ix_news_event_claims_revision_id', 'news_event_claims', ['revision_id'], unique=False)
    op.create_index('uq_news_claim_revision_key', 'news_event_claims', ['revision_id', 'claim_key'], unique=True)
    op.create_index('ix_news_claims_team_event', 'news_event_claims', ['team_id', 'event_id', 'claim_key'], unique=False)

    op.create_table('news_event_claim_evidence',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('claim_id', sa.Uuid(), nullable=False),
        sa.Column('evidence_id', sa.Uuid(), nullable=False),
        sa.Column('stance', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['revision_id'], ['news_event_revisions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['claim_id'], ['news_event_claims.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['evidence_id'], ['news_event_evidence.id'], ondelete='CASCADE'),
        sa.CheckConstraint("stance IN ('supports','contradicts')", name='ck_news_link_stance'),
    )
    op.create_index('ix_news_event_claim_evidence_team_id', 'news_event_claim_evidence', ['team_id'], unique=False)
    op.create_index('ix_news_event_claim_evidence_event_id', 'news_event_claim_evidence', ['event_id'], unique=False)
    op.create_index('ix_news_event_claim_evidence_revision_id', 'news_event_claim_evidence', ['revision_id'], unique=False)
    op.create_index('uq_news_claim_evidence_pair', 'news_event_claim_evidence', ['claim_id', 'evidence_id'], unique=True)
    op.create_index('ix_news_claim_evidence_team_event', 'news_event_claim_evidence', ['team_id', 'event_id'], unique=False)

    op.create_table('news_event_analyses',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('analysis_key', sa.String(length=200), nullable=False),
        sa.Column('section', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('label', sa.String(length=20), nullable=False),
        sa.Column('claim_ids', JSON, nullable=False),
        sa.Column('evidence_ids', JSON, nullable=False),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['revision_id'], ['news_event_revisions.id'], ondelete='CASCADE'),
        sa.CheckConstraint("section IN ('前因','后果','补充说明')", name='ck_news_analysis_section'),
        sa.CheckConstraint("label IN ('fact','inference')", name='ck_news_analysis_label'),
    )
    op.create_index('ix_news_event_analyses_team_id', 'news_event_analyses', ['team_id'], unique=False)
    op.create_index('ix_news_event_analyses_event_id', 'news_event_analyses', ['event_id'], unique=False)
    op.create_index('ix_news_event_analyses_revision_id', 'news_event_analyses', ['revision_id'], unique=False)
    op.create_index('uq_news_analysis_revision_key', 'news_event_analyses', ['revision_id', 'analysis_key'], unique=True)
    op.create_index('ix_news_analyses_team_event', 'news_event_analyses', ['team_id', 'event_id', 'section'], unique=False)

    op.create_table('news_drafts',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('target', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('current_revision', sa.Integer(), nullable=False),
        sa.Column('skill_id', sa.String(length=200), nullable=True),
        sa.Column('skill_version', sa.String(length=30), nullable=True),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint("target IN ('fresh','annotation','brief_text','research')", name='ck_news_draft_target'),
        sa.CheckConstraint("status IN ('draft','pending_review','approved','rejected','archived')",
                           name='ck_news_draft_status'),
    )
    op.create_index('ix_news_drafts_team_id', 'news_drafts', ['team_id'], unique=False)
    op.create_index('ix_news_drafts_event_id', 'news_drafts', ['event_id'], unique=False)
    op.create_index('ix_news_drafts_team_updated', 'news_drafts', ['team_id', 'updated_at'], unique=False)
    op.create_index('ix_news_drafts_team_event', 'news_drafts', ['team_id', 'event_id'], unique=False)

    op.create_table('news_draft_revisions',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('draft_id', sa.Uuid(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('event_revision_id', sa.Uuid(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('skill_id', sa.String(length=200), nullable=True),
        sa.Column('skill_version', sa.String(length=30), nullable=True),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('review_status', sa.String(length=20), nullable=False),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.Uuid(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['draft_id'], ['news_drafts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_revision_id'], ['news_event_revisions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint("review_status IN ('pending','approved','rejected')", name='ck_news_draft_review_status'),
    )
    op.create_index('ix_news_draft_revisions_team_id', 'news_draft_revisions', ['team_id'], unique=False)
    op.create_index('ix_news_draft_revisions_draft_id', 'news_draft_revisions', ['draft_id'], unique=False)
    op.create_index('uq_news_draft_revision', 'news_draft_revisions', ['draft_id', 'number'], unique=True)
    op.create_index('ix_news_draft_revisions_team_time', 'news_draft_revisions', ['team_id', 'created_at'], unique=False)

    op.create_table('news_skill_versions',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('skill_id', sa.String(length=200), nullable=False),
        sa.Column('version', sa.String(length=30), nullable=False),
        sa.Column('digest', sa.String(length=64), nullable=False),
        sa.Column('manifest', JSON, nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_news_skill_versions_team_id', 'news_skill_versions', ['team_id'], unique=False)
    op.create_index('uq_news_skill_version', 'news_skill_versions', ['team_id', 'skill_id', 'version'], unique=True)
    op.create_index('ix_news_skill_versions_team_time', 'news_skill_versions', ['team_id', 'created_at'], unique=False)

    op.create_table('news_workflow_runs',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('run_key', sa.String(length=64), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('event_revision', sa.Integer(), nullable=True),
        sa.Column('workflow_id', sa.String(length=200), nullable=False),
        sa.Column('workflow_digest', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('input', JSON, nullable=False),
        sa.Column('input_digest', sa.String(length=64), nullable=False),
        sa.Column('output', JSON, nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['news_events.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','blocked','cancelled')",
                           name='ck_news_workflow_run_status'),
    )
    op.create_index('ix_news_workflow_runs_team_id', 'news_workflow_runs', ['team_id'], unique=False)
    op.create_index('uq_news_workflow_run_key', 'news_workflow_runs', ['run_key'], unique=True)
    op.create_index('ix_news_workflow_runs_team_time', 'news_workflow_runs', ['team_id', 'created_at'], unique=False)
    op.create_index('ix_news_workflow_runs_team_status', 'news_workflow_runs', ['team_id', 'status'], unique=False)

    op.create_table('news_workflow_nodes',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('node_id', sa.String(length=200), nullable=False),
        sa.Column('skill_id', sa.String(length=200), nullable=False),
        sa.Column('skill_version', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('input_digest', sa.String(length=64), nullable=False),
        sa.Column('output_digest', sa.String(length=64), nullable=True),
        sa.Column('source_snapshot_hashes', JSON, nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('cached', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('output', JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['news_workflow_runs.id'], ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','blocked','cancelled')",
                           name='ck_news_workflow_node_status'),
    )
    op.create_index('ix_news_workflow_nodes_team_id', 'news_workflow_nodes', ['team_id'], unique=False)
    op.create_index('ix_news_workflow_nodes_run_id', 'news_workflow_nodes', ['run_id'], unique=False)
    op.create_index('uq_news_workflow_node', 'news_workflow_nodes', ['run_id', 'node_id'], unique=True)
    op.create_index('ix_news_workflow_nodes_team_run', 'news_workflow_nodes', ['team_id', 'run_id'], unique=False)

    op.create_table('news_audit_events',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=True),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('actor_name', sa.String(length=100), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('details', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_news_audit_events_team_id', 'news_audit_events', ['team_id'], unique=False)
    op.create_index('ix_news_audit_team_time', 'news_audit_events', ['team_id', 'created_at'], unique=False)
    op.create_index('ix_news_audit_entity', 'news_audit_events', ['team_id', 'entity_type', 'entity_id'], unique=False)


TABLES = (
    'news_audit_events', 'news_workflow_nodes', 'news_workflow_runs', 'news_skill_versions',
    'news_draft_revisions', 'news_drafts', 'news_event_analyses',
    'news_event_claim_evidence', 'news_event_claims', 'news_event_evidence',
    'news_event_revisions', 'news_events',
)


def downgrade():
    """Drop only an empty schema; never destroy an existing fact chain.

    Application rollback should keep these tables and re-point the image.  A
    schema rollback is allowed only before the first event is written, which
    keeps disposable/test databases reversible without risking production data.
    """
    bind = op.get_bind()
    present = set(sa.inspect(bind).get_table_names())
    for table in TABLES:
        if table not in present:
            continue
        if bind.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first() is not None:
            raise RuntimeError(
                'news_skill_os contains historical data; roll back the application and keep these tables'
            )
    for table in TABLES:
        if table in present:
            op.drop_table(table)
