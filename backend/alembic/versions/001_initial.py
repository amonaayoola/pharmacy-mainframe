"""initial schema

Revision ID: 001_initial
Revises: 
Create Date: 2026-04-02

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All tables are created via SQLAlchemy Base.metadata.create_all()
    # This migration just adds the performance indexes and views
    # that aren't handled by the ORM.

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_drugs_brand_trgm 
        ON drugs USING gin (brand_name gin_trgm_ops)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_expiry 
        ON stock_batches (expiry_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tx_created 
        ON stock_transactions (created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_refill_next_date 
        ON refill_schedules (next_refill_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispense_created 
        ON dispensing_records (created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_drugs_brand_trgm")
    op.execute("DROP INDEX IF EXISTS idx_stock_expiry")
    op.execute("DROP INDEX IF EXISTS idx_tx_created")
    op.execute("DROP INDEX IF EXISTS idx_refill_next_date")
    op.execute("DROP INDEX IF EXISTS idx_dispense_created")
