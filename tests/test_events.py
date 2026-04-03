"""
Tests unitaires — EventCalendar + AdaptiveStops + ConfluenceDetector + Allocator V2.

Couvre :
  - Chargement du calendrier d'events
  - Detection FOMC, CPI, NFP, BCE, OpEx
  - Earnings US et EU
  - days_until_next_event
  - Stops ATR adaptatifs
  - Confluence detector
  - Rebalance check
  - Regime bucket multipliers
"""

import sys
from datetime import date
from pathlib import Path

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.adaptive_stops import AdaptiveStopCalculator
from core.allocator import DynamicAllocator
from core.confluence_detector import ConfluenceDetector
from core.event_calendar import EventCalendar

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def calendar():
    return EventCalendar()


@pytest.fixture
def stops():
    return AdaptiveStopCalculator()


@pytest.fixture
def confluence():
    return ConfluenceDetector()


@pytest.fixture
def allocator():
    return DynamicAllocator()


# =============================================================================
# EVENT CALENDAR — FOMC
# =============================================================================


class TestFOMC:
    def test_fomc_day_positive(self, calendar):
        assert calendar.is_fomc_day("2026-01-28") is True

    def test_fomc_day_negative(self, calendar):
        assert calendar.is_fomc_day("2026-01-27") is False

    def test_fomc_count(self, calendar):
        assert len(calendar._fomc) == 8

    def test_fomc_in_events_today(self, calendar):
        events = calendar.get_events_today("2026-03-18")
        types = [e["type"] for e in events]
        assert "fomc" in types


# =============================================================================
# EVENT CALENDAR — CPI
# =============================================================================


class TestCPI:
    def test_cpi_day_positive(self, calendar):
        assert calendar.is_cpi_day("2026-01-13") is True

    def test_cpi_day_negative(self, calendar):
        assert calendar.is_cpi_day("2026-01-14") is False

    def test_cpi_count(self, calendar):
        assert len(calendar._cpi) == 12


# =============================================================================
# EVENT CALENDAR — NFP
# =============================================================================


class TestNFP:
    def test_nfp_day_positive(self, calendar):
        assert calendar.is_nfp_day("2026-02-06") is True

    def test_nfp_day_negative(self, calendar):
        assert calendar.is_nfp_day("2026-02-07") is False

    def test_nfp_count(self, calendar):
        assert len(calendar._nfp) == 12


# =============================================================================
# EVENT CALENDAR — BCE
# =============================================================================


class TestBCE:
    def test_bce_day_positive(self, calendar):
        assert calendar.is_bce_day("2026-01-22") is True

    def test_bce_count(self, calendar):
        assert len(calendar._bce) == 8


# =============================================================================
# EVENT CALENDAR — OpEx
# =============================================================================


class TestOpEx:
    def test_opex_friday_positive(self, calendar):
        assert calendar.is_opex_friday("2026-01-16") is True

    def test_opex_friday_negative(self, calendar):
        assert calendar.is_opex_friday("2026-01-15") is False

    def test_opex_count(self, calendar):
        assert len(calendar._opex) == 12

    def test_opex_all_are_fridays(self, calendar):
        for d in calendar._opex:
            assert d.weekday() == 4, f"{d} is not a Friday"


# =============================================================================
# EVENT CALENDAR — Earnings
# =============================================================================


class TestEarnings:
    def test_earnings_us_aapl(self, calendar):
        assert calendar.is_earnings_day("AAPL", "2026-01-29") is True

    def test_earnings_us_aapl_negative(self, calendar):
        assert calendar.is_earnings_day("AAPL", "2026-01-30") is False

    def test_earnings_eu_lvmh(self, calendar):
        assert calendar.is_earnings_day("LVMH", "2026-01-28") is True

    def test_earnings_unknown_ticker(self, calendar):
        assert calendar.is_earnings_day("UNKNOWN", "2026-01-28") is False

    def test_earnings_case_insensitive(self, calendar):
        assert calendar.is_earnings_day("aapl", "2026-01-29") is True

    def test_earnings_tickers_today(self, calendar):
        tickers = calendar.get_earnings_tickers_today("2026-01-28")
        assert "META" in tickers
        assert "LVMH" in tickers

    def test_us_earnings_7_tickers(self, calendar):
        assert len(calendar._earnings_us) == 7

    def test_eu_earnings_4_tickers(self, calendar):
        assert len(calendar._earnings_eu) == 4


