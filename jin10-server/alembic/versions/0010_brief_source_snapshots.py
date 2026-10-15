"""Store raw and normalized fixed-brief source data in PostgreSQL."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('brief_source_snapshots',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('version_id', sa.Uuid(), nullable=True),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('data_date', sa.Date(), nullable=False),
        sa.Column('relative_path', sa.String(length=1024), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('byte_count', sa.Integer(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['brief_jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['version_id'], ['brief_versions.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_brief_source_snapshots_team_id', 'brief_source_snapshots', ['team_id'])
    op.create_index('ix_brief_source_snapshots_job_id', 'brief_source_snapshots', ['job_id'])
    op.create_index('uq_brief_source_file', 'brief_source_snapshots',
                    ['job_id', 'attempt', 'relative_path'], unique=True)


def downgrade():
    op.drop_table('brief_source_snapshots')
