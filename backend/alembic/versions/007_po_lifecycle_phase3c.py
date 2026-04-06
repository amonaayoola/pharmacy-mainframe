"""Phase 3C — PO Lifecycle

Creates:
  - po_approvals     (approval audit trail per PO)
  - po_tracking      (immutable event log for PO status changes)

Revision ID: 007_po_lifecycle_phase3c
Revises: 006_procurement_rules_phase3b
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa

revision      = '007_po_lifecycle_phase3c'
down_revision = '006_procurement_rules_phase3b'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── po_approvals ──────────────────────────────────────────────────────
    op.create_table(
        'po_approvals',
        sa.Column('id',          sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('po_id',       sa.Integer(),    sa.ForeignKey('purchase_orders.id'), nullable=False),
        sa.Column('approver_id', sa.String(100),  nullable=False),
        sa.Column('status',      sa.String(20),   server_default='pending'),  # pending/approved/rejected
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes',       sa.Text(),        nullable=True),
    )
    op.create_index('idx_po_approvals_po', 'po_approvals', ['po_id'])

    # ── po_tracking ───────────────────────────────────────────────────────
    op.create_table(
        'po_tracking',
        sa.Column('id',        sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('po_id',     sa.Integer(),    sa.ForeignKey('purchase_orders.id'), nullable=False),
        sa.Column('event',     sa.String(100),  nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('notes',     sa.Text(),        nullable=True),
    )
    op.create_index('idx_po_tracking_po',   'po_tracking', ['po_id'])
    op.create_index('idx_po_tracking_time', 'po_tracking', ['timestamp'])


def downgrade() -> None:
    op.drop_index('idx_po_tracking_time', table_name='po_tracking')
    op.drop_index('idx_po_tracking_po',   table_name='po_tracking')
    op.drop_table('po_tracking')

    op.drop_index('idx_po_approvals_po', table_name='po_approvals')
    op.drop_table('po_approvals')