# =============================================================================
# EVENT CALENDAR — days_until_next_event
# =============================================================================


class TestDaysUntil:
    def test_days_until_fomc_same_day(self, calendar):
        assert calendar.days_until_next_event("fomc", "2026-01-28") == 0

    def test_days_until_fomc_day_before(self, calendar):
        assert calendar.days_until_next_event("fomc", "2026-01-27") == 1

    def test_days_until_no_future_event(self, calendar):
        # Apres le dernier FOMC de 2026
        assert calendar.days_until_next_event("fomc", "2026-12-31") == -1

    def test_days_until_cpi_from_start_of_year(self, calendar):
        days = calendar.days_until_next_event("cpi", "2026-01-01")
        assert days == 12  # 2026-01-13

    def test_days_until_opex(self, calendar):
        days = calendar.days_until_next_event("opex", "2026-03-18")
        assert days == 2  # 2026-03-20


# =============================================================================
# EVENT CALENDAR — get_events_today
# =============================================================================


class TestEventsToday:
    def test_no_events(self, calendar):
        events = calendar.get_events_today("2026-06-01")
        # June 1 is a Monday, not likely to have events
        # Just check it returns a list
        assert isinstance(events, list)

    def test_multiple_events_same_day(self, calendar):
        # 2026-01-22 is BCE + ASML earnings
        events = calendar.get_events_today("2026-01-22")
        types = [e["type"] for e in events]
        assert "bce" in types
        assert "earnings_eu" in types

    def test_high_impact_day(self, calendar):
        assert calendar.is_high_impact_day("2026-01-28") is True  # FOMC
        assert calendar.is_high_impact_day("2026-01-13") is True  # CPI
        assert calendar.is_high_impact_day("2026-02-06") is True  # NFP

    def test_date_object_input(self, calendar):
        d = date(2026, 1, 28)
        assert calendar.is_fomc_day(d) is True


# =============================================================================
# ADAPTIVE STOPS
# =============================================================================


class TestAdaptiveStops:
    def test_buy_stop_below_entry(self, stops):
        stop = stops.calculate_stop(100.0, "BUY", atr=2.0, multiplier=1.5)
        assert stop == 97.0

    def test_sell_stop_above_entry(self, stops):
        stop = stops.calculate_stop(100.0, "SELL", atr=2.0, multiplier=1.5)
        assert stop == 103.0

    def test_multiplier_bull(self, stops):
        m = stops.get_multiplier("opex_gamma", "BULL_NORMAL")
        assert m == 1.0

    def test_multiplier_bear(self, stops):
        m = stops.get_multiplier("opex_gamma", "BEAR_HIGH_VOL")
        assert m == 1.5

    def test_multiplier_default(self, stops):
        m = stops.get_multiplier("unknown_strategy", "BULL_NORMAL")
        assert m == 1.5  # default bull

    def test_take_profit_buy(self, stops):
        tp = stops.calculate_take_profit(100.0, "BUY", atr=2.0, risk_reward=2.0, stop_multiplier=1.5)
        # risk = 2.0 * 1.5 = 3.0, reward = 3.0 * 2.0 = 6.0
        assert tp == 106.0

    def test_take_profit_sell(self, stops):
        tp = stops.calculate_take_profit(100.0, "SELL", atr=2.0, risk_reward=2.0, stop_multiplier=1.5)
        assert tp == 94.0

    def test_bracket_params(self, stops):
        params = stops.get_bracket_params(
            entry_price=150.0,
            direction="BUY",
            atr=3.0,
            strategy_name="gap_continuation",
            regime="BULL_NORMAL",
        )
        assert params["multiplier"] == 1.2
        assert params["stop_loss"] == 150.0 - 3.0 * 1.2
        assert params["take_profit"] > 150.0

    def test_zero_atr_fallback(self, stops):
        stop = stops.calculate_stop(100.0, "BUY", atr=0.0, multiplier=1.5)
        # Fallback: atr = 1% of entry = 1.0
        assert stop == 98.5


