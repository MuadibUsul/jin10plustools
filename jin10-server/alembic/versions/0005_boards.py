"""shared selection boards

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """团队共享选稿板：已选素材与手动排序"""
    op.create_table(
        'boards',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('picked', JSON, nullable=False),
        sa.Column('order', JSON, nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_boards_team_day', 'boards', ['team_id', 'day'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_boards_team_day', table_name='boards')
    op.drop_table('boards')
