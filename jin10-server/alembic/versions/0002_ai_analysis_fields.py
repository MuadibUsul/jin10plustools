"""ai analysis fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21 17:59:40.973553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """AI 分析字段：栏目置信度、JEV 原始答案、尝试次数、分析时间"""
    op.add_column('materials', sa.Column('ai_confidence', sa.Float(), nullable=True))
    op.add_column('materials', sa.Column('ai_details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('materials', sa.Column('ai_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('materials', sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_materials_ai_queue', 'materials', ['ai_status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_materials_ai_queue', table_name='materials')
    op.drop_column('materials', 'analyzed_at')
    op.drop_column('materials', 'ai_attempts')
    op.drop_column('materials', 'ai_details')
    op.drop_column('materials', 'ai_confidence')
