"""
POS Transaction Models — Phase 2
Separate from the existing DispensingRecord/BasketItem (Phase 1) to keep
Phase 2 clean and independently lockable.
"""

from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean,
    DateTime, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


class TransactionStatus(str, enum.Enum):
    open   = "open"      # within void window (< 2 min)
    locked = "locked"    # past void window; immutable
    voided = "voided"    # successfully voided within window


class SaleTransaction(Base):
    """POS hard-locked sale transaction (Phase 2)."""
    __tablename__ = "sale_transactions"

    id             = Column(Integer, primary_key=True, index=True)
    patient_id     = Column(Integer, ForeignKey("patients.id"), nullable=True)
    pharmacist     = Column(String(100), nullable=False)           # staff name / ID
    payment_method = Column(String(50), default="cash")            # cash, pos, transfer
    total_ngn      = Column(Numeric(12, 2), nullable=False)
    fx_rate        = Column(Numeric(10, 2), nullable=False)
    status         = Column(Enum(TransactionStatus), default=TransactionStatus.open, nullable=False)
    notes          = Column(Text)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    patient = relationship("Patient")
    items   = relationship("SaleTransactionItem", back_populates="transaction",
                           cascade="all, delete-orphan")


class SaleTransactionItem(Base):
    """Individual line item within a POS transaction (Phase 2)."""
    __tablename__ = "sale_transaction_items"

    id             = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("sale_transactions.id"), nullable=False)
    drug_id        = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    batch_id       = Column(Integer, ForeignKey("stock_batches.id"), nullable=False)
    quantity       = Column(Integer, nullable=False)
    unit_price_ngn = Column(Numeric(10, 2), nullable=False)
    total_ngn      = Column(Numeric(10, 2), nullable=False)

    # Relationships
    transaction = relationship("SaleTransaction", back_populates="items")
    drug        = relationship("Drug")
    batch       = relationship("StockBatch")
