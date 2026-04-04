"""Phase 2 — POS Terminal with Hard Lock

Creates two new tables:
  - sale_transactions       (header: pharmacist, patient, total, fx_rate, status, timestamp)
  - sale_transaction_items  (line items: drug, batch, qty, price)

Adds a performance index on sale_transactions(created_at) for the daily
report query.

Revision ID: 004_pos_terminal_phase2
Revises: 003_inventory_intelligence_indexes
Create Date: 2026-04-04
"""

from alembic import op
import sqlalchemy as sa

revision      = '004_pos_terminal_phase2'
down_revision = '003_inventory_intelligence_indexes'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── sale_transactions ────────────────────────────────────────────────
    op.create_table(
        'sale_transactions',
        sa.Column('id',             sa.Integer(),       primary_key=True, autoincrement=True),
        sa.Column('patient_id',     sa.Integer(),       sa.ForeignKey('patients.id'), nullable=True),
        sa.Column('pharmacist',     sa.String(100),     nullable=False),
        sa.Column('payment_method', sa.String(50),      nullable=False, server_default='cash'),
        sa.Column('total_ngn',      sa.Numeric(12, 2),  nullable=False),
        sa.Column('fx_rate',        sa.Numeric(10, 2),  nullable=False),
        sa.Column(
            'status',
            sa.Enum('open', 'locked', 'voided', name='transactionstatus'),
            nullable=False,
            server_default='open',
        ),
        sa.Column('notes',          sa.Text(),          nullable=True),
        sa.Column('created_at',     sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )

    # Index for daily-report date-range queries
    op.create_index(
        'idx_sale_txn_created_at',
        'sale_transactions',
        ['created_at'],
    )

    # ── sale_transaction_items ───────────────────────────────────────────
    op.create_table(
        'sale_transaction_items',
        sa.Column('id',             sa.Integer(),      primary_key=True, autoincrement=True),
        sa.Column('transaction_id', sa.Integer(),
                  sa.ForeignKey('sale_transactions.id'), nullable=False),
        sa.Column('drug_id',        sa.Integer(),
                  sa.ForeignKey('drugs.id'),           nullable=False),
        sa.Column('batch_id',       sa.Integer(),
                  sa.ForeignKey('stock_batches.id'),   nullable=False),
        sa.Column('quantity',       sa.Integer(),      nullable=False),
        sa.Column('unit_price_ngn', sa.Numeric(10, 2), nullable=False),
        sa.Column('total_ngn',      sa.Numeric(10, 2), nullable=False),
    )

    # Index for joining items back to a transaction
    op.create_index(
        'idx_sale_items_txn_id',
        'sale_transaction_items',
        ['transaction_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_sale_items_txn_id',   table_name='sale_transaction_items')
    op.drop_table('sale_transaction_items')
    op.drop_index('idx_sale_txn_created_at', table_name='sale_transactions')
    op.drop_table('sale_transactions')
    op.execute("DROP TYPE IF EXISTS transactionstatus")
