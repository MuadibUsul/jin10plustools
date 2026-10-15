"""Isolated fixed-brief tables. Frozen schema, independent of future model changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('brief_configs',
        sa.Column('team_id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    )
    op.create_table('brief_issues',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('confirmed_version', sa.Integer(), nullable=True),
        sa.Column('confirmed_by', sa.Uuid(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_brief_issues_team_id', 'brief_issues', ['team_id'], unique=False)
    op.create_index('uq_brief_issue', 'brief_issues', ['team_id', 'issue_date', 'category'], unique=True)
    op.create_table('brief_versions',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('issue_id', sa.Uuid(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('dynamic_body', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['brief_issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_brief_versions_issue_id', 'brief_versions', ['issue_id'], unique=False)
    op.create_index('uq_brief_version', 'brief_versions', ['issue_id', 'number'], unique=True)
    op.create_table('brief_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('preview', sa.Boolean(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=180), nullable=False),
        sa.Column('active_key', sa.String(length=180), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('active_key'),
    )
    op.create_index('ix_brief_job_queue', 'brief_jobs', ['status', 'next_attempt_at'], unique=False)
    op.create_index('ix_brief_jobs_team_id', 'brief_jobs', ['team_id'], unique=False)
    op.create_index('uq_brief_job_key', 'brief_jobs', ['dedupe_key'], unique=True)
    op.create_table('brief_audits',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('issue_id', sa.Uuid(), nullable=False),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['issue_id'], ['brief_issues.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_brief_audits_issue_id', 'brief_audits', ['issue_id'], unique=False)

def downgrade():
    op.drop_table('brief_audits')
    op.drop_table('brief_jobs')
    op.drop_table('brief_versions')
    op.drop_table('brief_issues')
    op.drop_table('brief_configs')
