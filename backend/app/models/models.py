"""
Database Models — Pharmacy Intelligence Mainframe
PostgreSQL via SQLAlchemy ORM
"""

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime,
    Text, ForeignKey, Enum, ARRAY, JSON, Numeric
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class NAFDACStatus(str, enum.Enum):
    verified = "verified"
    pending = "pending"
    flagged = "flagged"
    counterfeit = "counterfeit"

class StockStatus(str, enum.Enum):
    ok = "ok"
    low = "low"
    critical = "critical"
    out = "out"
    expiring = "expiring"
    expired = "expired"
    promotion = "promotion"

class POStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    ordered = "ordered"
    received = "received"
    paid = "paid"
    cancelled = "cancelled"
    # legacy alias kept for backward compat
    sent = "sent"

class TransactionType(str, enum.Enum):
    sale = "sale"
    refund = "refund"
    adjustment = "adjustment"
    procurement = "procurement"

class WhatsAppMessageType(str, enum.Enum):
    refill_reminder = "refill_reminder"
    price_alert = "price_alert"
    promotion = "promotion"
    consultation = "consultation"


# ─────────────────────────────────────────────
# DRUG REGISTRY
# ─────────────────────────────────────────────

class Drug(Base):
    __tablename__ = "drugs"

    id = Column(Integer, primary_key=True, index=True)
    generic_name = Column(String(200), nullable=False, index=True)
    brand_name = Column(String(200), nullable=False)
    strength = Column(String(100))           # e.g. "500mg", "80mg/480mg"
    dosage_form = Column(String(100))        # Tablet, Capsule, Syrup, Injection
    nafdac_reg_no = Column(String(100), unique=True, index=True)
    manufacturer = Column(String(200))
    drug_class = Column(String(100))         # Antimalarial, Antibiotic, etc.
    tags = Column(ARRAY(String))             # ['ACT', 'antimalarial', 'prescription']
    requires_prescription = Column(Boolean, default=False)
    is_controlled = Column(Boolean, default=False)
    clinical_flags = Column(JSON, default={}) # {"conflict_with": ["VIT_C_HIGH"], "msg": "..."}
    cost_usd = Column(Numeric(10, 4), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    stock_batches = relationship("StockBatch", back_populates="drug")
    basket_items = relationship("BasketItem", back_populates="drug")
    procurement_lines = relationship("ProcurementLine", back_populates="drug")


# ─────────────────────────────────────────────
# STOCK / INVENTORY
# ─────────────────────────────────────────────

class StockBatch(Base):
    __tablename__ = "stock_batches"

    id = Column(Integer, primary_key=True, index=True)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    batch_no = Column(String(100), unique=True, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    unit_cost_usd = Column(Numeric(10, 4))
    expiry_date = Column(Date, nullable=False)
    manufacture_date = Column(Date)
    nafdac_status = Column(Enum(NAFDACStatus), default=NAFDACStatus.pending)
    verified_at = Column(DateTime(timezone=True))
    status = Column(Enum(StockStatus), default=StockStatus.ok)
    location = Column(String(100), default="Main Shelf")   # Shelf, Fridge, Controlled cabinet
    received_at = Column(DateTime(timezone=True), server_default=func.now())

    drug = relationship("Drug", back_populates="stock_batches")
    transactions = relationship("StockTransaction", back_populates="batch")


class StockTransaction(Base):
    """Every stock movement — sale, procurement, adjustment, expiry write-off."""
    __tablename__ = "stock_transactions"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("stock_batches.id"), nullable=False)
    transaction_type = Column(Enum(TransactionType), nullable=False)
    quantity_change = Column(Integer, nullable=False)  # Negative = consumed
    balance_after = Column(Integer, nullable=False)
    retail_price_ngn = Column(Numeric(12, 2))
    fx_rate_used = Column(Numeric(10, 2))
    dispensing_id = Column(Integer, ForeignKey("dispensing_records.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    batch = relationship("StockBatch", back_populates="transactions")


# ─────────────────────────────────────────────
# PATIENTS (World Model)
# ─────────────────────────────────────────────

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(200), nullable=False)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)  # WhatsApp ID
    date_of_birth = Column(Date)
    gender = Column(String(20))
    address = Column(Text)
    condition_tags = Column(ARRAY(String), default=[])  # ['Hypertension', 'Diabetes']
    allergies = Column(ARRAY(String), default=[])
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    whatsapp_opted_in = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    refill_schedules = relationship("RefillSchedule", back_populates="patient")
    dispensing_records = relationship("DispensingRecord", back_populates="patient")
    whatsapp_messages = relationship("WhatsAppMessage", back_populates="patient")


class RefillSchedule(Base):
    """30-day (or custom) chronic medication loops."""
    __tablename__ = "refill_schedules"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    cycle_days = Column(Integer, default=30)
    last_refill_date = Column(Date)
    next_refill_date = Column(Date)
    standard_qty = Column(Integer, default=30)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    patient = relationship("Patient", back_populates="refill_schedules")
    drug = relationship("Drug")


# ─────────────────────────────────────────────
# DISPENSING / POS
# ─────────────────────────────────────────────

class DispensingRecord(Base):
    """A completed transaction / receipt."""
    __tablename__ = "dispensing_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    served_by = Column(String(100))           # Staff name/ID
    total_ngn = Column(Numeric(12, 2), nullable=False)
    fx_rate = Column(Numeric(10, 2), nullable=False)
    clinical_audit_passed = Column(Boolean, nullable=False)
    audit_notes = Column(Text)
    payment_method = Column(String(50), default="cash")  # cash, pos, transfer
    receipt_qr_code = Column(String(500))      # Mainframe Verified QR
    is_refund = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    patient = relationship("Patient", back_populates="dispensing_records")
    items = relationship("BasketItem", back_populates="dispensing_record")


class BasketItem(Base):
    """Line items in a dispensing record."""
    __tablename__ = "basket_items"

    id = Column(Integer, primary_key=True, index=True)
    dispensing_id = Column(Integer, ForeignKey("dispensing_records.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("stock_batches.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price_ngn = Column(Numeric(10, 2), nullable=False)
    total_ngn = Column(Numeric(10, 2), nullable=False)
    margin_pct = Column(Numeric(5, 2))

    dispensing_record = relationship("DispensingRecord", back_populates="items")
    drug = relationship("Drug", back_populates="basket_items")


# ─────────────────────────────────────────────
# PROCUREMENT
# ─────────────────────────────────────────────

class Wholesaler(Base):
    __tablename__ = "wholesalers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    contact_person = Column(String(200))
    phone = Column(String(30))
    email = Column(String(200))
    address = Column(Text)
    rating = Column(Numeric(3, 1), default=5.0)
    lead_time_days = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)

    purchase_orders = relationship("PurchaseOrder", back_populates="wholesaler")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    wholesaler_id = Column(Integer, ForeignKey("wholesalers.id"), nullable=False)
    status = Column(Enum(POStatus), default=POStatus.draft)
    total_usd = Column(Numeric(12, 2))
    total_ngn = Column(Numeric(12, 2))
    fx_rate = Column(Numeric(10, 2))
    expected_delivery = Column(Date)
    auto_generated = Column(Boolean, default=False)  # True = created by Mainframe
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_at = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))

    # Phase 3 additions
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    approved_by = Column(String(100))
    dispatched_at = Column(DateTime(timezone=True))
    received_at = Column(DateTime(timezone=True))
    paid_at = Column(DateTime(timezone=True))
    budget_override = Column(Boolean, default=False)  # admin override flag
    override_reason = Column(Text)

    wholesaler = relationship("Wholesaler", back_populates="purchase_orders")
    vendor = relationship("Vendor", back_populates="purchase_orders")
    lines = relationship("ProcurementLine", back_populates="purchase_order")


