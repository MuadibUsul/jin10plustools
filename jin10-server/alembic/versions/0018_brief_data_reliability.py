"""Add source revisions and coverage checks without changing existing records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('brief_configs',sa.Column('data_enabled',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.execute('UPDATE brief_configs SET data_enabled = enabled')
    op.create_table('brief_data_points',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id',ondelete='CASCADE'),nullable=False),
        sa.Column('category',sa.String(30),nullable=False),sa.Column('series',sa.String(80),nullable=False),
        sa.Column('method',sa.String(200),nullable=False),sa.Column('data_date',sa.Date(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('values',sa.JSON().with_variant(postgresql.JSONB(),'postgresql'),nullable=False),
        sa.Column('units',sa.JSON().with_variant(postgresql.JSONB(),'postgresql'),nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('job_id',sa.Uuid(),sa.ForeignKey('brief_jobs.id',ondelete='SET NULL')),
        sa.Column('source_snapshot_id',sa.Uuid(),sa.ForeignKey('brief_source_snapshots.id',ondelete='SET NULL')),
        sa.Column('collected_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('uq_brief_data_point_revision','brief_data_points',
                    ['team_id','category','series','method','data_date','revision'],unique=True)
    op.create_index('ix_brief_data_points_team_date','brief_data_points',['team_id','data_date'])
    op.create_table('brief_data_checks',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id',ondelete='CASCADE'),nullable=False),
        sa.Column('category',sa.String(30),nullable=False),sa.Column('data_date',sa.Date(),nullable=False),
        sa.Column('job_id',sa.Uuid(),sa.ForeignKey('brief_jobs.id',ondelete='SET NULL')),
        sa.Column('attempt',sa.Integer(),nullable=False),sa.Column('status',sa.String(30),nullable=False),
        sa.Column('report',sa.JSON().with_variant(postgresql.JSONB(),'postgresql'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('uq_brief_data_check_attempt','brief_data_checks',['job_id','attempt'],unique=True)
    op.create_index('ix_brief_data_checks_team_date','brief_data_checks',['team_id','data_date'])


def downgrade():
    op.drop_table('brief_data_checks')
    op.drop_table('brief_data_points')
    op.drop_column('brief_configs','data_enabled')
