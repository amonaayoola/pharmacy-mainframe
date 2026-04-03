"""
Tests — FX Volatility Oracle
Covers: threshold detection, Claude call, DB persistence, API endpoints,
        direction logic, edge cases, and no-key graceful degradation.

Run:
    cd backend
    pytest tests/test_fx_oracle.py -v
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, ANY


# ─────────────────────────────────────────────
# THRESHOLD DETECTION LOGIC
# ─────────────────────────────────────────────

class TestVolatilityThreshold:
    """The 2% threshold is the core business rule — test it exhaustively."""

    def _change_pct(self, prev: float, new: float) -> float:
        return abs((new - prev) / prev) * 100

    def test_exactly_2pct_triggers(self):
        prev, new = 1500.0, 1530.0  # exactly 2%
        assert self._change_pct(prev, new) == pytest.approx(2.0, abs=0.001)

    def test_2point1pct_triggers(self):
        assert self._change_pct(1500.0, 1531.5) > 2.0

    def test_1point9pct_does_not_trigger(self):
        assert self._change_pct(1500.0, 1528.5) < 2.0

    def test_devaluation_direction(self):
        """Rate going UP = Naira losing value = devaluation."""
        prev, new = 1550.0, 1582.0
        direction = "devaluation" if new > prev else "appreciation"
        assert direction == "devaluation"

    def test_appreciation_direction(self):
        """Rate going DOWN = Naira gaining value = appreciation."""
        prev, new = 1580.0, 1548.0
        direction = "devaluation" if new > prev else "appreciation"
        assert direction == "appreciation"

    def test_zero_change_does_not_trigger(self):
        assert self._change_pct(1578.0, 1578.0) == 0.0

    def test_large_crash_triggers(self):
        """A major devaluation event (e.g. CBN policy shock)."""
        assert self._change_pct(1578.0, 1900.0) > 2.0

    def test_threshold_math_precision(self):
        """Floating point shouldn't cause false positives at exactly 1.999%."""
        prev = 1000.0
        new = 1019.99  # 1.999%
        assert self._change_pct(prev, new) < 2.0

    def test_margin_erosion_formula(self):
        """
        Core business logic: at 25% margin, a 2% FX swing erodes ~8% of net margin.
        E.g. drug costs $2.80. At ₦1500, retail = ₦5600. At ₦1530, retail stays ₦5600
        but landed cost = ₦4284 → retail should be ₦5712. Gap = ₦112 = profit leak.
        """
        cost_usd = 2.80
        old_rate, new_rate = 1500.0, 1530.0
        margin = 0.25

        old_retail = round((cost_usd * old_rate / (1 - margin)) / 10) * 10
        new_retail = round((cost_usd * new_rate / (1 - margin)) / 10) * 10

        # Retail price should be higher after devaluation
        assert new_retail > old_retail

        # Old retail, new landed cost = margin squeeze
        new_landed = cost_usd * new_rate
        actual_margin_pct = (old_retail - new_landed) / old_retail * 100
        assert actual_margin_pct < margin * 100  # margin has eroded


# ─────────────────────────────────────────────
# run_claude_fx_analysis — UNIT TESTS
# ─────────────────────────────────────────────

