"""
backend/services/refill_analytics.py
Phase 1D – Patient Refill Intelligence
Predict medication refill dates, calculate adherence, and identify patients
at risk of running out within the next 7 days.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class AdherenceProfile:
    patient_id: int
    drug_id: int
    drug_name: str
    avg_daily_consumption: float          # units/day
    adherence_rate: float                 # 0.0 – 1.0
    last_dispense_date: Optional[date]
    last_dispense_qty: int
    days_supply_remaining: float
    predicted_stockout_date: Optional[date]
    days_until_stockout: Optional[int]
    at_risk: bool                         # True  →  stockout within 7 days
    refill_due_date: Optional[date]       # suggested refill trigger date


@dataclass
class RefillSummary:
    total_patients_analysed: int
    at_risk_count: int
    due_today: int
    due_in_3_days: int
    due_in_7_days: int
    generated_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class RefillAnalyticsEngine:
    """
    Calculates medication adherence and predicts stockout dates for every
    active patient–drug combination that has dispensing history.
    """

    RISK_WINDOW_DAYS: int = 7          # flag patients running out within N days
    REFILL_LEAD_DAYS: int = 3          # prompt refill N days before stockout
    MIN_DISPENSES_FOR_PREDICTION: int = 2  # need ≥ this many fills to predict

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_at_risk_patients(self) -> List[AdherenceProfile]:
        """Return all patients predicted to run out within RISK_WINDOW_DAYS."""
        profiles = self._build_all_profiles()
        return [p for p in profiles if p.at_risk]

    def get_patient_profiles(self, patient_id: int) -> List[AdherenceProfile]:
        """Return adherence profiles for every drug a specific patient uses."""
        return self._build_all_profiles(patient_id=patient_id)

    def get_summary(self) -> RefillSummary:
        profiles = self._build_all_profiles()
        at_risk = [p for p in profiles if p.at_risk]
        today = date.today()
        return RefillSummary(
            total_patients_analysed=len(profiles),
            at_risk_count=len(at_risk),
            due_today=sum(
                1 for p in at_risk
                if p.refill_due_date and p.refill_due_date <= today
            ),
            due_in_3_days=sum(
                1 for p in at_risk
                if p.refill_due_date
                and today < p.refill_due_date <= today + timedelta(days=3)
            ),
            due_in_7_days=sum(
                1 for p in at_risk
                if p.refill_due_date
                and today < p.refill_due_date <= today + timedelta(days=7)
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_all_profiles(
        self, patient_id: Optional[int] = None
    ) -> List[AdherenceProfile]:
        rows = self._fetch_dispense_history(patient_id=patient_id)
        # group by (patient_id, drug_id)
        groups: dict[tuple[int, int], list] = {}
        for row in rows:
            key = (row.patient_id, row.drug_id)
            groups.setdefault(key, []).append(row)

        profiles: List[AdherenceProfile] = []
        for (pid, did), dispenses in groups.items():
            try:
                profile = self._compute_profile(pid, did, dispenses)
                profiles.append(profile)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipped profile for patient=%s drug=%s: %s", pid, did, exc
                )
        return profiles

    def _fetch_dispense_history(self, patient_id: Optional[int] = None):
        """
        Pull the last 90 days of dispensing records.
        Expects a table  dispensing_records  with columns:
            patient_id, drug_id, drug_name, quantity_dispensed, dispensed_at
        """
        sql = text(
            """
            SELECT
                dr.patient_id,
                dr.drug_id,
                d.name          AS drug_name,
                dr.quantity_dispensed,
                dr.dispensed_at::date AS dispense_date
            FROM dispensing_records dr
            JOIN drugs d ON d.id = dr.drug_id
            WHERE dr.dispensed_at >= CURRENT_DATE - INTERVAL '90 days'
              AND (:patient_id IS NULL OR dr.patient_id = :patient_id)
            ORDER BY dr.patient_id, dr.drug_id, dr.dispensed_at
            """
        )
        return self.db.execute(sql, {"patient_id": patient_id}).fetchall()

    def _compute_profile(
        self, patient_id: int, drug_id: int, dispenses: list
    ) -> AdherenceProfile:
        # sort chronologically
        dispenses = sorted(dispenses, key=lambda r: r.dispense_date)
        drug_name = dispenses[-1].drug_name
        last_dispense = dispenses[-1]

        if len(dispenses) < self.MIN_DISPENSES_FOR_PREDICTION:
            # not enough history – treat remaining stock as 0
            return self._insufficient_history_profile(
                patient_id, drug_id, drug_name, last_dispense
            )

        avg_daily, adherence = self._calc_consumption(dispenses)
        days_remaining = self._calc_days_remaining(
            last_dispense, avg_daily
        )
        stockout_date = (
            date.today() + timedelta(days=days_remaining)
            if days_remaining is not None
            else None
        )
        days_until = (
            (stockout_date - date.today()).days if stockout_date else None
        )
        at_risk = days_until is not None and days_until <= self.RISK_WINDOW_DAYS
        refill_due = (
            stockout_date - timedelta(days=self.REFILL_LEAD_DAYS)
            if stockout_date
            else None
        )

        return AdherenceProfile(
            patient_id=patient_id,
            drug_id=drug_id,
            drug_name=drug_name,
            avg_daily_consumption=round(avg_daily, 3),
            adherence_rate=round(adherence, 3),
            last_dispense_date=last_dispense.dispense_date,
            last_dispense_qty=last_dispense.quantity_dispensed,
            days_supply_remaining=round(days_remaining or 0, 1),
            predicted_stockout_date=stockout_date,
            days_until_stockout=days_until,
            at_risk=at_risk,
            refill_due_date=refill_due,
        )

    # ------------------------------------------------------------------
    # Statistical helpers
    # ------------------------------------------------------------------

    def _calc_consumption(self, dispenses: list) -> tuple[float, float]:
        """
        Average daily consumption  +  adherence rate.

        Adherence = actual_days_covered / total_days_in_window
        where days_covered is derived from each fill's expected duration.
        """
        total_qty = sum(d.quantity_dispensed for d in dispenses)
        first_date: date = dispenses[0].dispense_date
        last_date: date = dispenses[-1].dispense_date
        span_days = max((last_date - first_date).days, 1)

        avg_daily = total_qty / span_days

        # expected duration of each fill (qty / avg_daily) → covered days
        covered_days = 0.0
        for d in dispenses:
            if avg_daily > 0:
                covered_days += d.quantity_dispensed / avg_daily

        adherence = min(covered_days / span_days, 1.0) if span_days > 0 else 0.0
        return avg_daily, adherence

    def _calc_days_remaining(
        self, last_dispense, avg_daily: float
    ) -> Optional[float]:
        """
        Days of supply left = qty_last_fill / avg_daily  –  days_since_fill.
        Returns None when data is insufficient.
        """
        if avg_daily <= 0:
            return None
        days_since_fill = (date.today() - last_dispense.dispense_date).days
        supply_at_fill = last_dispense.quantity_dispensed / avg_daily
        remaining = supply_at_fill - days_since_fill
        return max(remaining, 0)

    def _insufficient_history_profile(
        self, patient_id, drug_id, drug_name, last_dispense
    ) -> AdherenceProfile:
        return AdherenceProfile(
            patient_id=patient_id,
            drug_id=drug_id,
            drug_name=drug_name,
            avg_daily_consumption=0.0,
            adherence_rate=0.0,
            last_dispense_date=last_dispense.dispense_date,
            last_dispense_qty=last_dispense.quantity_dispensed,
            days_supply_remaining=0.0,
            predicted_stockout_date=None,
            days_until_stockout=None,
            at_risk=False,
            refill_due_date=None,
        )
