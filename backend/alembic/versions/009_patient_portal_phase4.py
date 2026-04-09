"""Phase 4 — Patient Care Portal

Creates:
  - patient_sessions   (OTP + JWT session store)
  - patient_allergies  (patient-managed allergy registry)
  - refill_requests    (patient-initiated refill requests)

Revision ID: 009_patient_portal_phase4
Revises: 008_budget_compliance_phase3d
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa

revision      = '009_patient_portal_phase4'
down_revision = '008_budget_compliance_phase3d'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── patient_sessions ──────────────────────────────────────────────────
    op.create_table(
        'patient_sessions',
        sa.Column('id',             sa.Integer(),               primary_key=True, autoincrement=True),
        sa.Column('patient_id',     sa.Integer(),               sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('phone_number',   sa.String(30),              nullable=False),
        sa.Column('otp_hash',       sa.String(256),             nullable=True),
        sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('jwt_token',      sa.Text(),                  nullable=True),
        sa.Column('created_at',     sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_patient_sessions_phone',      'patient_sessions', ['phone_number'])
    op.create_index('idx_patient_sessions_patient_id', 'patient_sessions', ['patient_id'])

    # ── patient_allergies ─────────────────────────────────────────────────
    op.create_table(
        'patient_allergies',
        sa.Column('id',         sa.Integer(),               primary_key=True, autoincrement=True),
        sa.Column('patient_id', sa.Integer(),               sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('allergen',   sa.String(200),             nullable=False),
        sa.Column('severity',   sa.String(20),              server_default='mild', nullable=False),
        sa.Column('added_at',   sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
    )
    # Fast lookup at POS — most critical index in Phase 4
    op.create_index('idx_patient_allergies_patient_id', 'patient_allergies', ['patient_id'])

    # ── refill_requests ───────────────────────────────────────────────────
    op.create_table(
        'refill_requests',
        sa.Column('id',           sa.Integer(),               primary_key=True, autoincrement=True),
        sa.Column('patient_id',   sa.Integer(),               sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('drug_id',      sa.Integer(),               sa.ForeignKey('drugs.id'),    nullable=False),
        sa.Column('status',       sa.String(20),              server_default='pending', nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('notes',        sa.Text(),                  nullable=True),
    )
    op.create_index('idx_refill_requests_patient_id', 'refill_requests', ['patient_id'])
    op.create_index('idx_refill_requests_status',     'refill_requests', ['status'])


def downgrade() -> None:
    op.drop_index('idx_refill_requests_status',     table_name='refill_requests')
    op.drop_index('idx_refill_requests_patient_id', table_name='refill_requests')
    op.drop_table('refill_requests')

    op.drop_index('idx_patient_allergies_patient_id', table_name='patient_allergies')
    op.drop_table('patient_allergies')

    op.drop_index('idx_patient_sessions_patient_id', table_name='patient_sessions')
    op.drop_index('idx_patient_sessions_phone',      table_name='patient_sessions')
    op.drop_table('patient_sessions')
