"""Pin new image-only jobs to the edition selected by the editor."""
from alembic import op
import sqlalchemy as sa

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('brief_jobs', sa.Column('render_base_version', sa.Integer()))


def downgrade():
    op.drop_column('brief_jobs', 'render_base_version')
