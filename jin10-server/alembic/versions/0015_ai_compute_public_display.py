"""Keep the displayed precision and 7D summary for editor-requested reads."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_compute_observations', sa.Column('public_display', sa.JSON(), nullable=True))


def downgrade():
    # Historic source metadata remains available if an older app is restored.
    pass
