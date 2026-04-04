"""Phase 3 — Auto-Procurement Intelligence

Creates:
  - vendors                  (vendor registry with performance score)
  - vendor_drug_prices       (per-vendor drug pricing, USD + NGN)
  - procurement_budgets      (monthly budget per drug category)

Alters:
  - purchase_orders          (adds vendor_id, approved_by, dispatched_at,
                               received_at, paid_at, budget_override,
                               override_reason; extends status enum)
  - procurement_lines        (adds vendor_id, unit_cost_ngn, total_ngn)

Revision ID: 005_auto_procurement_phase3
Revises: 004_pos_terminal_phase2
Create Date: 2026-04-04
"""

from alembic import op
import sqlalchemy as sa

revision      = '005_auto_procurement_phase3'
down_revision = '004_pos_terminal_phase2'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── vendors ──────────────────────────────────────────────────────────
    op.create_table(
        'vendors',
        sa.Column('id',                sa.Integer(),        primary_key=True, autoincrement=True),
        sa.Column('name',              sa.String(200),      nullable=False),
        sa.Column('contact_person',    sa.String(200),      nullable=True),
        sa.Column('phone',             sa.String(30),       nullable=True),
        sa.Column('email',             sa.String(200),      nullable=True),
        sa.Column('address',           sa.Text(),           nullable=True),
        sa.Column('lead_time_days',    sa.Integer(),        server_default='3'),
        sa.Column('performance_score', sa.Numeric(3, 1),    server_default='5.0'),
        sa.Column('is_active',         sa.Boolean(),        server_default='true'),
        sa.Column('created_at',        sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at',        sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_vendors_name',      'vendors', ['name'])
    op.create_index('idx_vendors_is_active', 'vendors', ['is_active'])

    # ── vendor_drug_prices ────────────────────────────────────────────────
    op.create_table(
        'vendor_drug_prices',
        sa.Column('id',             sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('vendor_id',      sa.Integer(),       sa.ForeignKey('vendors.id'),  nullable=False),
        sa.Column('drug_id',        sa.Integer(),       sa.ForeignKey('drugs.id'),    nullable=False),
        sa.Column('unit_price_ngn', sa.Numeric(12, 2),  nullable=False),
        sa.Column('unit_price_usd', sa.Numeric(10, 4),  nullable=True),
        sa.Column('last_updated',   sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_vdp_vendor_drug', 'vendor_drug_prices', ['vendor_id', 'drug_id'])

    # ── procurement_budgets ───────────────────────────────────────────────
    op.create_table(
        'procurement_budgets',
        sa.Column('id',                  sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('category',            sa.String(100),     nullable=False),
        sa.Column('year',                sa.Integer(),       nullable=False),
        sa.Column('month',               sa.Integer(),       nullable=False),
        sa.Column('monthly_budget_ngn',  sa.Numeric(14, 2),  nullable=False),
        sa.Column('spent_ngn',           sa.Numeric(14, 2),  server_default='0'),
        sa.Column('created_at',          sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at',          sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('idx_pb_category_ym', 'procurement_budgets', ['category', 'year', 'month'])

    # ── purchase_orders: new columns ──────────────────────────────────────
    op.add_column('purchase_orders',
        sa.Column('vendor_id',       sa.Integer(), sa.ForeignKey('vendors.id'), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('approved_by',     sa.String(100), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('dispatched_at',   sa.DateTime(timezone=True), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('received_at',     sa.DateTime(timezone=True), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('paid_at',         sa.DateTime(timezone=True), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('budget_override', sa.Boolean(), server_default='false'))
    op.add_column('purchase_orders',
        sa.Column('override_reason', sa.Text(), nullable=True))

    # Extend the existing postatus enum with new values
    # PostgreSQL requires ALTER TYPE … ADD VALUE (cannot be done inside a transaction)
    op.execute("COMMIT")
    op.execute("ALTER TYPE postatus ADD VALUE IF NOT EXISTS 'ordered'")
    op.execute("ALTER TYPE postatus ADD VALUE IF NOT EXISTS 'paid'")
    op.execute("BEGIN")

    # ── procurement_lines: new columns ─────────────────────────────────────
    op.add_column('procurement_lines',
        sa.Column('vendor_id',      sa.Integer(), sa.ForeignKey('vendors.id'), nullable=True))
    op.add_column('procurement_lines',
        sa.Column('unit_cost_ngn',  sa.Numeric(12, 2), nullable=True))
    op.add_column('procurement_lines',
        sa.Column('total_ngn',      sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    # procurement_lines
    op.drop_column('procurement_lines', 'total_ngn')
    op.drop_column('procurement_lines', 'unit_cost_ngn')
    op.drop_column('procurement_lines', 'vendor_id')

    # purchase_orders
    op.drop_column('purchase_orders', 'override_reason')
    op.drop_column('purchase_orders', 'budget_override')
    op.drop_column('purchase_orders', 'paid_at')
    op.drop_column('purchase_orders', 'received_at')
    op.drop_column('purchase_orders', 'dispatched_at')
    op.drop_column('purchase_orders', 'approved_by')
    op.drop_column('purchase_orders', 'vendor_id')

    # tables
    op.drop_index('idx_pb_category_ym',  table_name='procurement_budgets')
    op.drop_table('procurement_budgets')

    op.drop_index('idx_vdp_vendor_drug', table_name='vendor_drug_prices')
    op.drop_table('vendor_drug_prices')

    op.drop_index('idx_vendors_is_active', table_name='vendors')
    op.drop_index('idx_vendors_name',      table_name='vendors')
    op.drop_table('vendors')
