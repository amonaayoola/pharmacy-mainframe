"""
NAFDAC Verification Service
Authenticates drug batches against the NAFDAC national registry.
Falls back to local cache when API is unavailable.
"""

import httpx
import logging
from typing import Optional, Dict
from datetime import datetime, date

from app.core.config import settings
from app.models.models import NAFDACStatus

logger = logging.getLogger(__name__)

# Local verified registry — populated from NAFDAC database sync
# In production this is backed by the nafdac_verifications table
LOCAL_REGISTRY: Dict[str, Dict] = {
    "GS-2024-0891": {
        "drug": "Artemether 80mg/Lumefantrine 480mg",
        "manufacturer": "Novartis AG Switzerland",
        "nafdac_reg": "A7-0023-2021",
        "status": NAFDACStatus.verified,
        "expiry": "2025-12-15",
        "registered": "2021-03-10",
    },
    "GS-2024-1192": {
        "drug": "Paracetamol 500mg Tablet",
        "manufacturer": "May & Baker Nigeria Plc",
        "nafdac_reg": "A5-1192-2020",
        "status": NAFDACStatus.verified,
        "expiry": "2026-08-20",
        "registered": "2020-11-22",
    },
    "RX-2024-3311": {
        "drug": "Amoxicillin Trihydrate 500mg Capsule",
        "manufacturer": "Emzor Pharmaceutical Industries",
        "nafdac_reg": "A1-3311-2022",
        "status": NAFDACStatus.verified,
        "expiry": "2026-05-10",
        "registered": "2022-01-15",
    },
    "GF-2024-8812": {
        "drug": "Metformin Hydrochloride 500mg Tablet",
        "manufacturer": "Fidson Healthcare Plc",
        "nafdac_reg": "A3-8812-2021",
        "status": NAFDACStatus.verified,
        "expiry": "2026-11-30",
        "registered": "2021-07-08",
    },
    "NV-2024-4421": {
        "drug": "Amlodipine Besilate 10mg Tablet",
        "manufacturer": "Pfizer Inc",
        "nafdac_reg": "A2-4421-2023",
        "status": NAFDACStatus.verified,
        "expiry": "2026-02-28",
        "registered": "2023-02-01",
    },
    "PT-2024-9921": {
        "drug": "Ascorbic Acid (Vitamin C) 1000mg Effervescent",
        "manufacturer": "Pharmanord Denmark",
        "nafdac_reg": "S3-9921-2022",
        "status": NAFDACStatus.flagged,  # High-dose flagged for ACT interaction
        "expiry": "2025-09-15",
        "registered": "2022-06-14",
        "flag_reason": "High-dose formulation — clinical interaction with ACTs",
    },
    "COUNTERFEIT-0001": {
        "drug": "UNREGISTERED PARACETAMOL",
        "manufacturer": "UNKNOWN",
        "nafdac_reg": "NONE",
        "status": NAFDACStatus.counterfeit,
        "expiry": "N/A",
        "registered": "N/A",
        "flag_reason": "Confirmed counterfeit. Seized batch. Report to NAFDAC hotline: 0800-NAFDAC1",
    },
}


