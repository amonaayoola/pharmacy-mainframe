"""
backend/services/refill_outreach.py
Phase 1D – Patient Notification Service

Queues refill reminders, dispatches via WhatsApp (already integrated)
or SMS, and records delivery + read status in the outreach_log table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.refill_analytics import AdherenceProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

WHATSAPP_TEMPLATE = (
    "Hello {patient_name} 👋\n\n"
    "This is a friendly reminder from your pharmacy.\n\n"
    "📋 *Medication Due for Refill:*\n"
    "{drug_lines}\n"
    "Please visit us or reply *REFILL* to place your order.\n\n"
    "_Your health is our priority._ 🏥"
)

SMS_TEMPLATE = (
    "Hi {patient_name}, your medication refill is due: "
    "{drug_summary}. "
    "Please contact your pharmacy. Reply STOP to opt out."
)

DRUG_LINE_TEMPLATE = "  • {drug_name} – {days_left} day(s) remaining"


def _build_drug_lines(profiles: List[AdherenceProfile]) -> tuple[str, str]:
    """Return (whatsapp_lines, sms_summary) for a list of profiles."""
    lines, summaries = [], []
    for p in profiles:
        days = (
            str(int(p.days_supply_remaining)) if p.days_supply_remaining else "?"
        )
        lines.append(
            DRUG_LINE_TEMPLATE.format(drug_name=p.drug_name, days_left=days)
        )
        summaries.append(f"{p.drug_name} ({days}d left)")
    return "\n".join(lines), ", ".join(summaries)


def _render_message(
    channel: str,
    patient_name: str,
    profiles: List[AdherenceProfile],
    custom_message: Optional[str] = None,
) -> str:
    if custom_message:
        return custom_message

    drug_lines, drug_summary = _build_drug_lines(profiles)

    if channel == "whatsapp":
        return WHATSAPP_TEMPLATE.format(
            patient_name=patient_name,
            drug_lines=drug_lines,
        )
    return SMS_TEMPLATE.format(
        patient_name=patient_name,
        drug_summary=drug_summary,
    )


# ---------------------------------------------------------------------------
# Outreach service
# ---------------------------------------------------------------------------

class RefillOutreachService:
    """
    Queues and dispatches refill reminders for patients.

    Delivery pipeline
    -----------------
    1. Look up patient contact details (phone, WhatsApp opt-in status).
    2. Render message from template.
    3. Write an OUTREACH LOG record with status = 'queued'.
    4. Call the WhatsApp / SMS gateway (already integrated via
       backend.integrations.whatsapp_client / sms_client).
    5. Update log record: status → 'sent' | 'failed', gateway_message_id.
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def queue_reminder(
        self,
        patient_id: int,
        profiles: List[AdherenceProfile],
        channel: str = "whatsapp",
        custom_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Queue a refill reminder for a patient.

        Returns a dict with keys: success, message_id, queued_at.
        Raises on unrecoverable errors.
        """
        channel = channel.lower()
        if channel not in {"whatsapp", "sms"}:
            raise ValueError(f"Unsupported channel: {channel!r}")

        patient = self._fetch_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        phone = patient["phone"]
        patient_name = patient["name"]

        if channel == "whatsapp" and not patient.get("whatsapp_opted_in"):
            logger.warning(
                "Patient %s has not opted in to WhatsApp – falling back to SMS",
                patient_id,
            )
            channel = "sms"

        message_body = _render_message(
            channel, patient_name, profiles, custom_message
        )
        internal_id = str(uuid.uuid4())
        queued_at = datetime.now(timezone.utc).isoformat()

        self._log_outreach(
            log_id=internal_id,
            patient_id=patient_id,
            channel=channel,
            message_body=message_body,
            status="queued",
        )

        # Dispatch
        try:
            gateway_id = self._dispatch(channel, phone, message_body, internal_id)
            self._update_outreach_status(
                log_id=internal_id, status="sent", gateway_message_id=gateway_id
            )
            logger.info(
                "Refill reminder sent to patient=%s via %s (gateway_id=%s)",
                patient_id, channel, gateway_id,
            )
            return {"success": True, "message_id": internal_id, "queued_at": queued_at}

        except Exception as exc:  # noqa: BLE001
            self._update_outreach_status(
                log_id=internal_id, status="failed", error=str(exc)
            )
            logger.error(
                "Failed to send refill reminder to patient=%s: %s", patient_id, exc
            )
            raise

    def bulk_queue(
        self,
        profiles_by_patient: Dict[int, List[AdherenceProfile]],
        channel: str = "whatsapp",
    ) -> Dict[str, Any]:
        """
        Send reminders for multiple patients (called by the daily scheduler).
        Returns aggregated metrics.
        """
        sent, failed = 0, 0
        for patient_id, profiles in profiles_by_patient.items():
            try:
                self.queue_reminder(patient_id, profiles, channel=channel)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Bulk reminder failed for patient %s: %s", patient_id, exc)
                failed += 1

        return {
            "total": sent + failed,
            "sent": sent,
            "failed": failed,
            "channel": channel,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_delivery_report(
        self, patient_id: Optional[int] = None, hours: int = 24
    ) -> List[Dict[str, Any]]:
        """Return outreach log entries from the last N hours."""
        sql = text(
            """
            SELECT
                log_id,
                patient_id,
                channel,
                status,
                gateway_message_id,
                read_at,
                queued_at,
                error_detail
            FROM outreach_log
            WHERE queued_at >= NOW() - CAST(:hours AS INT) * INTERVAL '1 hour'
              AND (:patient_id IS NULL OR patient_id = :patient_id)
            ORDER BY queued_at DESC
            """
        )
        rows = self.db.execute(
            sql, {"hours": hours, "patient_id": patient_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_patient(self, patient_id: int) -> Optional[Dict[str, Any]]:
        sql = text(
            """
            SELECT
                id,
                first_name || ' ' || last_name AS name,
                phone,
                whatsapp_opted_in
            FROM patients
            WHERE id = :patient_id AND active = TRUE
            """
        )
        result = self.db.execute(sql, {"patient_id": patient_id}).first()
        return dict(result._mapping) if result else None

    def _log_outreach(
        self,
        log_id: str,
        patient_id: int,
        channel: str,
        message_body: str,
        status: str,
    ) -> None:
        sql = text(
            """
            INSERT INTO outreach_log
                (log_id, patient_id, channel, message_body, status, queued_at)
            VALUES
                (:log_id, :patient_id, :channel, :message_body, :status, NOW())
            """
        )
        self.db.execute(
            sql,
            {
                "log_id": log_id,
                "patient_id": patient_id,
                "channel": channel,
                "message_body": message_body,
                "status": status,
            },
        )
        self.db.commit()

    def _update_outreach_status(
        self,
        log_id: str,
        status: str,
        gateway_message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        sql = text(
            """
            UPDATE outreach_log
            SET
                status             = :status,
                gateway_message_id = :gateway_id,
                error_detail       = :error,
                sent_at            = CASE WHEN :status = 'sent' THEN NOW() ELSE NULL END
            WHERE log_id = :log_id
            """
        )
        self.db.execute(
            sql,
            {
                "log_id": log_id,
                "status": status,
                "gateway_id": gateway_message_id,
                "error": error,
            },
        )
        self.db.commit()

    def _dispatch(
        self, channel: str, phone: str, message_body: str, internal_id: str
    ) -> str:
        """
        Route to the appropriate messaging gateway.
        Both clients are already integrated in backend.integrations.*
        Returns the gateway's message ID for delivery tracking.
        """
        if channel == "whatsapp":
            from backend.integrations.whatsapp_client import WhatsAppClient  # noqa: PLC0415

            client = WhatsAppClient()
            return client.send_message(to=phone, body=message_body)

        from backend.integrations.sms_client import SMSClient  # noqa: PLC0415

        client = SMSClient()
        return client.send(to=phone, body=message_body)
