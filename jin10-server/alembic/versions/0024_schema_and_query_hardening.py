"""Align feedback timestamps to TIMESTAMPTZ and add composite indexes for hot queries."""
from alembic import op
import sqlalchemy as sa

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.alter_column(
            'feedback', 'claim_until',
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using="claim_until AT TIME ZONE 'UTC'",
        )
        op.alter_column(
            'feedback', 'completed_at',
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using="completed_at AT TIME ZONE 'UTC'",
        )
        op.alter_column(
            'feedback_notification_reads', 'read_at',
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using="read_at AT TIME ZONE 'UTC'",
        )

    op.create_index('ix_issues_team_created', 'issues', ['team_id', 'created_at'])
    op.create_index('ix_brief_jobs_team_status', 'brief_jobs', ['team_id', 'status'])
    op.create_index('ix_brief_jobs_team_date_cat', 'brief_jobs', ['team_id', 'issue_date', 'category'])
    op.create_index('ix_brief_data_points_coverage', 'brief_data_points', ['team_id', 'category', 'data_date'])
    op.create_index('ix_brief_data_checks_lookup', 'brief_data_checks', ['team_id', 'category', 'data_date', 'created_at'])


def downgrade():
    op.drop_index('ix_brief_data_checks_lookup', table_name='brief_data_checks')
    op.drop_index('ix_brief_data_points_coverage', table_name='brief_data_points')
    op.drop_index('ix_brief_jobs_team_date_cat', table_name='brief_jobs')
    op.drop_index('ix_brief_jobs_team_status', table_name='brief_jobs')
    op.drop_index('ix_issues_team_created', table_name='issues')

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.alter_column(
            'feedback_notification_reads', 'read_at',
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using="read_at AT TIME ZONE 'UTC'",
        )
        op.alter_column(
            'feedback', 'completed_at',
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=True,
            postgresql_using="completed_at AT TIME ZONE 'UTC'",
        )
        op.alter_column(
            'feedback', 'claim_until',
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=True,
            postgresql_using="claim_until AT TIME ZONE 'UTC'",
        )
