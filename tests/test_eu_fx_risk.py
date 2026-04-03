"""Tests for EU/FX Risk Calibrator, Margin Guard, and Throttler."""
import zoneinfo
from datetime import datetime, timedelta



class TestEUFXRiskCalibrator:
    def test_import(self):
        from core.risk.eu_fx_risk_calibrator import EUFXRiskCalibrator
        cal = EUFXRiskCalibrator()
        assert cal is not None

    def test_fx_ere_with_leverage(self):
        from core.risk.eu_fx_risk_calibrator import EUFXRiskCalibrator
        cal = EUFXRiskCalibrator()
        ere = cal.calibrate_ere_for_fx(40_000, 2_000, "EURUSD")
        assert ere > 0
        assert ere >= 2_000

    def test_eu_equity_ere(self):
        from core.risk.eu_fx_risk_calibrator import EUFXRiskCalibrator
        cal = EUFXRiskCalibrator()
        ere = cal.calibrate_ere_for_eu_equity(5_000, "MC.PA")
        assert ere > 0

    def test_cross_market_exposure(self):
        from core.risk.eu_fx_risk_calibrator import EUFXRiskCalibrator
        cal = EUFXRiskCalibrator()
        positions = [
            {"ticker": "DAX", "market": "eu", "direction": "LONG", "value": 5000},
            {"ticker": "EURUSD", "market": "fx", "direction": "LONG", "value": 40000},
        ]
        result = cal.check_cross_market_exposure(positions)
        # Result is a dataclass with to_dict(), not a plain dict
        assert hasattr(result, "correlated_exposure")
        assert hasattr(result, "alerts")


class TestMarginGuard:
    def test_import(self):
        from core.risk.margin_guard import MarginGuard
        mg = MarginGuard()
        assert mg is not None

    def test_margin_check_pass(self):
        from core.risk.margin_guard import MarginGuard
        mg = MarginGuard()
        order = {"symbol": "EURUSD", "notional": 25_000, "asset_class": "fx"}
        broker_state = {
            "current_margin": 2_000,
            "available_margin": 8_000,
            "equity": 10_000,
        }
        result = mg.check_margin_available(broker_state, order)
        assert result["ok"]

    def test_margin_check_fail(self):
        from core.risk.margin_guard import MarginGuard
        mg = MarginGuard()
        # Huge notional relative to tiny equity should exceed block threshold
        order = {"symbol": "EURUSD", "notional": 500_000, "asset_class": "fx"}
        broker_state = {
            "current_margin": 8_000,
            "available_margin": 500,
            "equity": 10_000,
        }
        result = mg.check_margin_available(broker_state, order)
        # With 500K notional, margin ~16.5K > 10K equity → should flag
        assert result["utilization_after"] > 0.85 or not result["ok"]

    def test_futures_margin_check(self):
        from core.risk.margin_guard import MarginGuard
        mg = MarginGuard()
        result = mg.check_futures_margin("MES", 1)
        assert "margin_required" in result
        assert result["margin_required"] > 0


class TestV10ThrottlerEU:
    def test_normal_trading_allowed(self):
        from core.risk.v10_throttler_eu import V10ThrottlerEU
        th = V10ThrottlerEU()
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        now = datetime(2026, 3, 31, 10, 0, tzinfo=paris)
        ok, reason, mult = th.should_trade("eu_gap_open", now)
        assert ok
        assert mult == 1.0

    def test_consecutive_losses_pause(self):
        from core.risk.v10_throttler_eu import V10ThrottlerEU
        th = V10ThrottlerEU(max_consecutive_losses=3)
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        now = datetime(2026, 3, 31, 10, 0, tzinfo=paris)
        for _ in range(3):
            th.record_trade_result("eu_gap_open", -100, now)
        ok, reason, mult = th.should_trade("eu_gap_open", now)
        assert not ok
        assert "PAUSED" in reason

    def test_daily_limit(self):
        from core.risk.v10_throttler_eu import V10ThrottlerEU
        th = V10ThrottlerEU(max_trades_per_day=3)
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        now = datetime(2026, 3, 31, 10, 0, tzinfo=paris)
        for _ in range(3):
            th.record_trade_result("eu_gap_open", 50, now)
        ok, reason, mult = th.should_trade("eu_gap_open", now)
        assert not ok
        assert "DAILY_LIMIT" in reason

    def test_bce_day_blocks_non_bce(self):
        from core.risk.v10_throttler_eu import BCE_MEETING_DATES_2026, V10ThrottlerEU
        th = V10ThrottlerEU()
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        bce_date = BCE_MEETING_DATES_2026[0]
        now = datetime(bce_date.year, bce_date.month, bce_date.day, 10, 0, tzinfo=paris)
        ok, reason, _ = th.should_trade("eu_gap_open", now)
        assert not ok
        assert "BCE_MEETING" in reason

    def test_bce_day_allows_bce_strategy(self):
        from core.risk.v10_throttler_eu import BCE_MEETING_DATES_2026, V10ThrottlerEU
        th = V10ThrottlerEU()
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        bce_date = BCE_MEETING_DATES_2026[0]
        now = datetime(bce_date.year, bce_date.month, bce_date.day, 10, 0, tzinfo=paris)
        ok, _, _ = th.should_trade("eu_bce_press_conference", now)
        assert ok

    def test_post_holiday_reduced_size(self):
        from core.risk.v10_throttler_eu import EU_HOLIDAYS_2026, V10ThrottlerEU
        th = V10ThrottlerEU()
        paris = zoneinfo.ZoneInfo("Europe/Paris")
        holiday = EU_HOLIDAYS_2026[0]
        next_day = holiday + timedelta(days=1)
        now = datetime(next_day.year, next_day.month, next_day.day, 10, 0, tzinfo=paris)
        ok, reason, mult = th.should_trade("eu_gap_open", now)
        assert ok
        assert mult == 0.5
        assert "POST_HOLIDAY" in reason
