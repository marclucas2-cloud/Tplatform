"""Tests for calibrated per-strategy kill switch thresholds."""
import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.kill_switch_live import LiveKillSwitch


@pytest.fixture
def kill_switch(tmp_path):
    return LiveKillSwitch(state_path=tmp_path / "ks_state.json")


class TestCalibratedKillSwitch:
    def test_fx_strategy_within_threshold(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"fx_eurusd_trend": -200},  # -2% < -3% threshold
            capital=10000,
        )
        assert result["triggered"] is False
        assert len(result["disabled_strategies"]) == 0

    def test_fx_strategy_exceeds_threshold(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"fx_eurusd_trend": -350},  # -3.5% > -3% threshold
            capital=10000,
        )
        assert result["triggered"] is True
        assert "fx_eurusd_trend" in result["disabled_strategies"]

    def test_eu_intraday_tighter_threshold(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"eu_gap_open": -160},  # -1.6% > -1.5% threshold
            capital=10000,
        )
        assert result["triggered"] is True
        assert "eu_gap_open" in result["disabled_strategies"]

    def test_eu_intraday_within_threshold(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"eu_gap_open": -140},  # -1.4% < -1.5%
            capital=10000,
        )
        assert result["triggered"] is False

    def test_futures_threshold(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"brent_lag_mcl": -260},  # -2.6% > -2.5%
            capital=10000,
        )
        assert result["triggered"] is True

    def test_multiple_strategies_mixed(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {
                "fx_eurusd_trend": -100,  # OK
                "eu_gap_open": -200,      # TRIGGERED (-2% > -1.5%)
                "brent_lag_mcl": -50,     # OK
            },
            capital=10000,
        )
        assert result["triggered"] is True
        assert len(result["disabled_strategies"]) == 1
        assert "eu_gap_open" in result["disabled_strategies"]

    def test_unknown_strategy_uses_default(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"unknown_strat": -250},  # -2.5% > default -2%
            capital=10000,
        )
        assert result["triggered"] is True

    def test_positive_pnl_never_triggers(self, kill_switch):
        result = kill_switch.check_strategy_thresholds(
            {"fx_eurusd_trend": 500},
            capital=10000,
        )
        assert result["triggered"] is False
