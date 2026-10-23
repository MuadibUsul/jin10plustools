"""Team-scoped Token and GPU rental observations for the two new fixed briefs."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_compute_observations',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('token_price', sa.Numeric(20, 6), nullable=True),
        sa.Column('a100', sa.Numeric(20, 6), nullable=True),
        sa.Column('h100', sa.Numeric(20, 6), nullable=True),
        sa.Column('b200', sa.Numeric(20, 6), nullable=True),
        sa.Column('source', sa.String(length=200), nullable=False),
        sa.Column('source_status', sa.String(length=100), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.CheckConstraint("kind IN ('token', 'gpu')", name='ck_ai_compute_kind'),
    )
    op.create_index('uq_ai_compute_team_kind_date', 'ai_compute_observations',
                    ['team_id', 'kind', 'date'], unique=True)
    op.create_index('ix_ai_compute_team_kind_date', 'ai_compute_observations',
                    ['team_id', 'kind', 'date'], unique=False)


def downgrade():
    # Imported source observations are historical business data; keep them on app rollback.
    pass
