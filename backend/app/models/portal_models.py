"""
Patient Portal Models — Phase 4
SQLAlchemy ORM models for patient sessions, allergies, and refill requests.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class PatientSession(Base):
    __tablename__ = "patient_sessions"

    id             = Column(Integer, primary_key=True, index=True)
    patient_id     = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    phone_number   = Column(String(30), nullable=False, index=True)
    otp_hash       = Column(String(256), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    jwt_token      = Column(Text, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    patient = relationship("Patient", foreign_keys=[patient_id])


class PatientAllergy(Base):
    __tablename__ = "patient_allergies"

    id         = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    allergen   = Column(String(200), nullable=False)
    severity   = Column(String(20), default="mild", nullable=False)   # mild/moderate/severe
    added_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    patient = relationship("Patient", foreign_keys=[patient_id])


class RefillRequest(Base):
    __tablename__ = "refill_requests"

    id           = Column(Integer, primary_key=True, index=True)
    patient_id   = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    drug_id      = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    status       = Column(String(20), default="pending", nullable=False)  # pending/approved/dispensed/cancelled
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes        = Column(Text, nullable=True)

    patient = relationship("Patient", foreign_keys=[patient_id])
    drug    = relationship("Drug",    foreign_keys=[drug_id])
