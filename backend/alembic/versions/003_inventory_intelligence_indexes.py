"""Phase 1C — Inventory Intelligence performance indexes

No new tables are required for Phase 1C. All demand-forecasting and alert
logic is derived from existing tables (basket_items, dispensing_records,
stock_batches, refill_schedules, purchase_orders, procurement_lines).

This migration adds composite indexes that make the new service queries
fast at production data volumes:

  1. idx_basket_drug_dispense  — velocity calculation: filter by drug_id,
                                 join on dispensing_id
  2. idx_dispense_refund_date  — velocity calculation: filter non-refund rows
                                 within a date window
  3. idx_refill_drug_active    — chronic-drug check: filter by drug_id + active
  4. idx_po_autogen_status     — duplicate-PO guard: filter auto-generated POs
                                 by status
  5. idx_batch_drug_expiry_qty — current-stock calculation: filter by drug_id,
                                 expiry_date >= today, quantity > 0

Revision ID: 003_inventory_intelligence_indexes
Revises: 002_fx_alerts
Create Date: 2026-04-04
"""

from alembic import op

revision      = '003_inventory_intelligence_indexes'
down_revision = '002_fx_alerts'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # basket_items: (drug_id, dispensing_id) — velocity JOIN + filter
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_basket_drug_dispense
        ON basket_items (drug_id, dispensing_id)
    """)

    # dispensing_records: (is_refund, created_at) — exclude refunds + date range
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispense_refund_date
        ON dispensing_records (is_refund, created_at DESC)
    """)

    # refill_schedules: (drug_id, is_active) — chronic-drug lookup
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_refill_drug_active
        ON refill_schedules (drug_id, is_active)
    """)

    # purchase_orders: (auto_generated, status) — duplicate-PO guard
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_po_autogen_status
        ON purchase_orders (auto_generated, status)
    """)

    # stock_batches: (drug_id, expiry_date, quantity) — current-stock sum
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_batch_drug_expiry_qty
        ON stock_batches (drug_id, expiry_date, quantity)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_basket_drug_dispense")
    op.execute("DROP INDEX IF EXISTS idx_dispense_refund_date")
    op.execute("DROP INDEX IF EXISTS idx_refill_drug_active")
    op.execute("DROP INDEX IF EXISTS idx_po_autogen_status")
    op.execute("DROP INDEX IF EXISTS idx_batch_drug_expiry_qty")
