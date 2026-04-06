"""
Phase 3 Procurement Models — Extended
Covers Phase 3A (vendor intelligence), 3B (rules engine), 3C (PO lifecycle), 3D (budget/compliance).
These are NEW tables that extend what's already in models.py.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime,
    Text, ForeignKey, Numeric, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3A — Vendor Intelligence
# ─────────────────────────────────────────────────────────────────────────────

class VendorCategory(Base):
    """Drug categories a vendor can supply (antibiotics, antivirals, etc.)."""
    __tablename__ = "vendor_categories"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    category = Column(String(100), nullable=False)

    vendor = relationship("Vendor", back_populates="categories")
    __table_args__ = (UniqueConstraint("vendor_id", "category", name="uq_vendor_category"),)


class VendorPerformance(Base):
    """Aggregated performance metrics per vendor."""
    __tablename__ = "vendor_performance"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, unique=True)
    on_time_delivery_pct = Column(Numeric(5, 2), default=100.0)   # 0-100
    quality_score = Column(Numeric(3, 1), default=5.0)            # 1-5
    reliability_rating = Column(Numeric(3, 1), default=5.0)       # 1-5
    price_competitiveness = Column(Numeric(3, 1), default=5.0)    # 1-5
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", back_populates="performance")


class VendorPricingHistory(Base):
    """Historical price quotes from vendors for each drug."""
    __tablename__ = "vendor_pricing_history"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)  # stored in NGN
    quoted_date = Column(DateTime(timezone=True), server_default=func.now())

    vendor = relationship("Vendor")
    drug = relationship("Drug")


class VendorRelationship(Base):
    """Relationship status and tier between pharmacy and vendor."""
    __tablename__ = "vendor_relationships"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, unique=True)
    status = Column(String(20), default="primary")      # primary / secondary / suspended
    discount_tier = Column(String(20), default="standard")  # standard / silver / gold / platinum
    notes = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", back_populates="vendor_relationship")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3B — Procurement Rules Engine
# ─────────────────────────────────────────────────────────────────────────────

class ProcurementRule(Base):
    """
    Rules engine — configurable procurement decision rules.
    rule_type: stock_based | vendor_based | budget_based
    condition/action are JSONB for flexible logic storage.
    """
    __tablename__ = "procurement_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    rule_type = Column(String(50), nullable=False)   # stock_based / vendor_based / budget_based
    condition = Column(JSONB, nullable=False, default={})
    action = Column(JSONB, nullable=False, default={})
    priority = Column(Integer, default=10)           # lower = higher priority
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BudgetLimit(Base):
    """Budget ceiling per category (and optionally per vendor)."""
    __tablename__ = "budget_limits"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(100), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    monthly_limit = Column(Numeric(14, 2), nullable=False)
    current_spent = Column(Numeric(14, 2), default=0)
    reset_date = Column(Date)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor")


class ApprovalThreshold(Base):
    """PO value thresholds that require specific approver roles."""
    __tablename__ = "approval_thresholds"

    id = Column(Integer, primary_key=True, index=True)
    threshold_amount = Column(Numeric(14, 2), nullable=False)
    required_approver_role = Column(String(100), nullable=False)  # pharmacist / manager / owner
    escalate_to_owner = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3C — PO Lifecycle (extended tracking)
# ─────────────────────────────────────────────────────────────────────────────

class POApproval(Base):
    """Approval audit trail per PO."""
    __tablename__ = "po_approvals"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    approver_id = Column(String(100), nullable=False)   # staff ID or name
    status = Column(String(20), default="pending")       # pending / approved / rejected
    approved_at = Column(DateTime(timezone=True))
    notes = Column(Text)

    purchase_order = relationship("PurchaseOrder", back_populates="approvals")


class POTracking(Base):
    """Immutable event log for PO status changes."""
    __tablename__ = "po_tracking"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    event = Column(String(100), nullable=False)  # created / submitted / approved / dispatched / received / cancelled
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(Text)

    purchase_order = relationship("PurchaseOrder", back_populates="tracking_events")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3D — Budget & Compliance
# ─────────────────────────────────────────────────────────────────────────────

class BudgetTracking(Base):
    """Monthly budget tracking by category."""
    __tablename__ = "budget_tracking"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Date, nullable=False)           # first day of the month
    category = Column(String(100), nullable=False)
    budgeted = Column(Numeric(14, 2), default=0)
    spent = Column(Numeric(14, 2), default=0)
    variance = Column(Numeric(14, 2), default=0)   # budgeted - spent
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("month", "category", name="uq_budget_tracking_month_cat"),)


class ComplianceFlag(Base):
    """
    Compliance issues flagged against vendors or drugs.
    severity: warning | block
    flag_type: blacklisted_vendor | expired_batch | temp_control_issue
    """
    __tablename__ = "compliance_flags"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=True)
    flag_type = Column(String(50), nullable=False)   # blacklisted_vendor / expired_batch / temp_control_issue
    reason = Column(Text, nullable=False)
    severity = Column(String(20), default="warning")  # warning / block
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vendor = relationship("Vendor")
    drug = relationship("Drug")


class MonthlyReconciliation(Base):
    """Monthly reconciliation between ordered and received goods."""
    __tablename__ = "monthly_reconciliation"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Date, nullable=False)
    po_count = Column(Integer, default=0)
    total_ordered = Column(Numeric(14, 2), default=0)
    total_received = Column(Numeric(14, 2), default=0)
    discrepancies = Column(Integer, default=0)       # number of lines with qty mismatch
    variance_pct = Column(Numeric(6, 3), default=0)
    reconciled_by = Column(String(100))
    reconciled_at = Column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("month", name="uq_reconciliation_month"),)


class SeasonalForecast(Base):
    """Seasonal demand multipliers per drug per month of year."""
    __tablename__ = "seasonal_forecast"

    id = Column(Integer, primary_key=True, index=True)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    month = Column(Integer, nullable=False)             # 1-12
    demand_multiplier = Column(Numeric(5, 3), default=1.0)
    reason = Column(String(200))                        # "malaria season", "flu season", etc.
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    drug = relationship("Drug")
    __table_args__ = (UniqueConstraint("drug_id", "month", name="uq_seasonal_drug_month"),)