class ProcurementLine(Base):
    __tablename__ = "procurement_lines"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)  # Phase 3
    quantity_ordered = Column(Integer, nullable=False)
    quantity_received = Column(Integer, default=0)
    unit_cost_usd = Column(Numeric(10, 4))
    unit_cost_ngn = Column(Numeric(12, 2))   # Phase 3: NGN at PO creation time
    total_usd = Column(Numeric(12, 2))
    total_ngn = Column(Numeric(14, 2))       # Phase 3

    purchase_order = relationship("PurchaseOrder", back_populates="lines")
    drug = relationship("Drug", back_populates="procurement_lines")
    vendor = relationship("Vendor")


# ─────────────────────────────────────────────
# FX RATES
# ─────────────────────────────────────────────

class FXRate(Base):
    __tablename__ = "fx_rates"

    id = Column(Integer, primary_key=True, index=True)
    usd_ngn = Column(Numeric(10, 2), nullable=False)
    source = Column(String(100), default="AbokiFX")
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())


class FXAlert(Base):
    """
    AI-generated volatility alert — created by Claude when the Naira
    swings by >= FX_VOLATILITY_THRESHOLD_PCT (default 2%) in a single period.
    Stored in PostgreSQL alongside all other pharmacy data.
    """
    __tablename__ = "fx_alerts"

    id = Column(Integer, primary_key=True, index=True)
    prev_rate = Column(Numeric(10, 2), nullable=False)      # Rate before the swing
    new_rate = Column(Numeric(10, 2), nullable=False)       # Rate that triggered alert
    change_pct = Column(Numeric(6, 3), nullable=False)      # e.g. 2.340
    direction = Column(String(12), nullable=False)          # "devaluation" or "appreciation"
    claude_analysis = Column(Text, nullable=False)          # Full Claude response
    drugs_affected_count = Column(Integer)                  # How many drugs were repriced
    model_used = Column(String(100), default="claude-sonnet-4-6")
    triggered_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────