class NAFDACService:
    """
    Drug authentication service.
    Priority: 1) NAFDAC API → 2) Local cache → 3) Not-found response
    """

    def __init__(self):
        self.api_url = settings.NAFDAC_API_URL
        self.api_key = settings.NAFDAC_API_KEY

    async def verify_batch(self, batch_no: str, verified_by: str = "system") -> Dict:
        """
        Main verification method.
        Returns structured result dict.
        """
        batch_no = batch_no.strip().upper()

        # 1. Try NAFDAC live API if key is configured
        if self.api_key:
            api_result = await self._query_nafdac_api(batch_no)
            if api_result:
                logger.info(f"NAFDAC API verified: {batch_no}")
                return api_result

        # 2. Check local registry
        local = LOCAL_REGISTRY.get(batch_no)
        if local:
            result = {
                "batch_no": batch_no,
                "drug_name": local["drug"],
                "manufacturer": local["manufacturer"],
                "nafdac_reg_no": local["nafdac_reg"],
                "status": local["status"],
                "expiry_date": local.get("expiry", "N/A"),
                "registration_date": local.get("registered", "N/A"),
                "flag_reason": local.get("flag_reason"),
                "source": "local_registry",
                "verified_at": datetime.utcnow().isoformat(),
                "verified_by": verified_by,
                "message": self._build_message(local["status"], local.get("flag_reason")),
                "safe_to_dispense": local["status"] in [NAFDACStatus.verified],
            }
            logger.info(f"Local registry hit: {batch_no} → {local['status']}")
            return result

        # 3. Not found — treat as suspicious
        logger.warning(f"Batch not found in NAFDAC registry: {batch_no}")
        return {
            "batch_no": batch_no,
            "drug_name": "UNKNOWN",
            "manufacturer": "UNKNOWN",
            "nafdac_reg_no": None,
            "status": NAFDACStatus.pending,
            "source": "not_found",
            "verified_at": datetime.utcnow().isoformat(),
            "verified_by": verified_by,
            "message": f"Batch '{batch_no}' not found in NAFDAC database. "
                       "DO NOT DISPENSE until verified. "
                       "Report to NAFDAC: 0800-NAFDAC1 | nafdac.gov.ng",
            "safe_to_dispense": False,
        }

    async def _query_nafdac_api(self, batch_no: str) -> Optional[Dict]:
        """Hit the real NAFDAC API (when API key is available)."""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{self.api_url}/batch/{batch_no}",
                    headers={"X-API-Key": self.api_key, "Accept": "application/json"},
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()

                status_map = {
                    "REGISTERED": NAFDACStatus.verified,
                    "FLAGGED": NAFDACStatus.flagged,
                    "COUNTERFEIT": NAFDACStatus.counterfeit,
                    "SUSPENDED": NAFDACStatus.flagged,
                }
                status = status_map.get(data.get("status", ""), NAFDACStatus.pending)

                return {
                    "batch_no": batch_no,
                    "drug_name": data.get("productName", "Unknown"),
                    "manufacturer": data.get("manufacturer", "Unknown"),
                    "nafdac_reg_no": data.get("regNumber"),
                    "status": status,
                    "expiry_date": data.get("expiryDate"),
                    "registration_date": data.get("registrationDate"),
                    "source": "nafdac_api",
                    "verified_at": datetime.utcnow().isoformat(),
                    "message": self._build_message(status),
                    "safe_to_dispense": status == NAFDACStatus.verified,
                    "raw_response": data,
                }
        except httpx.RequestError as e:
            logger.warning(f"NAFDAC API unavailable: {e}")
            return None

    def _build_message(self, status: NAFDACStatus, flag_reason: str = None) -> str:
        messages = {
            NAFDACStatus.verified: "✅ AUTHENTIC — Batch is registered and verified with NAFDAC. Safe to dispense.",
            NAFDACStatus.flagged: f"⚠️ FLAGGED — Batch is registered but has an advisory flag. Reason: {flag_reason or 'See NAFDAC bulletin'}. Dispense with caution.",
            NAFDACStatus.counterfeit: "🚨 COUNTERFEIT DETECTED — DO NOT DISPENSE. Quarantine this batch immediately. Report to NAFDAC: 0800-NAFDAC1.",
            NAFDACStatus.pending: "🔍 PENDING — Batch status unconfirmed. Do not dispense until verified.",
        }
        return messages.get(status, "Status unknown. Verify manually.")

    def get_local_registry_summary(self) -> Dict:
        """Stats for the dashboard."""
        statuses = [v["status"] for v in LOCAL_REGISTRY.values()]
        return {
            "total_registered": len(LOCAL_REGISTRY),
            "verified": statuses.count(NAFDACStatus.verified),
            "flagged": statuses.count(NAFDACStatus.flagged),
            "counterfeit": statuses.count(NAFDACStatus.counterfeit),
        }


# Singleton
nafdac_service = NAFDACService()
