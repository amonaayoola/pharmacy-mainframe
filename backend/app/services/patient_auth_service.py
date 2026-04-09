"""
Patient Auth Service — Phase 4
WhatsApp OTP authentication for the patient portal.

Flow:
  1. send_otp(phone_number, db)       — generates 6-digit OTP, hashes + stores it, sends via WhatsApp
  2. verify_otp(phone_number, otp, db) — validates hash + expiry, returns signed JWT
  3. get_patient_from_token(token)     — decode JWT, return patient_id (used as dependency)
"""

import random
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.models import Patient
from app.models.portal_models import PatientSession
from app.services.whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 10
JWT_ALGORITHM      = "HS256"
JWT_EXPIRY_DAYS    = 30

_bearer = HTTPBearer()


# ─────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────

def _generate_otp() -> str:
    """Return a zero-padded 6-digit OTP string."""
    return str(random.randint(0, 999999)).zfill(6)


def _hash_otp(otp: str) -> str:
    """SHA-256 hash of the raw OTP."""
    return hashlib.sha256(otp.encode()).hexdigest()


def _issue_jwt(patient_id: int) -> str:
    payload = {
        "sub":      str(patient_id),
        "type":     "patient_portal",
        "exp":      datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat":      datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def send_otp(phone_number: str, db: Session) -> dict:
    """
    Generate a 6-digit OTP, hash it, persist in patient_sessions,
    and send it via WhatsApp.

    Raises 404 if no patient is registered with this phone number.
    """
    # Normalise phone: strip whitespace
    phone_number = phone_number.strip()

    # Look up the patient — phone_number is the WhatsApp identifier
    patient = (
        db.query(Patient)
        .filter(Patient.phone_number == phone_number)
        .first()
    )
    if not patient:
        raise HTTPException(
            status_code=404,
            detail="No patient account found for this phone number.",
        )

    otp       = _generate_otp()
    otp_hash  = _hash_otp(otp)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

    # Upsert: invalidate any existing pending session for this phone
    existing = (
        db.query(PatientSession)
        .filter(PatientSession.phone_number == phone_number)
        .order_by(PatientSession.created_at.desc())
        .first()
    )
    if existing:
        existing.otp_hash       = otp_hash
        existing.otp_expires_at = expires_at
        existing.jwt_token      = None
        session_row = existing
    else:
        session_row = PatientSession(
            patient_id     = patient.id,
            phone_number   = phone_number,
            otp_hash       = otp_hash,
            otp_expires_at = expires_at,
        )
        db.add(session_row)

    db.commit()

    # Send via WhatsApp
    message = (
        f"Your HealthBridge Portal OTP is *{otp}*.\n"
        f"It expires in {OTP_EXPIRY_MINUTES} minutes. Do not share it."
    )
    import asyncio
    try:
        asyncio.get_event_loop().run_until_complete(
            whatsapp_service.send_message(phone_number, message)
        )
    except RuntimeError:
        # In a sync FastAPI context there may already be a running loop; use a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(
                asyncio.run,
                whatsapp_service.send_message(phone_number, message)
            ).result()

    logger.info(f"OTP sent to patient_id={patient.id} phone={phone_number}")
    return {"detail": "OTP sent via WhatsApp.", "expires_in_minutes": OTP_EXPIRY_MINUTES}


def verify_otp(phone_number: str, otp: str, db: Session) -> dict:
    """
    Validate the OTP against the stored hash and expiry.
    On success, issue a JWT and persist it in patient_sessions.
    Returns {"access_token": ..., "token_type": "bearer"}.
    """
    phone_number = phone_number.strip()

    session_row = (
        db.query(PatientSession)
        .filter(PatientSession.phone_number == phone_number)
        .order_by(PatientSession.created_at.desc())
        .first()
    )

    if not session_row or not session_row.otp_hash:
        raise HTTPException(status_code=400, detail="No OTP request found. Please request a new OTP.")

    # Check expiry
    now = datetime.now(timezone.utc)
    expires = session_row.otp_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    # Validate hash
    if _hash_otp(otp.strip()) != session_row.otp_hash:
        raise HTTPException(status_code=400, detail="Invalid OTP.")

    # Issue JWT
    token = _issue_jwt(session_row.patient_id)

    # Persist token and clear OTP (one-time use)
    session_row.jwt_token      = token
    session_row.otp_hash       = None
    session_row.otp_expires_at = None
    db.commit()

    logger.info(f"JWT issued for patient_id={session_row.patient_id}")
    return {"access_token": token, "token_type": "bearer"}


def get_patient_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> int:
    """
    FastAPI dependency. Decodes the Bearer JWT and returns patient_id (int).
    Raises HTTP 401 on any invalid/expired token.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "patient_portal":
            raise HTTPException(status_code=401, detail="Invalid token type.")
        patient_id = int(payload["sub"])
        return patient_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
