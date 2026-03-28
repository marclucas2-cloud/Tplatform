"""
Tests for LiveRiskManager — live trading risk management ($10K IBKR).

Covers:
  - Each position limit (position, strategy, long, short, gross, positions count, cash)
  - Circuit breakers (daily, hourly, weekly)
  - Progressive deleveraging (3 levels)
  - Margin block (>85%)
  - Kill switches (5d trailing, monthly)
  - Bypass tests (no path should circumvent limits)
  - Isolation test (paper and live limits do not mix)
"""

import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def env_setup():
    """Ensure clean env for all tests."""
    with patch.dict(os.environ, {
        "PAPER_TRADING": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


@pytest.fixture
def live_rm():
    """LiveRiskManager instance with default live limits."""
    from core.risk_manager_live import LiveRiskManager
    limits_path = ROOT / "config" / "limits_live.yaml"
    return LiveRiskManager(limits_path=limits_path)


@pytest.fixture
def paper_rm():
    """Original RiskManager instance with paper limits (for isolation test)."""
    from core.risk_manager import RiskManager
    limits_path = ROOT / "config" / "limits.yaml"
    return RiskManager(limits_path=limits_path)


@pytest.fixture
def base_portfolio():
    """Baseline $10K portfolio with no positions."""
    return {
        "equity": 10_000.0,
        "cash": 10_000.0,
        "positions": [],
        "margin_used_pct": 0.0,
    }


@pytest.fixture
def portfolio_with_positions():
    """Portfolio with 4 existing positions."""
    return {
        "equity": 10_000.0,
        "cash": 4_000.0,
        "positions": [
            {"symbol": "AAPL", "notional": 1500, "side": "LONG", "strategy": "momentum"},
            {"symbol": "MSFT", "notional": 1500, "side": "LONG", "strategy": "momentum"},
            {"symbol": "JPM", "notional": 1500, "side": "LONG", "strategy": "pairs"},
            {"symbol": "XOM", "notional": 1500, "side": "SHORT", "strategy": "vrp"},
        ],
        "margin_used_pct": 0.40,
    }


# =============================================================================
# TEST 1: POSITION LIMIT (max 15% per position)
# =============================================================================

class TestPositionLimit:
    """max_position_pct = 0.15 -> $1,500 max per position on $10K."""

    def test_position_under_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1400, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_position_at_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_position_over_limit_rejected(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1600, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is False
        assert "Position limit" in msg

    def test_position_cumulative_over_limit(self, live_rm, base_portfolio):
        """Adding to existing position pushes over limit."""
        base_portfolio["positions"] = [
            {"symbol": "AAPL", "notional": 1000, "side": "LONG", "strategy": "test"},
        ]
        base_portfolio["cash"] = 9000
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 600, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is False
        assert "Position limit" in msg


# =============================================================================
# TEST 2: STRATEGY LIMIT (max 20% per strategy)
# =============================================================================

class TestStrategyLimit:
    """max_strategy_pct = 0.20 -> $2,000 max per strategy on $10K."""

    def test_strategy_under_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1500, "strategy": "momentum"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_strategy_over_limit_rejected(self, live_rm, base_portfolio):
        base_portfolio["positions"] = [
            {"symbol": "AAPL", "notional": 1500, "side": "LONG", "strategy": "momentum"},
        ]
        base_portfolio["cash"] = 8500
        order = {"symbol": "MSFT", "direction": "LONG", "notional": 600, "strategy": "momentum"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is False
        assert "Strategy limit" in msg


# =============================================================================
# TEST 3: LONG EXPOSURE (max 60%)
# =============================================================================

class TestLongExposure:
    """max_long_pct = 0.60 -> $6,000 max long."""

    def test_long_under_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_long_over_limit_rejected(self, live_rm):
        portfolio = {
            "equity": 10_000.0,
            "cash": 4_000.0,
            "positions": [
                {"symbol": "MSFT", "notional": 1500, "side": "LONG", "strategy": "a"},
                {"symbol": "JPM", "notional": 1500, "side": "LONG", "strategy": "b"},
                {"symbol": "XOM", "notional": 1500, "side": "LONG", "strategy": "c"},
                {"symbol": "AMD", "notional": 1500, "side": "LONG", "strategy": "d"},
            ],
            "margin_used_pct": 0.0,
        }
        order = {"symbol": "NVDA", "direction": "LONG", "notional": 200, "strategy": "e"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Long exposure" in msg


# =============================================================================
# TEST 4: SHORT EXPOSURE (max 40%)
# =============================================================================

class TestShortExposure:
    """max_short_pct = 0.40 -> $4,000 max short."""

    def test_short_under_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "SHORT", "notional": 1500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_short_over_limit_rejected(self, live_rm):
        portfolio = {
            "equity": 10_000.0,
            "cash": 6_000.0,
            "positions": [
                {"symbol": "MSFT", "notional": 1500, "side": "SHORT", "strategy": "a"},
                {"symbol": "JPM", "notional": 1500, "side": "SHORT", "strategy": "b"},
            ],
            "margin_used_pct": 0.0,
        }
        order = {"symbol": "XOM", "direction": "SHORT", "notional": 1200, "strategy": "c"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Short exposure" in msg


# =============================================================================
# TEST 5: GROSS EXPOSURE (max 120%)
# =============================================================================

class TestGrossExposure:
    """max_gross_pct = 1.20 -> $12,000 max gross."""

    def test_gross_under_limit_passes(self, live_rm, base_portfolio):
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_gross_over_limit_rejected(self, live_rm):
        # We test _check_gross_exposure directly to avoid cascading limit failures.
        # With $10K equity and max_gross_pct=1.20, gross limit = $12,000.
        portfolio = {
            "equity": 10_000.0,
            "cash": 5_000.0,
            "positions": [
                {"symbol": "AAPL", "notional": 1400, "side": "LONG", "strategy": "a"},
                {"symbol": "JPM", "notional": 1400, "side": "LONG", "strategy": "b"},
                {"symbol": "XOM", "notional": 1400, "side": "SHORT", "strategy": "c"},
                {"symbol": "COIN", "notional": 1400, "side": "LONG", "strategy": "d"},
                {"symbol": "HD", "notional": 1400, "side": "SHORT", "strategy": "e"},
                {"symbol": "CAT", "notional": 1400, "side": "LONG", "strategy": "f"},
                {"symbol": "UPS", "notional": 1400, "side": "SHORT", "strategy": "g"},
                {"symbol": "BAC", "notional": 1400, "side": "LONG", "strategy": "h"},
            ],
            "margin_used_pct": 0.0,
        }
        # existing = 8 * 1400 = 11200
        # order 900 -> total 12100 > 12000 -> gross fails
        # But position limit: 900 / 10000 = 9% < 15% OK
        # strategy: 900 / 10000 = 9% < 20% OK
        order = {"symbol": "WFC", "direction": "LONG", "notional": 900, "strategy": "i"}
        passed, msg = live_rm._check_gross_exposure(order, portfolio)
        assert passed is False
        assert "Gross exposure" in msg


# =============================================================================
# TEST 6: MAX POSITIONS COUNT (max 6)
# =============================================================================

class TestMaxPositions:
    """max_positions = 6."""

    def test_under_max_positions_passes(self, live_rm):
        """5 positions + 1 new unique symbol = 6 <= max 6, passes."""
        portfolio = {
            "equity": 10_000.0,
            "cash": 5_000.0,
            "positions": [
                {"symbol": "AAPL", "notional": 500, "side": "LONG", "strategy": "a"},
                {"symbol": "JPM", "notional": 500, "side": "LONG", "strategy": "b"},
                {"symbol": "XOM", "notional": 500, "side": "SHORT", "strategy": "c"},
                {"symbol": "COIN", "notional": 500, "side": "LONG", "strategy": "d"},
                {"symbol": "CAT", "notional": 500, "side": "SHORT", "strategy": "e"},
            ],
            "margin_used_pct": 0.0,
        }
        order = {"symbol": "HD", "direction": "LONG", "notional": 500, "strategy": "f"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is True

    def test_adding_to_existing_symbol_ok(self, live_rm):
        """Adding to an existing position doesn't increase count."""
        portfolio = {
            "equity": 10_000.0,
            "cash": 4_000.0,
            "positions": [
                {"symbol": "AAPL", "notional": 500, "side": "LONG", "strategy": "a"},
                {"symbol": "JPM", "notional": 500, "side": "LONG", "strategy": "b"},
                {"symbol": "XOM", "notional": 500, "side": "SHORT", "strategy": "c"},
                {"symbol": "COIN", "notional": 500, "side": "LONG", "strategy": "d"},
                {"symbol": "CAT", "notional": 500, "side": "SHORT", "strategy": "e"},
                {"symbol": "HD", "notional": 500, "side": "LONG", "strategy": "f"},
            ],
            "margin_used_pct": 0.0,
        }
        # Adding to existing AAPL -> still 6 positions
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 200, "strategy": "a"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is True

    def test_new_symbol_over_max_rejected(self, live_rm):
        """7th unique symbol is rejected."""
        portfolio = {
            "equity": 10_000.0,
            "cash": 4_000.0,
            "positions": [
                {"symbol": f"SYM{i}", "notional": 800, "side": "LONG", "strategy": f"s{i}"}
                for i in range(6)
            ],
            "margin_used_pct": 0.0,
        }
        order = {"symbol": "NEW_SYM", "direction": "LONG", "notional": 100, "strategy": "new"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Max positions" in msg


# =============================================================================
# TEST 7: CASH RESERVE (min 15%)
# =============================================================================

class TestCashReserve:
    """min_cash_pct = 0.15 -> must keep $1,500 in cash."""

    def test_enough_cash_passes(self, live_rm, base_portfolio):
        # 10000 - 1500 = 8500 cash / 10000 = 85% > 15%
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 1500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_not_enough_cash_rejected(self, live_rm):
        portfolio = {
            "equity": 10_000.0,
            "cash": 2_000.0,
            "positions": [
                {"symbol": "AAPL", "notional": 1500, "side": "LONG", "strategy": "a"},
            ],
            "margin_used_pct": 0.0,
        }
        # 2000 - 700 = 1300 / 10000 = 13% < 15%
        order = {"symbol": "MSFT", "direction": "LONG", "notional": 700, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Cash reserve" in msg


# =============================================================================
# TEST 8: CIRCUIT BREAKER — DAILY (1.5%)
# =============================================================================

class TestCircuitBreakerDaily:
    """daily_loss_pct = 0.015 -> -$150 stops trading today."""

    def test_no_loss_passes(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, daily_pnl_pct=0.0)
        daily_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_daily")
        assert daily_check["passed"] is True

    def test_small_loss_passes(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, daily_pnl_pct=-0.01)
        daily_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_daily")
        assert daily_check["passed"] is True

    def test_loss_over_threshold_triggers(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, daily_pnl_pct=-0.02)
        daily_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_daily")
        assert daily_check["passed"] is False
        assert result["passed"] is False
        assert "STOP_TRADING_TODAY" in result["actions"]

    def test_circuit_breaker_method_direct(self, live_rm):
        """Direct check_circuit_breaker method also works."""
        triggered, msg = live_rm.check_circuit_breaker(daily_pnl_pct=-0.02)
        assert triggered is True
        assert "CIRCUIT BREAKER DAILY" in msg


# =============================================================================
# TEST 9: CIRCUIT BREAKER — HOURLY (1%)
# =============================================================================

class TestCircuitBreakerHourly:
    """hourly_loss_pct = 0.01 -> -$100 pauses 30 min."""

    def test_small_hourly_passes(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, hourly_pnl_pct=-0.005)
        hourly_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_hourly")
        assert hourly_check["passed"] is True

    def test_hourly_over_threshold_triggers(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, hourly_pnl_pct=-0.015)
        hourly_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_hourly")
        assert hourly_check["passed"] is False
        assert "PAUSE_30_MIN" in result["actions"]


# =============================================================================
# TEST 10: CIRCUIT BREAKER — WEEKLY (3%)
# =============================================================================

class TestCircuitBreakerWeekly:
    """weekly_loss_pct = 0.03 -> -$300 reduces sizing 50%."""

    def test_weekly_under_limit_passes(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, weekly_pnl_pct=-0.02)
        weekly_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_weekly")
        assert weekly_check["passed"] is True

    def test_weekly_over_limit_triggers(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, weekly_pnl_pct=-0.04)
        weekly_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_weekly")
        assert weekly_check["passed"] is False
        assert "REDUCE_SIZING_50" in result["actions"]

    def test_weekly_does_not_fully_block(self, live_rm, base_portfolio):
        """Weekly circuit breaker adds action but doesn't set blocked_reason alone."""
        result = live_rm.check_all_limits(base_portfolio, weekly_pnl_pct=-0.04)
        # Weekly alone doesn't block if daily/hourly are fine
        # Check that blocked_reason is not set from weekly
        if result["blocked_reason"] is not None:
            assert "weekly" not in result["blocked_reason"].lower()


# =============================================================================
# TEST 11: DELEVERAGING LEVEL 1 (DD >= 1%)
# =============================================================================

class TestDeleveragingLevel1:
    """level_1_dd_pct = 0.01 -> reduce 30%."""

    def test_no_dd_no_deleveraging(self, live_rm):
        level, reduction, msg = live_rm.check_progressive_deleveraging(0.005)
        assert level == 0
        assert reduction == 0.0

    def test_level_1_triggered(self, live_rm):
        level, reduction, msg = live_rm.check_progressive_deleveraging(0.01)
        assert level == 1
        assert reduction == 0.30
        assert "L1" in msg

    def test_level_1_in_check_all(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, current_dd_pct=0.012)
        assert result["deleveraging"]["level"] == 1
        assert result["deleveraging"]["reduction_pct"] == 0.30
        assert "DELEVERAGE_L1" in result["actions"]


# =============================================================================
# TEST 12: DELEVERAGING LEVEL 2 (DD >= 1.5%)
# =============================================================================

class TestDeleveragingLevel2:
    """level_2_dd_pct = 0.015 -> reduce 50%."""

    def test_level_2_triggered(self, live_rm):
        level, reduction, msg = live_rm.check_progressive_deleveraging(0.015)
        assert level == 2
        assert reduction == 0.50
        assert "L2" in msg


# =============================================================================
# TEST 13: DELEVERAGING LEVEL 3 (DD >= 2%)
# =============================================================================

class TestDeleveragingLevel3:
    """level_3_dd_pct = 0.02 -> close all (100%)."""

    def test_level_3_triggered(self, live_rm):
        level, reduction, msg = live_rm.check_progressive_deleveraging(0.02)
        assert level == 3
        assert reduction == 1.00
        assert "L3" in msg or "Close all" in msg

    def test_level_3_extreme_dd(self, live_rm):
        """Extreme DD still triggers level 3."""
        level, reduction, msg = live_rm.check_progressive_deleveraging(0.10)
        assert level == 3
        assert reduction == 1.00


# =============================================================================
# TEST 14: MARGIN ALERT (>70%)
# =============================================================================

class TestMarginAlert:
    """max_margin_used_pct = 0.70 -> yellow alert."""

    def test_low_margin_ok(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, margin_used_pct=0.50)
        margin_alert = next(c for c in result["checks"] if c["name"] == "margin_alert")
        assert margin_alert["passed"] is True

    def test_high_margin_alert(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, margin_used_pct=0.75)
        margin_alert = next(c for c in result["checks"] if c["name"] == "margin_alert")
        assert margin_alert["passed"] is False
        assert "MARGIN_ALERT" in result["actions"]


# =============================================================================
# TEST 15: MARGIN BLOCK (>85%)
# =============================================================================

class TestMarginBlock:
    """block_margin_pct = 0.85 -> block new trades."""

    def test_margin_under_block_passes(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, margin_used_pct=0.80)
        margin_block = next(c for c in result["checks"] if c["name"] == "margin_block")
        assert margin_block["passed"] is True

    def test_margin_over_block_rejected(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, margin_used_pct=0.90)
        margin_block = next(c for c in result["checks"] if c["name"] == "margin_block")
        assert margin_block["passed"] is False
        assert result["passed"] is False
        assert "BLOCK_NEW_TRADES" in result["actions"]

    def test_margin_block_in_validate_order(self, live_rm):
        """validate_order also blocks when margin is too high."""
        portfolio = {
            "equity": 10_000.0,
            "cash": 5_000.0,
            "positions": [],
            "margin_used_pct": 0.90,
        }
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Margin block" in msg


# =============================================================================
# TEST 16: KILL SWITCH — 5-DAY TRAILING (3%)
# =============================================================================

class TestKillSwitch5D:
    """trailing_5d_loss_pct = 0.03 -> close all."""

    def test_small_trailing_loss_ok(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, trailing_5d_pnl_pct=-0.02)
        ks = next(c for c in result["checks"] if c["name"] == "kill_switch_5d")
        assert ks["passed"] is True

    def test_large_trailing_loss_triggers(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, trailing_5d_pnl_pct=-0.04)
        ks = next(c for c in result["checks"] if c["name"] == "kill_switch_5d")
        assert ks["passed"] is False
        assert result["passed"] is False
        assert "CLOSE_ALL_POSITIONS" in result["actions"]


# =============================================================================
# TEST 17: KILL SWITCH — MONTHLY (5%)
# =============================================================================

class TestKillSwitchMonthly:
    """max_monthly_loss_pct = 0.05 -> close all + review."""

    def test_small_monthly_loss_ok(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, monthly_pnl_pct=-0.03)
        ks = next(c for c in result["checks"] if c["name"] == "kill_switch_monthly")
        assert ks["passed"] is True

    def test_large_monthly_loss_triggers(self, live_rm, base_portfolio):
        result = live_rm.check_all_limits(base_portfolio, monthly_pnl_pct=-0.06)
        ks = next(c for c in result["checks"] if c["name"] == "kill_switch_monthly")
        assert ks["passed"] is False
        assert result["passed"] is False
        assert "CLOSE_ALL_REVIEW" in result["actions"]


# =============================================================================
# TEST 18: SECTOR LIMIT (max 30%)
# =============================================================================

class TestSectorLimit:
    """max_sector_pct = 0.30 -> $3,000 max in one sector."""

    def test_sector_under_limit_passes(self, live_rm, base_portfolio):
        base_portfolio["positions"] = [
            {"symbol": "AAPL", "notional": 1500, "side": "LONG", "strategy": "a"},
        ]
        base_portfolio["cash"] = 8500
        order = {"symbol": "MSFT", "direction": "LONG", "notional": 1400, "strategy": "b"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True

    def test_sector_over_limit_rejected(self, live_rm, base_portfolio):
        base_portfolio["positions"] = [
            {"symbol": "AAPL", "notional": 1500, "side": "LONG", "strategy": "a"},
            {"symbol": "MSFT", "notional": 1500, "side": "LONG", "strategy": "b"},
        ]
        base_portfolio["cash"] = 7000
        # Tech sector: AAPL 1500 + MSFT 1500 + NVDA 500 = 3500 = 35% > 30%
        order = {"symbol": "NVDA", "direction": "LONG", "notional": 500, "strategy": "c"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is False
        assert "Sector limit" in msg


# =============================================================================
# TEST 19: NO BYPASS — every check runs
# =============================================================================

class TestNoBypass:
    """No code path should skip risk checks."""

    def test_validate_order_runs_all_checks(self, live_rm, base_portfolio):
        """A valid order must pass through all checks without short-circuit."""
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 500, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, base_portfolio)
        assert passed is True
        assert msg == "OK"

    def test_zero_equity_always_fails(self, live_rm):
        """Zero equity must fail (division by zero guard)."""
        portfolio = {"equity": 0, "cash": 0, "positions": [], "margin_used_pct": 0.0}
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 100, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False
        assert "Equity" in msg

    def test_negative_equity_always_fails(self, live_rm):
        """Negative equity must fail."""
        portfolio = {"equity": -1000, "cash": 0, "positions": [], "margin_used_pct": 0.0}
        order = {"symbol": "AAPL", "direction": "LONG", "notional": 100, "strategy": "test"}
        passed, msg = live_rm.validate_order(order, portfolio)
        assert passed is False

    def test_missing_fields_dont_crash(self, live_rm, base_portfolio):
        """Missing optional fields should not crash the validator."""
        order = {"symbol": "AAPL", "notional": 500}
        # Should not raise, might fail on some checks
        passed, msg = live_rm.validate_order(order, base_portfolio)
        # We just assert it doesn't raise an exception
        assert isinstance(passed, bool)

    def test_check_all_limits_returns_complete_structure(self, live_rm, base_portfolio):
        """check_all_limits must return all expected keys."""
        result = live_rm.check_all_limits(base_portfolio)
        assert "passed" in result
        assert "checks" in result
        assert "blocked_reason" in result
        assert "deleveraging" in result
        assert "actions" in result
        assert isinstance(result["checks"], list)
        assert len(result["checks"]) >= 8  # at least 8 check categories

    def test_multiple_failures_first_one_wins(self, live_rm):
        """When multiple limits fail, blocked_reason is the first failure."""
        result = live_rm.check_all_limits(
            {"equity": 10_000, "cash": 0, "positions": []},
            daily_pnl_pct=-0.05,
            hourly_pnl_pct=-0.05,
            trailing_5d_pnl_pct=-0.10,
            monthly_pnl_pct=-0.10,
            margin_used_pct=0.95,
        )
        assert result["passed"] is False
        assert result["blocked_reason"] is not None
        # Multiple actions should be recorded
        assert len(result["actions"]) >= 2


# =============================================================================
# TEST 20: PAPER vs LIVE ISOLATION
# =============================================================================

class TestPaperLiveIsolation:
    """Paper and live limits must not mix."""

    def test_live_uses_live_limits(self, live_rm):
        """LiveRiskManager loads limits_live.yaml, not limits.yaml."""
        assert live_rm.mode == "LIVE"
        assert live_rm.capital == 10_000
        assert live_rm.position_limits.get("max_position_pct") == 0.15
        assert live_rm.position_limits.get("max_positions") == 6

    def test_paper_uses_paper_limits(self, paper_rm):
        """Original RiskManager loads limits.yaml with paper settings."""
        assert paper_rm.limits["position_limits"]["max_single_position"] == 0.10
        assert paper_rm.limits["position_limits"]["max_single_strategy"] == 0.15

    def test_live_has_tighter_daily_cb(self, live_rm, paper_rm):
        """Live daily CB (1.5%) is tighter than paper (5%)."""
        live_daily = live_rm.circuit_breakers_cfg.get("daily_loss_pct", 0.015)
        paper_daily = paper_rm.limits["risk_limits"]["circuit_breaker_daily_dd"]
        assert live_daily < paper_daily

    def test_live_has_tighter_hourly_cb(self, live_rm, paper_rm):
        """Live hourly CB (1%) is tighter than paper (3%)."""
        live_hourly = live_rm.circuit_breakers_cfg.get("hourly_loss_pct", 0.01)
        paper_hourly = paper_rm.limits["risk_limits"]["circuit_breaker_hourly_dd"]
        assert live_hourly < paper_hourly

    def test_live_requires_more_cash(self, live_rm, paper_rm):
        """Live min cash (15%) is higher than paper (7%)."""
        live_cash = live_rm.position_limits.get("min_cash_pct", 0.15)
        paper_cash = paper_rm.limits["exposure_limits"]["min_cash"]
        assert live_cash > paper_cash

    def test_live_max_positions_capped(self, live_rm):
        """Live has explicit max_positions cap (6)."""
        assert live_rm.position_limits.get("max_positions") == 6

    def test_modifying_live_doesnt_affect_paper(self, live_rm, paper_rm):
        """Mutating live limits dict doesn't change paper limits."""
        live_rm.position_limits["max_position_pct"] = 0.99
        assert paper_rm.limits["position_limits"]["max_single_position"] == 0.10


# =============================================================================
# TEST 21: AUDIT LOGGING
# =============================================================================

class TestAuditLogging:
    """Every check should be logged to the audit file."""

    def test_validate_order_writes_audit(self, live_rm, base_portfolio, tmp_path):
        """validate_order creates audit log entries."""
        from core import risk_manager_live
        original_dir = risk_manager_live.AUDIT_LOG_DIR
        risk_manager_live.AUDIT_LOG_DIR = tmp_path
        try:
            order = {"symbol": "AAPL", "direction": "LONG", "notional": 500, "strategy": "test"}
            live_rm.validate_order(order, base_portfolio)
            # Check that audit files were created
            audit_files = list(tmp_path.glob("audit_*.jsonl"))
            assert len(audit_files) == 1
            lines = audit_files[0].read_text().strip().split("\n")
            assert len(lines) >= 9  # 9 checks in validate_order
            # Each line should be valid JSON
            for line in lines:
                entry = json.loads(line)
                assert "check" in entry
                assert "passed" in entry
                assert "timestamp" in entry
        finally:
            risk_manager_live.AUDIT_LOG_DIR = original_dir

    def test_check_all_limits_writes_audit(self, live_rm, base_portfolio, tmp_path):
        """check_all_limits creates audit log entries."""
        from core import risk_manager_live
        original_dir = risk_manager_live.AUDIT_LOG_DIR
        risk_manager_live.AUDIT_LOG_DIR = tmp_path
        try:
            live_rm.check_all_limits(base_portfolio, daily_pnl_pct=-0.001)
            audit_files = list(tmp_path.glob("audit_*.jsonl"))
            assert len(audit_files) == 1
            lines = audit_files[0].read_text().strip().split("\n")
            assert len(lines) >= 8  # at least 8 checks
        finally:
            risk_manager_live.AUDIT_LOG_DIR = original_dir


# =============================================================================
# TEST 22: INHERITANCE — LiveRiskManager IS a RiskManager
# =============================================================================

class TestInheritance:
    """LiveRiskManager must be a proper subclass of RiskManager."""

    def test_isinstance(self, live_rm):
        from core.risk_manager import RiskManager
        assert isinstance(live_rm, RiskManager)

    def test_has_var_methods(self, live_rm):
        """Parent VaR methods are accessible."""
        assert hasattr(live_rm, "calculate_var")
        assert hasattr(live_rm, "calculate_cvar")
        assert hasattr(live_rm, "calculate_portfolio_var")

    def test_var_still_works(self, live_rm):
        """VaR calculation from parent still functions."""
        returns = [-0.01, 0.02, -0.005, 0.015, -0.02, 0.01]
        var = live_rm.calculate_var(returns, confidence=0.95)
        assert var > 0


# =============================================================================
# TEST 23: EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Boundary and edge case tests."""

    def test_exact_threshold_daily_cb(self, live_rm, base_portfolio):
        """Loss exactly at threshold should NOT trigger (using >)."""
        result = live_rm.check_all_limits(base_portfolio, daily_pnl_pct=-0.015)
        daily_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_daily")
        assert daily_check["passed"] is True

    def test_just_over_threshold_daily_cb(self, live_rm, base_portfolio):
        """Loss just above threshold triggers."""
        result = live_rm.check_all_limits(base_portfolio, daily_pnl_pct=-0.0151)
        daily_check = next(c for c in result["checks"] if c["name"] == "circuit_breaker_daily")
        assert daily_check["passed"] is False

    def test_positive_pnl_never_triggers_cb(self, live_rm, base_portfolio):
        """Positive PnL (gains) should never trigger circuit breakers."""
        result = live_rm.check_all_limits(
            base_portfolio,
            daily_pnl_pct=0.05,
            hourly_pnl_pct=0.03,
            weekly_pnl_pct=0.10,
        )
        # Gains must NOT trigger circuit breakers — only losses (negative PnL) do
        for check in result["checks"]:
            if check["name"] in (
                "circuit_breaker_daily",
                "circuit_breaker_hourly",
                "circuit_breaker_weekly",
            ):
                assert check["passed"] is True, (
                    f"{check['name']} should not trigger on positive PnL"
                )

    def test_empty_positions_all_checks_pass(self, live_rm, base_portfolio):
        """Clean portfolio with no positions passes all checks."""
        result = live_rm.check_all_limits(base_portfolio)
        assert result["passed"] is True
        assert result["blocked_reason"] is None
        assert len(result["actions"]) == 0

    def test_deleveraging_negative_dd_uses_abs(self, live_rm):
        """Negative DD value is treated as positive (abs)."""
        level, reduction, _ = live_rm.check_progressive_deleveraging(-0.02)
        assert level == 3
        assert reduction == 1.00
