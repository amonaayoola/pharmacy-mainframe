"""add fx_alerts table

Revision ID: 002_fx_alerts
Revises: 001_initial
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = '002_fx_alerts'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'fx_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prev_rate', sa.Numeric(10, 2), nullable=False),
        sa.Column('new_rate', sa.Numeric(10, 2), nullable=False),
        sa.Column('change_pct', sa.Numeric(6, 3), nullable=False),
        sa.Column('direction', sa.String(12), nullable=False),
        sa.Column('claude_analysis', sa.Text(), nullable=False),
        sa.Column('drugs_affected_count', sa.Integer(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column(
            'triggered_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_fx_alerts_triggered', 'fx_alerts', ['triggered_at'])


def downgrade() -> None:
    op.drop_index('idx_fx_alerts_triggered', table_name='fx_alerts')
    op.drop_table('fx_alerts')
