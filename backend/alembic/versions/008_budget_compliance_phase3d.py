"""Phase 3D — Budget & Compliance

Creates:
  - budget_tracking          (monthly spend tracking by category)
  - compliance_flags         (vendor/drug compliance issues)
  - monthly_reconciliation   (ordered vs received reconciliation)
  - seasonal_forecast        (demand multipliers per drug per month)

Revision ID: 008_budget_compliance_phase3d
Revises: 007_po_lifecycle_phase3c
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa

revision      = '008_budget_compliance_phase3d'
down_revision = '007_po_lifecycle_phase3c'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── budget_tracking ───────────────────────────────────────────────────
    op.create_table(
        'budget_tracking',
        sa.Column('id',         sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('month',      sa.Date(),          nullable=False),
        sa.Column('category',   sa.String(100),     nullable=False),
        sa.Column('budgeted',   sa.Numeric(14, 2),  server_default='0'),
        sa.Column('spent',      sa.Numeric(14, 2),  server_default='0'),
        sa.Column('variance',   sa.Numeric(14, 2),  server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('month', 'category', name='uq_budget_tracking_month_cat'),
    )
    op.create_index('idx_budget_tracking_month', 'budget_tracking', ['month'])

    # ── compliance_flags ──────────────────────────────────────────────────
    op.create_table(
        'compliance_flags',
        sa.Column('id',         sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('vendor_id',  sa.Integer(),    sa.ForeignKey('vendors.id'), nullable=True),
        sa.Column('drug_id',    sa.Integer(),    sa.ForeignKey('drugs.id'),   nullable=True),
        sa.Column('flag_type',  sa.String(50),   nullable=False),
        sa.Column('reason',     sa.Text(),       nullable=False),
        sa.Column('severity',   sa.String(20),   server_default='warning'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_compliance_vendor', 'compliance_flags', ['vendor_id'])
    op.create_index('idx_compliance_drug',   'compliance_flags', ['drug_id'])
    op.create_index('idx_compliance_type',   'compliance_flags', ['flag_type'])

    # ── monthly_reconciliation ────────────────────────────────────────────
    op.create_table(
        'monthly_reconciliation',
        sa.Column('id',              sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('month',           sa.Date(),          nullable=False,  unique=True),
        sa.Column('po_count',        sa.Integer(),       server_default='0'),
        sa.Column('total_ordered',   sa.Numeric(14, 2),  server_default='0'),
        sa.Column('total_received',  sa.Numeric(14, 2),  server_default='0'),
        sa.Column('discrepancies',   sa.Integer(),       server_default='0'),
        sa.Column('variance_pct',    sa.Numeric(6, 3),   server_default='0'),
        sa.Column('reconciled_by',   sa.String(100),     nullable=True),
        sa.Column('reconciled_at',   sa.DateTime(timezone=True), nullable=True),
    )

    # ── seasonal_forecast ─────────────────────────────────────────────────
    op.create_table(
        'seasonal_forecast',
        sa.Column('id',                sa.Integer(),      primary_key=True, autoincrement=True),
        sa.Column('drug_id',           sa.Integer(),      sa.ForeignKey('drugs.id'), nullable=False),
        sa.Column('month',             sa.Integer(),      nullable=False),   # 1-12
        sa.Column('demand_multiplier', sa.Numeric(5, 3),  server_default='1.000'),
        sa.Column('reason',            sa.String(200),    nullable=True),
        sa.Column('updated_at',        sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('drug_id', 'month', name='uq_seasonal_drug_month'),
    )
    op.create_index('idx_seasonal_drug', 'seasonal_forecast', ['drug_id'])


def downgrade() -> None:
    op.drop_index('idx_seasonal_drug',    table_name='seasonal_forecast')
    op.drop_table('seasonal_forecast')

    op.drop_table('monthly_reconciliation')

    op.drop_index('idx_compliance_type',   table_name='compliance_flags')
    op.drop_index('idx_compliance_drug',   table_name='compliance_flags')
    op.drop_index('idx_compliance_vendor', table_name='compliance_flags')
    op.drop_table('compliance_flags')

    op.drop_index('idx_budget_tracking_month', table_name='budget_tracking')
    op.drop_table('budget_tracking')
