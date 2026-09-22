"""parse reports

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """插件解析异常上报"""
    op.create_table(
        'parse_reports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('source_item_id', sa.String(length=64), nullable=True),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('html', sa.Text(), nullable=False),
        sa.Column('extension_version', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_parse_reports_team_created', 'parse_reports', ['team_id', 'created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_parse_reports_team_created', table_name='parse_reports')
    op.drop_table('parse_reports')
