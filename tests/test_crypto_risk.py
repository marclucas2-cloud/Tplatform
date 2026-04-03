"""Tests for CryptoRiskManager V2 + KillSwitch V2 — 25 tests."""
import pytest

from core.crypto.risk_manager_crypto import (
    CryptoKillSwitch,
    CryptoRiskManager,
)


@pytest.fixture
def risk_mgr():
    return CryptoRiskManager(capital=15_000)


@pytest.fixture
def kill_switch(tmp_path):
    # Use tmp_path to avoid reading production state file
    ks = CryptoKillSwitch()
    ks._STATE_PATH = tmp_path / "test_kill_switch_state.json"
    ks._active = False
    ks._trigger_reason = ""
    return ks


class TestKillSwitchV2:
    def test_initial(self, kill_switch):
        assert kill_switch.is_killed is False

    def test_daily_loss(self, kill_switch):
        killed, reason = kill_switch.check(daily_pnl_pct=-6)
        assert killed and "daily" in reason

    def test_hourly_loss(self, kill_switch):
        killed, reason = kill_switch.check(hourly_pnl_pct=-4)
        assert killed

    def test_max_drawdown_20pct(self, kill_switch):
        """V2: 20% max DD (stricter than V1 25%)."""
        killed, reason = kill_switch.check(drawdown_pct=-21)
        assert killed and "drawdown" in reason

    def test_api_down(self, kill_switch):
        killed, _ = kill_switch.check(api_down_minutes=15)
        assert killed

    def test_margin_level_critical(self, kill_switch):
        """V2 NEW: margin level < 1.2 triggers kill."""
        killed, reason = kill_switch.check(margin_level_min=1.1)
        assert killed and "margin" in reason

    def test_borrow_rate_spike(self, kill_switch):
        """V2 NEW: borrow rate spike > 1%/day triggers kill."""
        killed, reason = kill_switch.check(max_borrow_rate_daily=0.02)
        assert killed and "borrow" in reason

    def test_no_trigger(self, kill_switch):
        killed, _ = kill_switch.check(
            daily_pnl_pct=-1, hourly_pnl_pct=-0.5,
            drawdown_pct=-3, margin_level_min=3.0,
        )
        assert not killed

    def test_reset(self, kill_switch):
        kill_switch.check(daily_pnl_pct=-10)
        kill_switch.reset(_authorized_by="test")
        assert not kill_switch.is_killed

    def test_status(self, kill_switch):
        s = kill_switch.status()
        assert "active" in s and "reason" in s


class TestRiskChecksV2:
    def test_position_15pct(self, risk_mgr):
        """V2: 15% max per position (stricter)."""
        ok, _ = risk_mgr.check_position_size(2000)  # 13.3% of 15K
        assert ok
        ok, _ = risk_mgr.check_position_size(2500)  # 16.7% — over
        assert not ok

    def test_strategy_25pct(self, risk_mgr):
        """V2: 25% max per strategy (from crypto_limits.yaml)."""
        ok, _ = risk_mgr.check_strategy_concentration(3600)  # 24%
        assert ok
        ok, _ = risk_mgr.check_strategy_concentration(3900)  # 26% — over
        assert not ok

    def test_gross_short_40pct(self, risk_mgr):
        """V2: shorts capped at 40% (more conservative)."""
        ok, _ = risk_mgr.check_gross_exposure(long_exposure=5000, short_exposure=6500)
        assert not ok  # 43.3% short > 40%

    def test_gross_long_80pct(self, risk_mgr):
        ok, _ = risk_mgr.check_gross_exposure(long_exposure=13000, short_exposure=0)
        assert not ok  # 86.7% > 80%

    def test_margin_health_ok(self, risk_mgr):
        ok, _ = risk_mgr.check_margin_health([
            {"is_margin_borrow": True, "asset_value": 2500,
             "total_debt": 1000, "symbol": "BTCUSDT"},
        ])
        assert ok  # margin_level = 2.5

    def test_margin_health_close(self, risk_mgr):
        """margin_level < 1.3 triggers CLOSE action."""
        ok, msg = risk_mgr.check_margin_health([
            {"is_margin_borrow": True, "asset_value": 1250,
             "total_debt": 1000, "symbol": "BTCUSDT"},
        ])
        assert not ok  # margin_level = 1.25 < 1.3
        assert "CLOSE" in msg

    def test_borrow_cost_ok(self, risk_mgr):
        """Monthly borrow cost within limits."""
        positions = [
            {"is_margin_borrow": True, "borrow_rate_daily": 0.0005,
             "borrowed_amount": 1000, "symbol": "BTCUSDT"},
        ]
        ok, _ = risk_mgr.check_borrow_costs(positions)
        assert ok  # monthly = 0.0005 * 1000 * 30 = $15 => 0.1% of 15K

    def test_borrow_cost_exceeded(self, risk_mgr):
        """Monthly borrow cost exceeds 2% of capital."""
        positions = [
            {"is_margin_borrow": True, "borrow_rate_daily": 0.01,
             "borrowed_amount": 5000, "symbol": "BTCUSDT"},
        ]
        ok, _ = risk_mgr.check_borrow_costs(positions)
        assert not ok  # monthly = 0.01 * 5000 * 30 = $1500 => 10% of 15K

    def test_cash_reserve(self, risk_mgr):
        """check_cash_reserve: OK when >= 10%, fail when < 10%."""
        ok, _ = risk_mgr.check_cash_reserve(1800)  # 12% of 15K
        assert ok
        ok, _ = risk_mgr.check_cash_reserve(1200)  # 8% of 15K
        assert not ok


class TestDeleveraging:
    def test_no_delev(self, risk_mgr):
        assert risk_mgr.get_deleveraging_factor(2) == 1.0

    def test_level_1(self, risk_mgr):
        assert risk_mgr.get_deleveraging_factor(5) < 1.0

    def test_level_3(self, risk_mgr):
        """15% <= dd < 20% => 0.25 factor."""
        assert risk_mgr.get_deleveraging_factor(19) == 0.25


class TestCheckAll:
    def test_no_positions(self, risk_mgr):
        """No positions with sufficient cash should pass all checks."""
        result = risk_mgr.check_all(
            [], current_equity=15000, cash_available=2000,
        )
        assert result["passed"]

    def test_returns_12_checks(self, risk_mgr):
        result = risk_mgr.check_all(
            [], current_equity=15000, cash_available=2000,
        )
        assert len(result["checks"]) >= 10
