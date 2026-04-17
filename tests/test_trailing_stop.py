"""Tests for trailing stop futures module."""
import json
from pathlib import Path

import pytest

from core.runtime.trailing_stop_futures import (
    compute_trailing_sl,
    update_trailing_stops,
    TRAILING_CONFIG,
)


class TestComputeTrailingSL:
    def test_no_change_when_sl_already_at_trail(self):
        # SL already at trailing level (4800 * 0.996 = 4780.80), no ratchet needed
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4800,
            current_price=4790, trail_pct=0.004,
            current_sl=4780.80, side="BUY",
        )
        assert result is None

    def test_ratchet_up_when_new_high(self):
        # Entry 4800, highest 4850, trail 0.4% = 4850 * 0.996 = 4830.60
        # Current SL is 4780 -> should ratchet to 4830.60
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4845, trail_pct=0.004,
            current_sl=4780, side="BUY",
        )
        assert result is not None
        assert result == round(4850 * 0.996, 2)
        assert result > 4780

    def test_no_ratchet_down(self):
        # SL already at 4830, new calc would be 4820 -> no change
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4824,
            current_price=4820, trail_pct=0.004,
            current_sl=4830, side="BUY",
        )
        assert result is None

    def test_short_not_supported(self):
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4845, trail_pct=0.004,
            current_sl=4780, side="SELL",
        )
        assert result is None


class TestUpdateTrailingStops:
    def test_no_trailing_for_unknown_strategy(self):
        positions = {"MES": {"strategy": "overnight_mes", "entry": 5500, "sl": 5470, "side": "BUY"}}
        prices = {"MES": 5520}
        mods = update_trailing_stops(positions, prices)
        assert len(mods) == 0

    def test_trailing_for_gold_trend(self):
        positions = {
            "MGC": {
                "strategy": "gold_trend_mgc",
                "entry": 4800, "sl": 4780.80,
                "side": "BUY", "highest_since_entry": 4800,
            }
        }
        prices = {"MGC": 4850}
        mods = update_trailing_stops(positions, prices)
        assert len(mods) == 1
        assert mods[0]["symbol"] == "MGC"
        assert mods[0]["new_sl"] > 4780.80
        # 4850 * 0.996 = 4830.60
        assert mods[0]["new_sl"] == round(4850 * 0.996, 2)

    def test_updates_highest_in_position(self):
        positions = {
            "MGC": {
                "strategy": "gold_trend_mgc",
                "entry": 4800, "sl": 4780.80,
                "side": "BUY", "highest_since_entry": 4800,
            }
        }
        prices = {"MGC": 4860}
        update_trailing_stops(positions, prices)
        assert positions["MGC"]["highest_since_entry"] == 4860

    def test_min_move_filter(self):
        # SL at 4830, new calc at 4830.05 -> less than 1 tick (0.1) -> skip
        positions = {
            "MGC": {
                "strategy": "gold_trend_mgc",
                "entry": 4800, "sl": 4830.00,
                "side": "BUY", "highest_since_entry": 4834.25,
            }
        }
        prices = {"MGC": 4834.30}  # just barely above previous high
        mods = update_trailing_stops(positions, prices)
        assert len(mods) == 0

    def test_no_mod_when_price_missing(self):
        positions = {
            "MGC": {
                "strategy": "gold_trend_mgc",
                "entry": 4800, "sl": 4780,
                "side": "BUY", "highest_since_entry": 4800,
            }
        }
        mods = update_trailing_stops(positions, {})
        assert len(mods) == 0


class TestTrailingConfig:
    def test_gold_trend_mgc_configured(self):
        assert "gold_trend_mgc" in TRAILING_CONFIG
        cfg = TRAILING_CONFIG["gold_trend_mgc"]
        assert cfg["trail_pct"] == 0.004
        assert cfg["tp_pct"] == 0.008
