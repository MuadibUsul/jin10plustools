"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-21 17:41:02.606924

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """团队、账号、素材、图片"""
    op.create_table('teams',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_team_id'), 'users', ['team_id'], unique=False)
    op.create_table('assets',
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('mime', sa.String(length=50), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('team_id', 'id')
    )
    op.create_table('materials',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('source_item_id', sa.String(length=64), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('item_kind', sa.String(length=20), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('detail_url', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('flash_title', sa.Text(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_raw', sa.Text(), nullable=False),
    sa.Column('plus_title', sa.Text(), nullable=True),
    sa.Column('has_plus_link', sa.Boolean(), nullable=False),
    sa.Column('images', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('publish_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('raw_html', sa.Text(), nullable=False),
    sa.Column('collect_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('collect_method', sa.String(length=20), nullable=False),
    sa.Column('interpretation_status', sa.String(length=20), nullable=False),
    sa.Column('interpretation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('analyze_requested', sa.Boolean(), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=True),
    sa.Column('importance_score', sa.Integer(), nullable=False),
    sa.Column('final_score', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=1), nullable=True),
    sa.Column('ai_status', sa.String(length=20), nullable=False),
    sa.Column('ai_reason', sa.Text(), nullable=True),
    sa.Column('ai_error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('collected_by', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_materials_team_collect', 'materials', ['team_id', 'collect_time'], unique=False)
    op.create_index('ix_materials_team_sync', 'materials', ['team_id', 'updated_at', 'id'], unique=False)
    op.create_index('uq_materials_team_fingerprint', 'materials', ['team_id', 'fingerprint'], unique=True)
    op.create_index('uq_materials_team_item', 'materials', ['team_id', 'source_item_id'], unique=True, postgresql_where=sa.text('source_item_id IS NOT NULL'), sqlite_where=sa.text('source_item_id IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_materials_team_item', table_name='materials', postgresql_where=sa.text('source_item_id IS NOT NULL'), sqlite_where=sa.text('source_item_id IS NOT NULL'))
    op.drop_index('uq_materials_team_fingerprint', table_name='materials')
    op.drop_index('ix_materials_team_sync', table_name='materials')
    op.drop_index('ix_materials_team_collect', table_name='materials')
    op.drop_table('materials')
    op.drop_table('assets')
    op.drop_index(op.f('ix_users_team_id'), table_name='users')
    op.drop_table('users')
    op.drop_table('teams')
