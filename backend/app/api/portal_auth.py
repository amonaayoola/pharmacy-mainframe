"""
Patient Portal Auth Router — Phase 4
Endpoints: /api/portal/auth
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import patient_auth_service

router = APIRouter()


# ─────────────────────────────────────────────
# REQUEST / RESPONSE SCHEMAS
# ─────────────────────────────────────────────

class OTPRequest(BaseModel):
    phone_number: str


class OTPVerify(BaseModel):
    phone_number: str
    otp: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/request-otp", status_code=200)
def request_otp(payload: OTPRequest, db: Session = Depends(get_db)):
    """
    Send a 6-digit OTP to the patient's registered WhatsApp number.
    Returns 404 if no patient exists with that phone number.
    """
    return patient_auth_service.send_otp(payload.phone_number, db)


@router.post("/verify-otp", response_model=TokenOut, status_code=200)
def verify_otp(payload: OTPVerify, db: Session = Depends(get_db)):
    """
    Verify the OTP and return a signed JWT for portal access.
    The JWT must be sent as a Bearer token on all /api/portal/* requests.
    """
    return patient_auth_service.verify_otp(payload.phone_number, payload.otp, db)
