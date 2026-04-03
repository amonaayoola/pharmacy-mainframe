"""
Test Suite — Pharmacy Intelligence Mainframe
Tests: Clinical Engine, FX Pricing, NAFDAC, Dispensing API, Scheduler Jobs

Run:
    cd backend
    pytest tests/ -v
    pytest tests/ -v --tb=short   # Shorter tracebacks
    pytest tests/test_clinical.py -v  # Single module
"""

import pytest
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ─────────────────────────────────────────────
# CLINICAL ENGINE TESTS
# ─────────────────────────────────────────────

class TestClinicalGateway:
    """Tests for the drug interaction engine."""

    def setup_method(self):
        from app.services.clinical_service import ClinicalGateway, AuditResult
        self.gw = ClinicalGateway()
        self.AuditResult = AuditResult

    def test_clear_basket_single_drug(self):
        basket = [{"drug_id": 1, "name": "Paracetamol 500mg", "tags": ["analgesic", "OTC"]}]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.CLEAR
        assert report.safe_to_dispense is True
        assert len(report.alerts) == 0

    def test_block_act_plus_high_dose_vit_c(self):
        """Core Nigerian pharmacy scenario: ACT + Vitamin C 1000mg = BLOCK."""
        basket = [
            {"drug_id": 1, "name": "Coartem 80/480mg", "tags": ["ACT", "antimalarial"]},
            {"drug_id": 2, "name": "Vitamin C 1000mg", "tags": ["supplement", "VIT_C_HIGH"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.BLOCK
        assert report.safe_to_dispense is False
        assert len(report.alerts) == 1
        assert "ACT" in report.alerts[0].message or "artemisinin" in report.alerts[0].message.lower()

    def test_block_ssri_plus_maoi(self):
        """Serotonin syndrome risk — should always BLOCK."""
        basket = [
            {"drug_id": 3, "name": "Fluoxetine 20mg", "tags": ["SSRI", "antidepressant"]},
            {"drug_id": 4, "name": "Phenelzine 15mg", "tags": ["MAOI", "antidepressant"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.BLOCK
        assert report.safe_to_dispense is False

    def test_warn_ace_inhibitor_plus_k_sparing_diuretic(self):
        """Hyperkalaemia risk — WARN but allow dispense."""
        basket = [
            {"drug_id": 5, "name": "Lisinopril 10mg", "tags": ["ACE_inhibitor", "antihypertensive"]},
            {"drug_id": 6, "name": "Spironolactone 25mg", "tags": ["potassium_sparing_diuretic", "diuretic"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.WARN
        assert report.safe_to_dispense is True  # WARN does not block
        assert len(report.alerts) == 1

    def test_block_anticoagulant_plus_nsaid(self):
        basket = [
            {"drug_id": 7, "name": "Warfarin 5mg", "tags": ["anticoagulant"]},
            {"drug_id": 8, "name": "Ibuprofen 400mg", "tags": ["NSAID", "analgesic"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.BLOCK
        assert report.safe_to_dispense is False

    def test_empty_basket_is_clear(self):
        report = self.gw.audit_basket([])
        assert report.result == self.AuditResult.CLEAR
        assert report.safe_to_dispense is True

    def test_multiple_safe_drugs_are_clear(self):
        basket = [
            {"drug_id": 1, "name": "Paracetamol 500mg", "tags": ["analgesic"]},
            {"drug_id": 2, "name": "Amoxicillin 500mg", "tags": ["antibiotic", "penicillin"]},
            {"drug_id": 3, "name": "ORS", "tags": ["electrolyte", "ORS"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.CLEAR

    def test_act_without_vit_c_is_clear(self):
        basket = [
            {"drug_id": 1, "name": "Coartem 80/480mg", "tags": ["ACT", "antimalarial"]},
            {"drug_id": 2, "name": "Paracetamol 500mg", "tags": ["analgesic"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.CLEAR

    def test_vit_c_without_act_is_clear(self):
        basket = [
            {"drug_id": 2, "name": "Vitamin C 1000mg", "tags": ["supplement", "VIT_C_HIGH"]},
            {"drug_id": 3, "name": "Paracetamol 500mg", "tags": ["analgesic"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.CLEAR

    def test_multiple_blocks_all_reported(self):
        """All interactions must be reported, not just the first."""
        basket = [
            {"drug_id": 1, "name": "Warfarin", "tags": ["anticoagulant"]},
            {"drug_id": 2, "name": "Ibuprofen", "tags": ["NSAID"]},
            {"drug_id": 3, "name": "Fluoxetine", "tags": ["SSRI"]},
            {"drug_id": 4, "name": "Phenelzine", "tags": ["MAOI"]},
        ]
        report = self.gw.audit_basket(basket)
        assert report.result == self.AuditResult.BLOCK
        assert len(report.alerts) >= 2

    def test_audit_notes_populated_on_block(self):
        basket = [
            {"drug_id": 1, "name": "Coartem", "tags": ["ACT"]},
            {"drug_id": 2, "name": "Vitamin C 1000mg", "tags": ["VIT_C_HIGH"]},
        ]
        report = self.gw.audit_basket(basket)
        assert len(report.audit_notes) > 0
        assert "BLOCK" in report.audit_notes


# ─────────────────────────────────────────────
# FX PRICING ENGINE TESTS
# ─────────────────────────────────────────────

class TestPricingEngine:
    """Tests for the dynamic pricing and margin protection engine."""

    def setup_method(self):
        from app.services.fx_service import PricingEngine
        self.engine = PricingEngine(fx_rate=1578.0, margin=0.25)

    def test_landed_cost_calculation(self):
        """$2.80 USD × 1578 NGN/USD = ₦4,418.40"""
        landed = self.engine.landed_cost_ngn(2.80)
        assert abs(landed - 4418.40) < 0.01

    def test_retail_price_rounds_to_nearest_10(self):
        """Retail price should always be rounded to nearest ₦10."""
        retail = self.engine.retail_price_ngn(2.80, margin=0.25)
        assert retail % 10 == 0

    def test_retail_price_at_25_percent_margin(self):
        """$2.80 cost → ₦4,418.40 landed → ₦5,890 retail at 25% margin."""
        retail = self.engine.retail_price_ngn(2.80, margin=0.25)
        landed = self.engine.landed_cost_ngn(2.80)
        # Verify margin: (retail - landed) / retail ≈ 25%
        actual_margin = (retail - landed) / retail
        assert abs(actual_margin - 0.25) < 0.01  # Within 1% of target

    def test_margin_amount_is_positive(self):
        margin_ngn = self.engine.margin_amount_ngn(0.12)
        assert margin_ngn > 0

    def test_zero_cost_drug_returns_zero_price(self):
        landed = self.engine.landed_cost_ngn(0.0)
        assert landed == 0.0

    def test_higher_margin_gives_higher_retail(self):
        price_25 = self.engine.retail_price_ngn(1.00, margin=0.25)
        price_40 = self.engine.retail_price_ngn(1.00, margin=0.40)
        assert price_40 > price_25

    def test_higher_fx_rate_gives_higher_retail(self):
        from app.services.fx_service import PricingEngine
        engine_low = PricingEngine(fx_rate=1400.0)
        engine_high = PricingEngine(fx_rate=1800.0)
        assert engine_high.retail_price_ngn(1.00) > engine_low.retail_price_ngn(1.00)

    def test_price_all_drugs_returns_correct_fields(self):
        mock_drugs = [
            MagicMock(id=1, generic_name="Paracetamol", brand_name="Emzor Para", cost_usd=Decimal("0.12")),
            MagicMock(id=2, generic_name="Artemether", brand_name="Coartem", cost_usd=Decimal("2.80")),
        ]
        results = self.engine.price_all_drugs(mock_drugs, margin=0.25)
        assert len(results) == 2
        for r in results:
            assert "retail_ngn" in r
            assert "landed_ngn" in r
            assert "margin_ngn" in r
            assert r["retail_ngn"] % 10 == 0
            assert r["margin_ngn"] > 0

    def test_back_calculate_margin(self):
        retail = self.engine.retail_price_ngn(1.00, margin=0.30)
        back_calc = self.engine.margin_percentage(1.00, retail)
        assert abs(back_calc - 30.0) < 2.0  # Within 2% (due to rounding)


# ─────────────────────────────────────────────
# NAFDAC SERVICE TESTS
# ─────────────────────────────────────────────

class TestNAFDACService:
    """Tests for batch authentication."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.services.nafdac_service import NAFDACService
        self.service = NAFDACService()

    @pytest.mark.asyncio
    async def test_verify_known_authentic_batch(self):
        result = await self.service.verify_batch("GS-2024-1192")
        assert result["status"] == "verified"
        assert result["safe_to_dispense"] is True
        assert "Paracetamol" in result["drug_name"]

    @pytest.mark.asyncio
    async def test_verify_flagged_batch(self):
        result = await self.service.verify_batch("PT-2024-9921")
        assert result["status"] == "flagged"
        assert result["safe_to_dispense"] is False

    @pytest.mark.asyncio
    async def test_verify_counterfeit_batch(self):
        result = await self.service.verify_batch("COUNTERFEIT-0001")
        assert result["status"] == "counterfeit"
        assert result["safe_to_dispense"] is False
        assert "COUNTERFEIT" in result["message"].upper() or "DO NOT" in result["message"]

    @pytest.mark.asyncio
    async def test_verify_unknown_batch_is_not_safe(self):
        result = await self.service.verify_batch("TOTALLY-FAKE-9999")
        assert result["safe_to_dispense"] is False
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_batch_number_case_insensitive(self):
        upper = await self.service.verify_batch("GS-2024-1192")
        lower = await self.service.verify_batch("gs-2024-1192")
        assert upper["status"] == lower["status"]

    def test_registry_summary_counts(self):
        summary = self.service.get_local_registry_summary()
        assert summary["total_registered"] > 0
        assert summary["verified"] > 0
        assert summary["total_registered"] == (
            summary["verified"] + summary["flagged"] + summary["counterfeit"]
        )


# ─────────────────────────────────────────────
# WHATSAPP SERVICE TESTS
# ─────────────────────────────────────────────

class TestWhatsAppService:
    """Tests for message formatting and inbound intent parsing."""

    def setup_method(self):
        from app.services.whatsapp_service import WhatsAppService
        self.service = WhatsAppService()

    def test_parse_yes_response(self):
        for text in ["YES", "yes", "Y", "y", "1", "OK", "CONFIRM"]:
            assert self.service.parse_inbound_response(text) == "confirm_pickup"

    def test_parse_deliver_response(self):
        for text in ["DELIVER", "deliver", "Delivery", "D", "2"]:
            assert self.service.parse_inbound_response(text) == "confirm_delivery"

    def test_parse_later_response(self):
        for text in ["LATER", "TOMORROW", "3", "REMIND", "NO"]:
            assert self.service.parse_inbound_response(text) == "postpone"

    def test_parse_cancel_response(self):
        for text in ["CANCEL", "STOP", "QUIT"]:
            assert self.service.parse_inbound_response(text) == "cancel"

    def test_parse_unknown_response(self):
        assert self.service.parse_inbound_response("what is the capital of France") == "unknown"
        assert self.service.parse_inbound_response("") == "unknown"

    @pytest.mark.asyncio
    async def test_send_in_dev_mode(self):
        """When no API keys configured, dev mode logs and returns success."""
        result = await self.service.send_message("+2348021112222", "Test message")
        assert result["status"] == "dev_mode"
        assert "phone" in result

    def test_refill_message_contains_patient_name(self):
        from app.services.whatsapp_service import refill_reminder_message
        msg = refill_reminder_message("Mrs. Adebayo Funmilayo", "Amlodipine 10mg",
                                       2, 3200.0, "Lagos Branch")
        assert "Adebayo" in msg or "Mrs." in msg
        assert "Amlodipine" in msg
        assert "3,200" in msg or "3200" in msg
        assert "2 day" in msg

    def test_delivery_confirmation_contains_price(self):
        from app.services.whatsapp_service import delivery_confirmation_message
        msg = delivery_confirmation_message("Mr. Obi", "Metformin 500mg", 8400.0, "3:00 PM")
        assert "8,400" in msg or "8400" in msg
        assert "3:00" in msg

    def test_drug_interaction_warning_contains_both_drugs(self):
        from app.services.whatsapp_service import drug_interaction_warning_message
        msg = drug_interaction_warning_message("Mrs. Eze", "Warfarin", "Ibuprofen",
                                                "Bleeding risk")
        assert "Warfarin" in msg
        assert "Ibuprofen" in msg
        assert "Bleeding" in msg


# ─────────────────────────────────────────────
# DISPENSING API INTEGRATION TESTS
# ─────────────────────────────────────────────

class TestDispensingEndpoint:
    """FastAPI endpoint tests using TestClient."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def mock_db(self):
        """Override DB dependency with mocked session."""
        from app.core.database import get_db
        from app.main import app
        mock_session = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_session
        yield mock_session
        app.dependency_overrides.clear()

    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "operational"

    def test_audit_endpoint_clear_basket(self, client, mock_db):
        """Mock a clean basket and verify audit returns CLEAR."""
        mock_drug = MagicMock()
        mock_drug.id = 1
        mock_drug.brand_name = "Paracetamol"
        mock_drug.strength = "500mg"
        mock_drug.tags = ["analgesic"]
        mock_db.query.return_value.filter.return_value.first.return_value = mock_drug

        resp = client.post("/api/dispense/audit", json={
            "items": [{"drug_id": 1, "quantity": 2}],
            "served_by": "Staff"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "CLEAR"
        assert data["safe_to_dispense"] is True


# ─────────────────────────────────────────────
# SCHEDULER / BACKGROUND JOB TESTS
# ─────────────────────────────────────────────

class TestSchedulerJobs:
    """Tests for background job logic (mocked external deps)."""

    @pytest.mark.asyncio
    async def test_fx_sync_records_rate(self):
        """FX sync should fetch rate and return a float."""
        with patch("app.services.fx_service.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fx:
            mock_fx.return_value = 1590.0
            from app.services.fx_service import fetch_live_fx_rate
            rate = await fetch_live_fx_rate()
            assert isinstance(rate, float)
            assert rate > 0

    @pytest.mark.asyncio
    async def test_whatsapp_refill_job_dev_mode(self):
        """Refill outreach in dev mode should not raise."""
        from app.services.whatsapp_service import WhatsAppService
        svc = WhatsAppService()
        result = await svc.send_refill_reminder(
            patient_name="Test Patient",
            phone="+2348000000000",
            drug_name="Paracetamol 500mg",
            days_left=2,
            price_ngn=500.0,
        )
        assert result is not None
        assert result.get("status") == "dev_mode"


# ─────────────────────────────────────────────
# EDGE CASES & SECURITY TESTS
# ─────────────────────────────────────────────

class TestEdgeCases:
    def test_pricing_engine_with_extreme_fx_rate(self):
        """Price engine must not crash at very high or low FX rates."""
        from app.services.fx_service import PricingEngine
        for rate in [100.0, 500.0, 2000.0, 5000.0]:
            engine = PricingEngine(fx_rate=rate)
            retail = engine.retail_price_ngn(1.00)
            assert retail > 0
            assert retail % 10 == 0

    def test_clinical_audit_ignores_none_tags(self):
        """Drugs without tags should not crash the engine."""
        from app.services.clinical_service import ClinicalGateway
        gw = ClinicalGateway()
        basket = [
            {"drug_id": 1, "name": "Unknown Drug", "tags": None},
            {"drug_id": 2, "name": "Another Drug", "tags": []},
        ]
        # Should not raise
        report = gw.audit_basket(basket)
        assert report is not None

    @pytest.mark.asyncio
    async def test_nafdac_sql_injection_safe(self):
        """Batch number input should be treated as string, never executed."""
        from app.services.nafdac_service import NAFDACService
        svc = NAFDACService()
        # These should return "not found" gracefully, never raise or execute
        for evil_input in ["'; DROP TABLE drugs; --", "<script>alert(1)</script>", "../../etc/passwd"]:
            result = await svc.verify_batch(evil_input)
            assert result["safe_to_dispense"] is False
            assert result["status"] in ["pending", "not_found"]
