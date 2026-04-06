"""Phase 3B — Procurement Rules Engine

Creates:
  - vendor_categories        (categories each vendor supplies)
  - vendor_performance       (on_time/quality/reliability metrics)
  - vendor_pricing_history   (historical price quotes)
  - vendor_relationships     (primary/secondary/suspended status)
  - procurement_rules        (configurable decision rules with JSONB)
  - budget_limits            (per-category, per-vendor budget ceilings)
  - approval_thresholds      (PO value → required approver role)

Alters:
  - purchase_orders          (adds po_number, created_by, sent_date)

Revision ID: 006_procurement_rules_phase3b
Revises: 005_auto_procurement_phase3
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision      = '006_procurement_rules_phase3b'
down_revision = '005_auto_procurement_phase3'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── vendor_categories ─────────────────────────────────────────────────
    op.create_table(
        'vendor_categories',
        sa.Column('id',        sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('vendor_id', sa.Integer(),    sa.ForeignKey('vendors.id'), nullable=False),
        sa.Column('category',  sa.String(100),  nullable=False),
        sa.UniqueConstraint('vendor_id', 'category', name='uq_vendor_category'),
    )
    op.create_index('idx_vendor_categories_vendor', 'vendor_categories', ['vendor_id'])
    op.create_index('idx_vendor_categories_cat',    'vendor_categories', ['category'])

    # ── vendor_performance ────────────────────────────────────────────────
    op.create_table(
        'vendor_performance',
        sa.Column('id',                    sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('vendor_id',             sa.Integer(),       sa.ForeignKey('vendors.id'), nullable=False, unique=True),
        sa.Column('on_time_delivery_pct',  sa.Numeric(5, 2),   server_default='100.0'),
        sa.Column('quality_score',         sa.Numeric(3, 1),   server_default='5.0'),
        sa.Column('reliability_rating',    sa.Numeric(3, 1),   server_default='5.0'),
        sa.Column('price_competitiveness', sa.Numeric(3, 1),   server_default='5.0'),
        sa.Column('last_updated',          sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

    # ── vendor_pricing_history ────────────────────────────────────────────
    op.create_table(
        'vendor_pricing_history',
        sa.Column('id',          sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('vendor_id',   sa.Integer(),       sa.ForeignKey('vendors.id'), nullable=False),
        sa.Column('drug_id',     sa.Integer(),       sa.ForeignKey('drugs.id'),   nullable=False),
        sa.Column('unit_price',  sa.Numeric(12, 2),  nullable=False),
        sa.Column('quoted_date', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_vph_vendor_drug', 'vendor_pricing_history', ['vendor_id', 'drug_id'])

    # ── vendor_relationships ──────────────────────────────────────────────
    op.create_table(
        'vendor_relationships',
        sa.Column('id',            sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('vendor_id',     sa.Integer(),    sa.ForeignKey('vendors.id'), nullable=False, unique=True),
        sa.Column('status',        sa.String(20),   server_default='primary'),
        sa.Column('discount_tier', sa.String(20),   server_default='standard'),
        sa.Column('notes',         sa.Text(),       nullable=True),
        sa.Column('updated_at',    sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

    # ── procurement_rules ─────────────────────────────────────────────────
    op.create_table(
        'procurement_rules',
        sa.Column('id',         sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('name',       sa.String(200),  nullable=False),
        sa.Column('rule_type',  sa.String(50),   nullable=False),
        sa.Column('condition',  JSONB,           nullable=False, server_default='{}'),
        sa.Column('action',     JSONB,           nullable=False, server_default='{}'),
        sa.Column('priority',   sa.Integer(),    server_default='10'),
        sa.Column('active',     sa.Boolean(),    server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_rules_type_active', 'procurement_rules', ['rule_type', 'active'])

    # ── budget_limits ─────────────────────────────────────────────────────
    op.create_table(
        'budget_limits',
        sa.Column('id',            sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('category',      sa.String(100),     nullable=False),
        sa.Column('vendor_id',     sa.Integer(),       sa.ForeignKey('vendors.id'), nullable=True),
        sa.Column('monthly_limit', sa.Numeric(14, 2),  nullable=False),
        sa.Column('current_spent', sa.Numeric(14, 2),  server_default='0'),
        sa.Column('reset_date',    sa.Date(),          nullable=True),
        sa.Column('updated_at',    sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

    # ── approval_thresholds ───────────────────────────────────────────────
    op.create_table(
        'approval_thresholds',
        sa.Column('id',                      sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('threshold_amount',        sa.Numeric(14, 2),  nullable=False),
        sa.Column('required_approver_role',  sa.String(100),     nullable=False),
        sa.Column('escalate_to_owner',       sa.Boolean(),       server_default='false'),
        sa.Column('created_at',              sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

    # ── purchase_orders: new columns & nullability fix ────────────────────
    op.add_column('purchase_orders',
        sa.Column('po_number',   sa.String(50), nullable=True))
    op.create_index('idx_po_number', 'purchase_orders', ['po_number'], unique=True)
    op.add_column('purchase_orders',
        sa.Column('created_by',  sa.String(100), nullable=True))
    op.add_column('purchase_orders',
        sa.Column('sent_date',   sa.Date(), nullable=True))
    # Allow vendor-only POs (wholesaler_id becomes optional in Phase 3)
    op.alter_column('purchase_orders', 'wholesaler_id', nullable=True)


def downgrade() -> None:
    op.drop_column('purchase_orders', 'sent_date')
    op.drop_column('purchase_orders', 'created_by')
    op.drop_index('idx_po_number', table_name='purchase_orders')
    op.drop_column('purchase_orders', 'po_number')

    op.drop_table('approval_thresholds')
    op.drop_table('budget_limits')
    op.drop_index('idx_rules_type_active', table_name='procurement_rules')
    op.drop_table('procurement_rules')
    op.drop_table('vendor_relationships')
    op.drop_index('idx_vph_vendor_drug', table_name='vendor_pricing_history')
    op.drop_table('vendor_pricing_history')
    op.drop_table('vendor_performance')
    op.drop_index('idx_vendor_categories_cat',    table_name='vendor_categories')
    op.drop_index('idx_vendor_categories_vendor', table_name='vendor_categories')
    op.drop_table('vendor_categories')
