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

    def test_price_guard_blocks_close_at_touch_after_retrace(self):
        """Reproduces the 2026-05-06 MGC scenario: high=4734.6 atteint le matin,
        prix retrace a 4696 l'apres-midi. Strict trailing 0.4% donnerait
        new_sl=4715.66, mais ce niveau > current_price=4696 -> SL fire au touch
        = close-at-touch deguise. Le garde-fou doit retourner None."""
        result = compute_trailing_sl(
            entry_price=4576.097, highest_price=4734.6,
            current_price=4696.0, trail_pct=0.004,
            current_sl=4484.5, side="BUY",
        )
        assert result is None, (
            "guard must reject SL at/above current price even when ratchet rule "
            "would otherwise approve"
        )

    def test_price_guard_allows_when_sl_safely_below_mark(self):
        # high=4850, current=4845, trail 0.4% -> new_sl=4830.60.
        # Guard threshold = 4845 * 0.9995 = 4842.58. 4830.60 < 4842.58 -> OK.
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4845, trail_pct=0.004,
            current_sl=4780, side="BUY",
        )
        assert result == 4830.60

    def test_price_guard_blocks_when_sl_within_margin(self):
        # high=4850, current=4832 (sharp retrace). trail 0.4% -> new_sl=4830.60.
        # Guard threshold = 4832 * 0.9995 = 4829.58. 4830.60 >= 4829.58 -> block.
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4832.0, trail_pct=0.004,
            current_sl=4780, side="BUY",
        )
        assert result is None

    def test_price_guard_pct_override(self):
        # With a 0%-margin guard the SL must only be strictly below the mark.
        # high=4850, current=4830.61 (one cent above target SL).
        # Default 0.05% guard would block; override 0% must let it pass.
        result_default = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4830.61, trail_pct=0.004,
            current_sl=4780, side="BUY",
        )
        assert result_default is None
        result_override = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=4830.61, trail_pct=0.004,
            current_sl=4780, side="BUY",
            price_guard_pct=0.0,
        )
        assert result_override == 4830.60

    def test_price_guard_skipped_when_current_zero(self):
        # When current price is unknown (0 or negative), don't block —
        # caller is expected to skip the position separately.
        result = compute_trailing_sl(
            entry_price=4800, highest_price=4850,
            current_price=0.0, trail_pct=0.004,
            current_sl=4780, side="BUY",
        )
        assert result == 4830.60


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