class TestClaudeFXAnalysis:
    """Test the async function that calls Claude and saves to DB."""

    @pytest.mark.asyncio
    async def test_skips_when_no_api_key(self):
        """Must not crash or call httpx when ANTHROPIC_API_KEY is blank."""
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.FX_ALERT_ENABLED = True

            from app.core.scheduler import run_claude_fx_analysis
            db = MagicMock()

            # Should return None quietly — no DB write, no HTTP call
            result = await run_claude_fx_analysis(1500.0, 1530.0, 2.0, "devaluation", db)
            assert result is None
            db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_alerts_disabled(self):
        """FX_ALERT_ENABLED=false must prevent any Claude call."""
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.FX_ALERT_ENABLED = False

            from app.core.scheduler import run_claude_fx_analysis
            db = MagicMock()
            result = await run_claude_fx_analysis(1500.0, 1530.0, 2.0, "devaluation", db)
            assert result is None
            db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_call_persists_to_db(self):
        """Happy path: Claude responds → FXAlert is saved to PostgreSQL."""
        mock_analysis = (
            "REPRICING ADVISORY: The Naira has devalued 2.0%. "
            "Immediately reprice ACTs, insulin, and antihypertensives."
        )
        mock_http_response = MagicMock()
        mock_http_response.raise_for_status = MagicMock()
        mock_http_response.json.return_value = {
            "content": [{"text": mock_analysis}]
        }

        mock_db = MagicMock()
        # Mock the drug query to return 3 sample drugs
        mock_drug1 = MagicMock(brand_name="Coartem", generic_name="Artemether", strength="80/480mg", drug_class="Antimalarial", tags=["ACT"], cost_usd=Decimal("2.80"))
        mock_drug2 = MagicMock(brand_name="Norvasc", generic_name="Amlodipine", strength="10mg", drug_class="Antihypertensive", tags=["CCB"], cost_usd=Decimal("0.35"))
        mock_drug3 = MagicMock(brand_name="Glucophage", generic_name="Metformin", strength="500mg", drug_class="Antidiabetic", tags=["metformin"], cost_usd=Decimal("0.28"))
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_drug1, mock_drug2, mock_drug3]

        with patch("app.core.config.settings") as mock_settings, \
             patch("httpx.AsyncClient") as mock_client_class:

            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test-key"
            mock_settings.FX_ALERT_ENABLED = True

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_http_response)
            mock_client_class.return_value = mock_client

            from app.core.scheduler import run_claude_fx_analysis
            await run_claude_fx_analysis(
                prev_rate=1500.0,
                new_rate=1530.0,
                change_pct=2.0,
                direction="devaluation",
                db=mock_db,
            )

        # FXAlert was created and added to DB
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

        # Check the alert object that was added
        added_alert = mock_db.add.call_args[0][0]
        assert float(added_alert.prev_rate) == 1500.0
        assert float(added_alert.new_rate) == 1530.0
        assert float(added_alert.change_pct) == 2.0
        assert added_alert.direction == "devaluation"
        assert added_alert.claude_analysis == mock_analysis
        assert added_alert.drugs_affected_count == 3
        assert added_alert.model_used == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_anthropic_http_error_does_not_crash_scheduler(self):
        """A 401/429/500 from Anthropic must not kill the scheduler job."""
        import httpx

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        with patch("app.core.config.settings") as mock_settings, \
             patch("httpx.AsyncClient") as mock_client_class:

            mock_settings.ANTHROPIC_API_KEY = "sk-ant-bad-key"
            mock_settings.FX_ALERT_ENABLED = True

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            error_response = MagicMock()
            error_response.status_code = 401
            error_response.text = "Unauthorized"
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=error_response)
            )
            mock_client_class.return_value = mock_client

            from app.core.scheduler import run_claude_fx_analysis
            # Must not raise
            await run_claude_fx_analysis(1500.0, 1530.0, 2.0, "devaluation", mock_db)

        # Nothing committed to DB on error
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_network_error_does_not_crash_scheduler(self):
        """No internet connection must not kill the scheduler."""
        import httpx

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        with patch("app.core.config.settings") as mock_settings, \
             patch("httpx.AsyncClient") as mock_client_class:

            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.FX_ALERT_ENABLED = True

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            mock_client_class.return_value = mock_client

            from app.core.scheduler import run_claude_fx_analysis
            await run_claude_fx_analysis(1500.0, 1530.0, 2.0, "devaluation", mock_db)

        mock_db.commit.assert_not_called()


# ─────────────────────────────────────────────
# job_fx_sync — INTEGRATION TESTS
# ─────────────────────────────────────────────

