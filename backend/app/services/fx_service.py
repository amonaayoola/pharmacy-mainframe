"""
FX Rate Service — Live NGN/USD parallel market rate
Sources: AbokiFX API → Redis cache → DB fallback → hardcoded fallback
"""

import httpx
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Simple in-memory cache (replace with Redis in production)
_fx_cache: dict = {"rate": None, "updated_at": None}


async def fetch_live_fx_rate() -> float:
    """
    Fetch the live USD/NGN parallel market rate from AbokiFX.
    Falls back to cached or hardcoded rate on failure.
    """
    # Check in-memory cache (< 6 hours old)
    if _fx_cache["rate"] and _fx_cache["updated_at"]:
        age_hours = (datetime.utcnow() - _fx_cache["updated_at"]).seconds / 3600
        if age_hours < settings.FX_UPDATE_INTERVAL_HOURS:
            return _fx_cache["rate"]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                settings.ABOKIFX_URL,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            # AbokiFX returns: {"rates": {"USD": {"buy": 1575, "sell": 1580}}}
            usd_sell = float(data["rates"]["USD"]["sell"])
            _fx_cache["rate"] = usd_sell
            _fx_cache["updated_at"] = datetime.utcnow()
            logger.info(f"✅ FX rate updated: 1 USD = ₦{usd_sell:,.2f}")
            return usd_sell

    except httpx.HTTPError as e:
        logger.warning(f"⚠️  AbokiFX request failed: {e}. Using cached/fallback rate.")
    except (KeyError, ValueError) as e:
        logger.warning(f"⚠️  AbokiFX parse error: {e}. Using cached/fallback rate.")

    # Return cached rate or hardcoded fallback
    if _fx_cache["rate"]:
        logger.info(f"Using cached FX rate: ₦{_fx_cache['rate']:,.2f}")
        return _fx_cache["rate"]

    logger.warning(f"Using hardcoded fallback FX rate: ₦{settings.FX_FALLBACK_RATE:,.2f}")
    return settings.FX_FALLBACK_RATE


def get_cached_fx_rate() -> float:
    """Synchronous getter for cached rate — used in price calculations."""
    return _fx_cache["rate"] or settings.FX_FALLBACK_RATE


def set_manual_fx_rate(rate: float) -> None:
    """Override the FX rate manually (for testing or CBN rate mode)."""
    _fx_cache["rate"] = rate
    _fx_cache["updated_at"] = datetime.utcnow()
    logger.info(f"Manual FX rate set: ₦{rate:,.2f}")


class PricingEngine:
    """
    Core pricing engine — protects margins against Naira volatility.
    All prices in the system flow through this class.
    """

    def __init__(self, fx_rate: Optional[float] = None, margin: float = None):
        self.fx_rate = fx_rate or get_cached_fx_rate()
        self.margin = margin or settings.DEFAULT_MARGIN

    def landed_cost_ngn(self, cost_usd: float) -> float:
        """Convert USD cost to NGN using parallel market rate."""
        return round(cost_usd * self.fx_rate, 2)

    def retail_price_ngn(self, cost_usd: float, margin: float = None) -> float:
        """
        Calculate retail price protecting the desired margin.
        Formula: retail = landed_cost / (1 - margin)
        Rounds to nearest ₦10 for clean pricing.
        """
        m = margin or self.margin
        landed = self.landed_cost_ngn(cost_usd)
        retail = landed / (1 - m)
        return round(retail / 10) * 10  # Round to nearest ₦10

    def margin_amount_ngn(self, cost_usd: float, margin: float = None) -> float:
        """NGN profit on one unit."""
        retail = self.retail_price_ngn(cost_usd, margin)
        landed = self.landed_cost_ngn(cost_usd)
        return round(retail - landed, 2)

    def margin_percentage(self, cost_usd: float, retail_ngn: float) -> float:
        """Back-calculate actual margin from a given retail price."""
        landed = self.landed_cost_ngn(cost_usd)
        if retail_ngn == 0:
            return 0
        return round((retail_ngn - landed) / retail_ngn * 100, 2)

    def price_all_drugs(self, drugs: list, margin: float = None) -> list:
        """
        Reprice all drugs with current FX rate.
        Returns list of dicts with updated prices.
        """
        m = margin or self.margin
        return [
            {
                "drug_id": d.id,
                "generic_name": d.generic_name,
                "brand_name": d.brand_name,
                "cost_usd": float(d.cost_usd),
                "landed_ngn": self.landed_cost_ngn(float(d.cost_usd)),
                "retail_ngn": self.retail_price_ngn(float(d.cost_usd), m),
                "margin_ngn": self.margin_amount_ngn(float(d.cost_usd), m),
                "margin_pct": m * 100,
                "fx_rate": self.fx_rate,
            }
            for d in drugs
        ]
