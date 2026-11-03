"""Add retry provenance and content-addressed evidence without rewriting old bytes."""
from alembic import op
import sqlalchemy as sa

revision='0019'
down_revision='0018'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('brief_audits',sa.Column('details',sa.JSON(),nullable=True))
    op.add_column('brief_jobs',sa.Column('schedule_origin',sa.Boolean(),nullable=True))
    op.execute("UPDATE brief_jobs SET schedule_origin = (created_by IS NULL AND NOT preview AND action <> 'reconcile')")
    op.create_table('brief_source_blobs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id',ondelete='CASCADE'),nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('byte_count',sa.Integer(),nullable=False),
        sa.Column('content',sa.LargeBinary(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('uq_brief_source_blob_team_sha','brief_source_blobs',['team_id','sha256'],unique=True)
    op.add_column('brief_source_snapshots',sa.Column('blob_id',sa.Uuid(),nullable=True))
    op.create_foreign_key('fk_brief_snapshot_blob','brief_source_snapshots','brief_source_blobs',['blob_id'],['id'],ondelete='RESTRICT')
    op.create_index('ix_brief_source_snapshots_blob_id','brief_source_snapshots',['blob_id'])

def downgrade():
    raise RuntimeError('Evidence-preserving additive migration; automatic rollback is disabled')