# WHATSAPP MESSAGES
# ─────────────────────────────────────────────

class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    direction = Column(String(10))            # "outbound" or "inbound"
    message_type = Column(Enum(WhatsAppMessageType), nullable=True)
    body = Column(Text, nullable=False)
    wa_message_id = Column(String(200))       # External ID from WhatsApp API
    status = Column(String(50), default="sent")  # sent, delivered, read, failed
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

    patient = relationship("Patient", back_populates="whatsapp_messages")


# ─────────────────────────────────────────────
# NAFDAC VERIFICATION LOG
# ─────────────────────────────────────────────

class NAFDACVerification(Base):
    __tablename__ = "nafdac_verifications"

    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(100), nullable=False, index=True)
    nafdac_reg_no = Column(String(100))
    result = Column(Enum(NAFDACStatus), nullable=False)
    response_data = Column(JSON)               # Full API response stored
    verified_by = Column(String(100))
    verified_at = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────
# PHASE 3 — AUTO-PROCUREMENT INTELLIGENCE
# ─────────────────────────────────────────────

class Vendor(Base):
    """
    Drug vendor / supplier (replaces / extends Wholesaler for Phase 3).
    A Vendor can be linked to multiple drugs with specific pricing.
    """
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    contact_person = Column(String(200))
    phone = Column(String(30))
    email = Column(String(200))
    address = Column(Text)
    lead_time_days = Column(Integer, default=3)
    performance_score = Column(Numeric(3, 1), default=5.0)   # 0–10
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    drug_prices = relationship("VendorDrugPrice", back_populates="vendor")
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")


class VendorDrugPrice(Base):
    """Per-vendor unit pricing for each drug, stored in both USD and NGN."""
    __tablename__ = "vendor_drug_prices"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    unit_price_ngn = Column(Numeric(12, 2), nullable=False)
    unit_price_usd = Column(Numeric(10, 4))   # optional — populated at creation
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", back_populates="drug_prices")
    drug = relationship("Drug")


class ProcurementBudget(Base):
    """
    Monthly procurement budget per drug category.
    Tracks budget ceiling and running spend so the rules engine can
    block or flag PO approvals that would exceed the monthly limit.
    """
    __tablename__ = "procurement_budgets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(100), nullable=False, index=True)   # drug_class value
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)                       # 1–12
    monthly_budget_ngn = Column(Numeric(14, 2), nullable=False)
    spent_ngn = Column(Numeric(14, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
