"""Add feedback claims, resolutions, and per-user notification receipts."""
from alembic import op
import sqlalchemy as sa

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedback_mcp_credentials',
        sa.Column('team_id', sa.Uuid(), sa.ForeignKey('teams.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
    )
    op.add_column('feedback', sa.Column('claim_id', sa.Uuid()))
    op.add_column('feedback', sa.Column('claim_until', sa.DateTime()))
    op.add_column('feedback', sa.Column('completed_at', sa.DateTime()))
    op.add_column('feedback', sa.Column('resolution', sa.Text()))
    op.create_table(
        'feedback_notification_reads',
        sa.Column('feedback_id', sa.Uuid(), sa.ForeignKey('feedback.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('read_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('feedback_notification_reads')
    for column in ('resolution', 'completed_at', 'claim_until', 'claim_id'):
        op.drop_column('feedback', column)
    op.drop_table('feedback_mcp_credentials')