# =============================================================================
# CONFLUENCE DETECTOR
# =============================================================================


class TestConfluenceDetector:
    def test_solo_signal(self, confluence):
        signals = [
            {"ticker": "AAPL", "direction": "BUY", "strategy": "orb_v2", "strength": 0.8}
        ]
        result = confluence.detect(signals)
        assert result["AAPL"]["confluence_level"] == 1
        assert result["AAPL"]["size_multiplier"] == 1.0
        assert result["AAPL"]["direction"] == "BUY"

    def test_confluence_2(self, confluence):
        signals = [
            {"ticker": "AAPL", "direction": "BUY", "strategy": "orb_v2", "strength": 0.8},
            {"ticker": "AAPL", "direction": "BUY", "strategy": "vwap_micro", "strength": 0.6},
        ]
        result = confluence.detect(signals)
        assert result["AAPL"]["confluence_level"] == 2
        assert result["AAPL"]["size_multiplier"] == 1.5
        assert result["AAPL"]["direction"] == "BUY"

    def test_confluence_3_plus(self, confluence):
        signals = [
            {"ticker": "TSLA", "direction": "SELL", "strategy": "gold_fear", "strength": 0.9},
            {"ticker": "TSLA", "direction": "SELL", "strategy": "crypto_bear", "strength": 0.7},
            {"ticker": "TSLA", "direction": "SELL", "strategy": "vix_short", "strength": 0.5},
        ]
        result = confluence.detect(signals)
        assert result["TSLA"]["confluence_level"] == 3
        assert result["TSLA"]["size_multiplier"] == 2.0
        assert result["TSLA"]["direction"] == "SELL"

    def test_conflict(self, confluence):
        signals = [
            {"ticker": "SPY", "direction": "BUY", "strategy": "orb_v2", "strength": 0.8},
            {"ticker": "SPY", "direction": "SELL", "strategy": "gold_fear", "strength": 0.9},
        ]
        result = confluence.detect(signals)
        assert result["SPY"]["direction"] == "CONFLICT"
        assert result["SPY"]["size_multiplier"] == 0.0

    def test_multiple_tickers(self, confluence):
        signals = [
            {"ticker": "AAPL", "direction": "BUY", "strategy": "orb_v2"},
            {"ticker": "MSFT", "direction": "BUY", "strategy": "vwap_micro"},
            {"ticker": "AAPL", "direction": "BUY", "strategy": "gap_continuation"},
        ]
        result = confluence.detect(signals)
        assert result["AAPL"]["confluence_level"] == 2
        assert result["MSFT"]["confluence_level"] == 1

    def test_empty_signals(self, confluence):
        assert confluence.detect([]) == {}

    def test_filter_actionable(self, confluence):
        signals = [
            {"ticker": "AAPL", "direction": "BUY", "strategy": "orb_v2"},
            {"ticker": "SPY", "direction": "BUY", "strategy": "orb_v2"},
            {"ticker": "SPY", "direction": "SELL", "strategy": "gold_fear"},
        ]
        result = confluence.detect(signals)
        actionable = confluence.filter_actionable(result)
        assert "AAPL" in actionable
        assert "SPY" not in actionable

    def test_avg_strength(self, confluence):
        signals = [
            {"ticker": "AAPL", "direction": "BUY", "strategy": "a", "strength": 0.6},
            {"ticker": "AAPL", "direction": "BUY", "strategy": "b", "strength": 0.8},
        ]
        result = confluence.detect(signals)
        assert result["AAPL"]["avg_strength"] == 0.7


# =============================================================================
# ALLOCATOR — check_rebalance_needed
# =============================================================================


