"""
Core configuration — reads from environment variables / .env file
"""

from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Pharmacy Intelligence Mainframe"
    APP_VERSION: str = "1.0.0"
    BRANCH_NAME: str = "HealthBridge Lagos — Branch 001"
    DEBUG: bool = False
    SECRET_KEY: str

    # Database (PostgreSQL)
    DATABASE_URL: str = "postgresql://pharmacy_user:pharmacy_pass@localhost:5432/pharmacy_mainframe"

    # Redis (for caching FX rates, sessions)
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://healthbridge.ng",
    ]

    # FX Rate Sources
    ABOKIFX_URL: str = "https://abokifx.com/api/v1/rates/unofficial"
    FX_FALLBACK_RATE: float = 1578.0  # Hardcoded fallback if API fails
    FX_UPDATE_INTERVAL_HOURS: int = 6

    # NAFDAC Integration
    NAFDAC_API_URL: str = "https://api.nafdac.gov.ng/v1"
    NAFDAC_API_KEY: str = ""  # Set in .env

    # WhatsApp (Twilio or Meta Cloud API)
    WHATSAPP_PROVIDER: str = "meta"  # "twilio" or "meta"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"

    # Pharmacy
    DEFAULT_MARGIN: float = 0.25         # 25% margin target
    LOW_STOCK_THRESHOLD_DAYS: int = 7    # Alert when days of stock < 7
    EXPIRY_WARN_DAYS: int = 90           # Flag items expiring within 90 days
    REFILL_REMINDER_DAYS: int = 3        # Send WhatsApp X days before refill due

    # Procurement
    AUTO_PO_ENABLED: bool = True
    AUTO_PO_THRESHOLD_DAYS: int = 7     # Generate PO when stock < 7 days

    # Claude AI — FX Volatility Oracle
    ANTHROPIC_API_KEY: str = ""          # Set in .env — get from console.anthropic.com
    FX_VOLATILITY_THRESHOLD_PCT: float = 2.0   # Trigger Claude analysis at 2% swing
    FX_ALERT_ENABLED: bool = True               # Toggle the AI alert feature

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
