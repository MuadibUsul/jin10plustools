"""Persist manual cover selections on shared boards."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
    op.add_column('boards', sa.Column('cover_ids', json_type, nullable=True))


def downgrade() -> None:
    op.drop_column('boards', 'cover_ids')
