"""One-row-per-day editor entry and immutable job input."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_compute_observations', sa.Column('manual_display', sa.JSON(), nullable=True))
    op.add_column('brief_jobs', sa.Column('manual_capture', sa.JSON(), nullable=True))


def downgrade():
    # Preserve captured editorial observations and queued job inputs.
    pass
