"""Keep rejected observations as evidence, outside the production projection."""
from alembic import op
import sqlalchemy as sa

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('brief_data_points', sa.Column('quality_status', sa.String(20), nullable=False,
                                               server_default='accepted'))
    op.add_column('brief_data_points', sa.Column('quality_reason', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('brief_data_points', 'quality_reason')
    op.drop_column('brief_data_points', 'quality_status')