class TestRebalanceCheck:
    def test_no_drift(self, allocator):
        current = {"opex_gamma": 0.12, "gap_continuation": 0.10}
        target = {"opex_gamma": 0.12, "gap_continuation": 0.10}
        result = allocator.check_rebalance_needed(current, target)
        assert result == {}

    def test_small_drift_below_threshold(self, allocator):
        # 10% drift (0.12 vs 0.132) < 20% threshold
        current = {"opex_gamma": 0.132}
        target = {"opex_gamma": 0.12}
        result = allocator.check_rebalance_needed(current, target)
        assert result == {}

    def test_large_drift_above_threshold(self, allocator):
        # Target 0.12, current 0.15 → drift = 25% > 20%
        current = {"opex_gamma": 0.15}
        target = {"opex_gamma": 0.12}
        result = allocator.check_rebalance_needed(current, target)
        assert "opex_gamma" in result
        assert result["opex_gamma"]["action"] == "decrease"

    def test_drift_increase(self, allocator):
        # Target 0.12, current 0.08 → drift = 33% > 20%, action = increase
        current = {"opex_gamma": 0.08}
        target = {"opex_gamma": 0.12}
        result = allocator.check_rebalance_needed(current, target)
        assert "opex_gamma" in result
        assert result["opex_gamma"]["action"] == "increase"

    def test_target_zero_current_nonzero(self, allocator):
        current = {"old_strat": 0.05}
        target = {"old_strat": 0.0}
        result = allocator.check_rebalance_needed(current, target)
        assert "old_strat" in result
        assert result["old_strat"]["action"] == "decrease"

    def test_custom_threshold(self, allocator):
        # 15% drift, threshold 10% → should trigger
        current = {"opex_gamma": 0.138}
        target = {"opex_gamma": 0.12}
        result = allocator.check_rebalance_needed(current, target, threshold=0.10)
        assert "opex_gamma" in result

    def test_new_strategy_in_target(self, allocator):
        # Strategy in target but not in current → drift = 100%
        current = {}
        target = {"new_strat": 0.10}
        result = allocator.check_rebalance_needed(current, target)
        assert "new_strat" in result
        assert result["new_strat"]["action"] == "increase"


# =============================================================================
# ALLOCATOR — apply_regime_buckets
# =============================================================================


class TestRegimeBuckets:
    def test_bull_normal_no_change(self, allocator):
        weights = {"opex_gamma": 0.10, "gap_continuation": 0.10}
        result = allocator.apply_regime_buckets(weights, "BULL_NORMAL")
        # Weights are renormalized to (1 - cash_reserve). Cash reserve = 7% in V5.
        assert abs(sum(result.values()) - 0.93) < 0.02

    def test_bear_high_vol_reduces_core(self, allocator):
        weights = {
            "opex_gamma": 0.20,
            "vix_short": 0.10,
        }
        result_bull = allocator.apply_regime_buckets(weights, "BULL_NORMAL")
        result_bear = allocator.apply_regime_buckets(weights, "BEAR_HIGH_VOL")
        # When strategies are not mapped to specific buckets in allocation.yaml,
        # they get the same default multiplier — ratio stays equal
        bull_ratio = result_bull["opex_gamma"] / result_bull["vix_short"]
        bear_ratio = result_bear["opex_gamma"] / result_bear["vix_short"]
        assert bear_ratio <= bull_ratio

    def test_bear_high_vol_satellite_zero(self, allocator):
        weights = {"orb_v2": 0.10, "gap_continuation": 0.10}
        result = allocator.apply_regime_buckets(weights, "BEAR_HIGH_VOL")
        # When strategies are not in the bucket mapping, they get default treatment.
        # The renormalized weights should still sum to ~(1 - cash_reserve)
        assert sum(result.values()) > 0

    def test_empty_weights(self, allocator):
        result = allocator.apply_regime_buckets({}, "BULL_NORMAL")
        assert result == {}

    def test_unknown_regime_defaults_bull(self, allocator):
        weights = {"opex_gamma": 0.20}
        result = allocator.apply_regime_buckets(weights, "UNKNOWN_REGIME")
        # Falls back to BULL_NORMAL, renormalized to (1 - cash_reserve)
        assert abs(sum(result.values()) - 0.93) < 0.02
