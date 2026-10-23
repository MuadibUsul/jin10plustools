"""Exportable ERP observations, backfilled from each team's latest archived series."""
import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        'valuation_observations',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('team_id', sa.Uuid(), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('close', sa.Numeric(20, 6), nullable=False),
        sa.Column('pe_ttm_mcw', sa.Numeric(20, 6), nullable=False),
        sa.Column('bond_yield_number', sa.Numeric(20, 6), nullable=False),
        sa.Column('erp', sa.Numeric(20, 12), nullable=False),
        sa.Column('source_status', sa.String(100)),
        sa.Column('source_job_id', sa.Uuid(), sa.ForeignKey('brief_jobs.id', ondelete='SET NULL')),
    )
    op.create_index('uq_valuation_observation_team_date', 'valuation_observations',
                    ['team_id', 'date'], unique=True)
    connection = op.get_bind()
    snapshot = sa.table('brief_source_snapshots',
        sa.column('team_id', sa.Uuid()), sa.column('job_id', sa.Uuid()),
        sa.column('relative_path', sa.String()), sa.column('content', sa.LargeBinary()),
        sa.column('sha256', sa.String()), sa.column('data_date', sa.Date()),
        sa.column('captured_at', sa.DateTime()))
    teams = connection.execute(sa.select(snapshot.c.team_id).where(
        snapshot.c.relative_path.in_(['erp-plot-data.json', 'origin/erp-plot-data.json'])
    ).distinct()).scalars()
    for team_id in teams:
        candidates = connection.execute(sa.select(snapshot).where(
            snapshot.c.team_id == team_id,
            snapshot.c.relative_path.in_(['erp-plot-data.json', 'origin/erp-plot-data.json']),
        ).order_by(snapshot.c.data_date.desc(), snapshot.c.captured_at.desc())).all()
        for candidate in candidates:
            try:
                if hashlib.sha256(candidate.content).hexdigest() != candidate.sha256:
                    continue
                source = json.loads(candidate.content.decode('utf-8-sig'))
                if not isinstance(source, list) or not source:
                    continue
                rows = []
                seen = set()
                for item in source:
                    day = date.fromisoformat(item['date'])
                    if day in seen or day > candidate.data_date:
                        raise ValueError('invalid date')
                    seen.add(day)
                    close = Decimal(str(item['close']))
                    pe = Decimal(str(item['pe_ttm_mcw']))
                    bond = Decimal(str(item['bond_yield_number']))
                    if not all(v.is_finite() for v in (close, pe, bond)) or close <= 0 or pe <= 0:
                        raise ValueError('invalid ERP input')
                    rows.append({'id': uuid.uuid4(), 'team_id': team_id, 'date': day,
                                 'close': close, 'pe_ttm_mcw': pe, 'bond_yield_number': bond,
                                 'erp': Decimal(1) / pe - bond / 100,
                                 'source_status': item.get('source_status'),
                                 'source_job_id': candidate.job_id})
                for offset in range(0, len(rows), 500):
                    op.bulk_insert(table, rows[offset:offset + 500])
                break
            except (KeyError, TypeError, ValueError, InvalidOperation, UnicodeError):
                continue


def downgrade():
    # Historical business observations should survive application rollback.
    pass