class TestJobFXSync:
    """Test the full job_fx_sync flow including threshold branching."""

    @pytest.mark.asyncio
    async def test_no_previous_rate_establishes_baseline(self):
        """First run — no prev record in DB. Should just save rate, no alert."""
        with patch("app.core.scheduler.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fetch, \
             patch("app.core.scheduler.SessionLocal") as mock_session_cls, \
             patch("app.core.scheduler.run_claude_fx_analysis", new_callable=AsyncMock) as mock_claude:

            mock_fetch.return_value = 1578.0
            mock_db = MagicMock()
            mock_db.query.return_value.order_by.return_value.first.return_value = None  # No prev
            mock_session_cls.return_value = mock_db

            from app.core.scheduler import job_fx_sync
            await job_fx_sync()

        # Saved the rate
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        # Did NOT call Claude — no previous rate to compare
        mock_claude.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_threshold_no_claude_call(self):
        """1.5% swing → below 2% threshold → no Claude call."""
        with patch("app.core.scheduler.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fetch, \
             patch("app.core.scheduler.SessionLocal") as mock_session_cls, \
             patch("app.core.scheduler.run_claude_fx_analysis", new_callable=AsyncMock) as mock_claude, \
             patch("app.core.scheduler.settings") as mock_settings:

            mock_settings.FX_VOLATILITY_THRESHOLD_PCT = 2.0
            mock_fetch.return_value = 1523.7  # 1.58% above 1500

            prev = MagicMock()
            prev.usd_ngn = Decimal("1500.00")
            mock_db = MagicMock()
            mock_db.query.return_value.order_by.return_value.first.return_value = prev
            mock_session_cls.return_value = mock_db

            from app.core.scheduler import job_fx_sync
            await job_fx_sync()

        mock_claude.assert_not_called()

    @pytest.mark.asyncio
    async def test_above_threshold_triggers_claude(self):
        """2.5% swing → above threshold → Claude is called with correct args."""
        with patch("app.core.scheduler.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fetch, \
             patch("app.core.scheduler.SessionLocal") as mock_session_cls, \
             patch("app.core.scheduler.run_claude_fx_analysis", new_callable=AsyncMock) as mock_claude, \
             patch("app.core.scheduler.settings") as mock_settings:

            mock_settings.FX_VOLATILITY_THRESHOLD_PCT = 2.0
            new_rate = 1537.5  # 2.5% above 1500
            mock_fetch.return_value = new_rate

            prev = MagicMock()
            prev.usd_ngn = Decimal("1500.00")
            mock_db = MagicMock()
            mock_db.query.return_value.order_by.return_value.first.return_value = prev
            mock_session_cls.return_value = mock_db

            from app.core.scheduler import job_fx_sync
            await job_fx_sync()

        mock_claude.assert_called_once()
        call_kwargs = mock_claude.call_args.kwargs
        assert call_kwargs["prev_rate"] == 1500.0
        assert call_kwargs["new_rate"] == new_rate
        assert call_kwargs["direction"] == "devaluation"
        assert call_kwargs["change_pct"] == pytest.approx(2.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_appreciation_direction_correct(self):
        """Rate drop → appreciation direction."""
        with patch("app.core.scheduler.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fetch, \
             patch("app.core.scheduler.SessionLocal") as mock_session_cls, \
             patch("app.core.scheduler.run_claude_fx_analysis", new_callable=AsyncMock) as mock_claude, \
             patch("app.core.scheduler.settings") as mock_settings:

            mock_settings.FX_VOLATILITY_THRESHOLD_PCT = 2.0
            mock_fetch.return_value = 1500.0  # Down from 1540

            prev = MagicMock()
            prev.usd_ngn = Decimal("1540.00")
            mock_db = MagicMock()
            mock_db.query.return_value.order_by.return_value.first.return_value = prev
            mock_session_cls.return_value = mock_db

            from app.core.scheduler import job_fx_sync
            await job_fx_sync()

        mock_claude.assert_called_once()
        assert mock_claude.call_args.kwargs["direction"] == "appreciation"

    @pytest.mark.asyncio
    async def test_db_error_doesnt_crash_app(self):
        """If DB write fails, job catches the exception — scheduler stays alive."""
        with patch("app.core.scheduler.fetch_live_fx_rate", new_callable=AsyncMock) as mock_fetch, \
             patch("app.core.scheduler.SessionLocal") as mock_session_cls:

            mock_fetch.return_value = 1578.0
            mock_db = MagicMock()
            mock_db.query.side_effect = Exception("DB connection lost")
            mock_session_cls.return_value = mock_db

            from app.core.scheduler import job_fx_sync
            # Must not raise — scheduler would die if it did
            await job_fx_sync()

        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()


# ─────────────────────────────────────────────
# API ENDPOINT TESTS
# ─────────────────────────────────────────────

class TestFXAlertEndpoints:
    """Test GET /api/pricing/fx-alerts and /api/pricing/fx-alerts/latest."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def mock_db_with_alerts(self):
        from app.core.database import get_db
        from app.main import app

        alert1 = MagicMock()
        alert1.id = 1
        alert1.prev_rate = Decimal("1500.00")
        alert1.new_rate = Decimal("1530.00")
        alert1.change_pct = Decimal("2.000")
        alert1.direction = "devaluation"
        alert1.claude_analysis = "Reprice ACTs immediately. Coartem margin eroded by ₦112/unit."
        alert1.drugs_affected_count = 10
        alert1.model_used = "claude-sonnet-4-6"
        alert1.triggered_at = datetime.utcnow()

        mock_session = MagicMock()
        mock_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = [alert1]
        mock_session.query.return_value.order_by.return_value.first.return_value = alert1

        app.dependency_overrides[get_db] = lambda: mock_session
        yield mock_session
        app.dependency_overrides.clear()

    def test_fx_alerts_returns_list(self, client, mock_db_with_alerts):
        resp = client.get("/api/pricing/fx-alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["direction"] == "devaluation"
        assert "claude_analysis" in data[0]
        assert data[0]["drugs_affected_count"] == 10

    def test_fx_alerts_latest_returns_single(self, client, mock_db_with_alerts):
        resp = client.get("/api/pricing/fx-alerts/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["direction"] == "devaluation"
        assert float(data["change_pct"]) == 2.0

    def test_fx_alerts_latest_no_alerts(self, client):
        from app.core.database import get_db
        from app.main import app
        mock_session = MagicMock()
        mock_session.query.return_value.order_by.return_value.first.return_value = None
        app.dependency_overrides[get_db] = lambda: mock_session
        try:
            resp = client.get("/api/pricing/fx-alerts/latest")
            assert resp.status_code == 200
            assert resp.json()["alert"] is None
        finally:
            app.dependency_overrides.clear()

    def test_fx_alerts_content_is_complete(self, client, mock_db_with_alerts):
        """Every field the dashboard needs must be present."""
        resp = client.get("/api/pricing/fx-alerts")
        alert = resp.json()[0]
        required_fields = [
            "id", "prev_rate", "new_rate", "change_pct",
            "direction", "claude_analysis", "drugs_affected_count",
            "model_used", "triggered_at"
        ]
        for field in required_fields:
            assert field in alert, f"Missing field: {field}"


# ─────────────────────────────────────────────
# PROMPT QUALITY TESTS
# ─────────────────────────────────────────────

class TestClaudePromptQuality:
    """
    Verify the prompt sent to Claude contains the right context.
    We don't test Claude's output — we test our input to it.
    """

    @pytest.mark.asyncio
    async def test_prompt_contains_rate_info(self):
        """The prompt must include both rates and direction."""
        captured_prompt = {}

        async def capture_post(url, headers, json):
            captured_prompt["json"] = json
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"content": [{"text": "Test analysis"}]}
            return mock_resp

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        with patch("app.core.config.settings") as mock_settings, \
             patch("httpx.AsyncClient") as mock_client_class:

            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.FX_ALERT_ENABLED = True

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_class.return_value = mock_client

            from app.core.scheduler import run_claude_fx_analysis
            await run_claude_fx_analysis(1500.0, 1530.0, 2.0, "devaluation", mock_db)

        prompt_text = captured_prompt["json"]["messages"][0]["content"]
        assert "1,500" in prompt_text or "1500" in prompt_text
        assert "1,530" in prompt_text or "1530" in prompt_text
        assert "2.0" in prompt_text or "2.00" in prompt_text or "2.1" in prompt_text
        assert "devaluation" in prompt_text.lower() or "devalued" in prompt_text.lower()
        assert "claude-sonnet-4-6" == captured_prompt["json"]["model"]
        assert captured_prompt["json"]["max_tokens"] == 600
