"""Tests for FX trailing stop — ATR-based dynamic stop."""
import pytest
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.fx_trailing_stop import FXTrailingStop


class TestFXTrailingStopInit:
    def test_default_params(self):
        ts = FXTrailingStop()
        assert ts.activation_atr == 1.5
        assert ts.trail_atr == 1.0

    def test_custom_params(self):
        ts = FXTrailingStop(activation_atr=2.0, trail_atr=1.2)
        assert ts.activation_atr == 2.0
        assert ts.trail_atr == 1.2

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            FXTrailingStop(activation_atr=-1)
        with pytest.raises(ValueError):
            FXTrailingStop(activation_atr=1.0, trail_atr=1.5)  # trail >= activation


class TestTrailingStopLong:
    def test_not_activated_below_threshold(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        # Entry 1.0800, ATR=0.0050, activation at 1.5*0.005=0.0075 profit
        result = ts.update("pos1", 1.0800, 1.0850, 0.0050, 1, 1.0700)
        assert result is None  # profit 0.0050 < 0.0075

    def test_activated_above_threshold(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        # profit = 1.0880 - 1.0800 = 0.0080 > 0.0075
        result = ts.update("pos1", 1.0800, 1.0880, 0.0050, 1, 1.0700)
        assert result is not None
        # new stop = 1.0880 - 1.0*0.005 = 1.0830
        assert abs(result - 1.0830) < 0.00001

    def test_stop_never_moves_down(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        ts.update("pos1", 1.0800, 1.0900, 0.0050, 1, 1.0700)
        # Price drops but stop shouldn't move down
        result = ts.update("pos1", 1.0800, 1.0860, 0.0050, 1, 1.0850)
        assert result is None  # 1.0900 - 0.005 = 1.0850, same as current

    def test_stop_ratchets_up(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        r1 = ts.update("pos1", 1.0800, 1.0900, 0.0050, 1, 1.0700)
        assert r1 is not None  # 1.0900 - 0.005 = 1.0850
        r2 = ts.update("pos1", 1.0800, 1.0950, 0.0050, 1, r1)
        assert r2 is not None  # 1.0950 - 0.005 = 1.0900
        assert r2 > r1


class TestTrailingStopShort:
    def test_short_activated(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        # Short entry 1.0800, price drops to 1.0720 = 80 pips profit
        result = ts.update("pos1", 1.0800, 1.0720, 0.0050, -1, 1.0900)
        assert result is not None
        # new stop = 1.0720 + 0.005 = 1.0770
        assert abs(result - 1.0770) < 0.00001

    def test_short_stop_never_moves_up(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        r1 = ts.update("pos1", 1.0800, 1.0700, 0.0050, -1, 1.0900)
        # Price bounces up
        result = ts.update("pos1", 1.0800, 1.0730, 0.0050, -1, r1)
        assert result is None  # Would move stop up


class TestTrailingStopReset:
    def test_reset_clears_state(self):
        ts = FXTrailingStop()
        ts.update("pos1", 1.0800, 1.0900, 0.0050, 1, 1.0700)
        assert ts.is_activated("pos1") is True
        ts.reset("pos1")
        assert ts.is_activated("pos1") is False

    def test_status(self):
        ts = FXTrailingStop()
        status = ts.get_status("pos1")
        assert status["activated"] is False
        assert status["best_price"] is None


class TestTrailingStopEdgeCases:
    def test_zero_atr(self):
        ts = FXTrailingStop()
        result = ts.update("pos1", 1.0800, 1.0900, 0, 1, 1.0700)
        assert result is None

    def test_multiple_positions(self):
        ts = FXTrailingStop(activation_atr=1.5, trail_atr=1.0)
        r1 = ts.update("eurusd", 1.0800, 1.0900, 0.0050, 1, 1.0700)
        r2 = ts.update("gbpusd", 1.2600, 1.2720, 0.0060, 1, 1.2500)
        assert r1 is not None
        assert r2 is not None
        assert ts.is_activated("eurusd") is True
        assert ts.is_activated("gbpusd") is True
