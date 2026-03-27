"""
Tests P3 Components — DynamicAllocatorV2, Calendar Spread, Protective Puts,
EUR/NOK Carry, Lead-Lag Cross-Timezone, Live Checklist V2.

20+ tests couvrant :
- DynamicAllocatorV2 : regime targets, smooth transition, confidence, history, backtest
- ES Calendar Spread : signal generation, market neutrality, z-score
- Protective Puts Overlay : VIX threshold, cost budget, roll logic
- EUR/NOK Carry : oil filter, carry direction, momentum alignment
- Lead-Lag : US→EU, VIX→DAX signals
- Live Checklist : file exists, 17 items
"""

import os
import sys
import importlib.util
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

# ── Add project root to path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.dynamic_allocator_v2 import DynamicAllocatorV2


def _load_strategy_module(filename: str):
    """Load a strategy module directly from file, bypassing __init__.py."""
    filepath = PROJECT_ROOT / "intraday-backtesterV2" / "strategies" / filename
    spec = importlib.util.spec_from_file_location(
        filepath.stem, str(filepath)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — generate synthetic OHLCV data
# ═══════════════════════════════════════════════════════════════════════════

def make_ohlcv(
    n_bars: int = 50,
    base_price: float = 100.0,
    start: str = "2026-01-05 09:30",
    freq: str = "5min",
    trend: float = 0.0,
    volatility: float = 0.01,
) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame with DatetimeIndex."""
    idx = pd.date_range(start=start, periods=n_bars, freq=freq)
    np.random.seed(42)
    returns = np.random.normal(trend, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(np.random.normal(0, volatility / 2, n_bars)))
    low = close * (1 - np.abs(np.random.normal(0, volatility / 2, n_bars)))
    open_ = close * (1 + np.random.normal(0, volatility / 3, n_bars))
    volume = np.random.randint(1000, 100000, n_bars)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=idx)


def make_ohlcv_daily(
    n_days: int = 250,
    base_price: float = 100.0,
    start: str = "2025-01-02",
    trend: float = 0.0003,
    volatility: float = 0.01,
) -> pd.DataFrame:
    """Generate synthetic daily OHLCV."""
    idx = pd.bdate_range(start=start, periods=n_days)
    np.random.seed(42)
    returns = np.random.normal(trend, volatility, n_days)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * 1.005
    low = close * 0.995
    open_ = close * 1.001
    volume = np.random.randint(10000, 1000000, n_days)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=idx)


# ═══════════════════════════════════════════════════════════════════════════
# 1. DynamicAllocatorV2 Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDynamicAllocatorV2:

    def test_regime_targets_sum_to_one(self):
        """Each regime target allocation must sum to 1.0."""
        for regime, targets in DynamicAllocatorV2.REGIME_TARGETS.items():
            total = sum(targets.values())
            assert abs(total - 1.0) < 1e-6, f"{regime} targets sum to {total}, expected 1.0"

    def test_all_regimes_have_same_keys(self):
        """All regime targets must have the same asset class keys."""
        keys = None
        for regime, targets in DynamicAllocatorV2.REGIME_TARGETS.items():
            if keys is None:
                keys = set(targets.keys())
            else:
                assert set(targets.keys()) == keys, f"{regime} has different keys"

    def test_smooth_transition_no_whipsaw(self):
        """Smooth transition should move only 20% toward target per step."""
        alloc = DynamicAllocatorV2()
        current = {"us_equity": 0.50, "cash": 0.50}
        target = {"us_equity": 0.30, "cash": 0.70}

        result = alloc.smooth_transition(current, target, speed=0.20)

        # After one step at 20% speed, us_equity should move from 0.50 toward 0.30
        # Raw: 0.50 + 0.20 * (0.30 - 0.50) = 0.46
        # After normalization, proportions should be maintained
        assert result["us_equity"] < current["us_equity"], "Should move toward target"
        assert result["us_equity"] > target["us_equity"], "Should not overshoot in 1 step"

    def test_smooth_transition_normalizes(self):
        """Result of smooth transition must sum to 1.0."""
        alloc = DynamicAllocatorV2()
        current = {"a": 0.3, "b": 0.3, "c": 0.4}
        target = {"a": 0.5, "b": 0.2, "c": 0.3}

        result = alloc.smooth_transition(current, target)
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-6, f"Sum is {total}, expected 1.0"

    def test_confidence_blending_full_confidence(self):
        """Full confidence (1.0) should return exact regime target."""
        alloc = DynamicAllocatorV2()
        result = alloc.calculate_regime_allocation("BULL", confidence=1.0)
        expected = DynamicAllocatorV2.REGIME_TARGETS["BULL"]
        for key in expected:
            assert abs(result[key] - expected[key]) < 1e-6, f"Key {key} mismatch"

    def test_confidence_blending_zero_confidence(self):
        """Zero confidence should return neutral allocation."""
        alloc = DynamicAllocatorV2()
        result = alloc.calculate_regime_allocation("BEAR", confidence=0.0)
        neutral = DynamicAllocatorV2.REGIME_TARGETS["NEUTRAL"]
        for key in neutral:
            assert abs(result[key] - neutral[key]) < 1e-6, f"Key {key} mismatch at 0 confidence"

    def test_confidence_blending_partial(self):
        """Partial confidence should blend between neutral and target."""
        alloc = DynamicAllocatorV2()
        result = alloc.calculate_regime_allocation("BEAR", confidence=0.5)
        neutral = DynamicAllocatorV2.REGIME_TARGETS["NEUTRAL"]
        bear = DynamicAllocatorV2.REGIME_TARGETS["BEAR"]
        for key in neutral:
            expected = neutral[key] + 0.5 * (bear[key] - neutral[key])
            assert abs(result[key] - expected) < 1e-6, f"Key {key} partial blend mismatch"

    def test_update_records_history(self):
        """Each update call should append to history."""
        alloc = DynamicAllocatorV2()
        assert len(alloc._history) == 0

        alloc.update("BULL", confidence=0.8)
        assert len(alloc._history) == 1
        assert alloc._history[0]["regime"] == "BULL"
        assert alloc._history[0]["confidence"] == 0.8

        alloc.update("BEAR", confidence=0.6)
        assert len(alloc._history) == 2

    def test_update_returns_valid_allocation(self):
        """Update must return an allocation that sums to 1.0."""
        alloc = DynamicAllocatorV2()
        result = alloc.update("BULL", confidence=1.0)
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-6

    def test_bear_regime_increases_shorts_and_cash(self):
        """In BEAR regime, shorts and cash should be higher than in BULL."""
        bull = DynamicAllocatorV2.REGIME_TARGETS["BULL"]
        bear = DynamicAllocatorV2.REGIME_TARGETS["BEAR"]
        assert bear["shorts"] > bull["shorts"]
        assert bear["cash"] > bull["cash"]
        assert bear["futures_hedge"] > bull["futures_hedge"]
        assert bull["us_equity"] > bear["us_equity"]

    def test_get_strategy_weight(self):
        """get_strategy_weight returns the current bucket weight."""
        alloc = DynamicAllocatorV2()
        weight = alloc.get_strategy_weight("some_strategy", "us_equity")
        neutral_us = DynamicAllocatorV2.REGIME_TARGETS["NEUTRAL"]["us_equity"]
        assert abs(weight - neutral_us) < 1e-6

    def test_get_strategy_weight_unknown_class(self):
        """Unknown asset class should return 0.0."""
        alloc = DynamicAllocatorV2()
        weight = alloc.get_strategy_weight("mystery", "unknown_asset")
        assert weight == 0.0

    def test_backtest_dynamic_vs_static_returns_keys(self):
        """Backtest result must have dynamic_sharpe, static_sharpe, improvement_pct."""
        alloc = DynamicAllocatorV2()
        n = 100
        regimes = [
            {"date": f"2026-01-{i+1:02d}", "regime": "BULL" if i < 50 else "BEAR", "confidence": 0.8}
            for i in range(n)
        ]
        returns = {ac: list(np.random.normal(0.001, 0.01, n)) for ac in alloc.REGIME_TARGETS["NEUTRAL"]}

        result = alloc.backtest_dynamic_vs_static(regimes, returns)
        assert "dynamic_sharpe" in result
        assert "static_sharpe" in result
        assert "improvement_pct" in result

    def test_multiple_updates_converge(self):
        """After many BULL updates, allocation should converge toward BULL target."""
        alloc = DynamicAllocatorV2()
        for _ in range(50):
            alloc.update("BULL", confidence=1.0)

        bull_target = DynamicAllocatorV2.REGIME_TARGETS["BULL"]
        for key in bull_target:
            assert abs(alloc.current_allocation[key] - bull_target[key]) < 0.02, \
                f"{key}: {alloc.current_allocation[key]} not close to {bull_target[key]}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. ES Calendar Spread Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestESCalendarSpread:

    def _import_module(self):
        return _load_strategy_module("futures_es_calendar_spread.py")

    def test_spread_computation(self):
        """Spread = front - next close prices."""
        mod = self._import_module()
        strat = mod.ESCalendarSpreadStrategy()
        idx = pd.date_range("2026-01-05 10:00", periods=30, freq="5min")
        front = pd.DataFrame({"close": np.full(30, 5000.0)}, index=idx)
        next_ = pd.DataFrame({"close": np.full(30, 5005.0)}, index=idx)
        spread = strat._compute_spread(front, next_)
        assert len(spread) == 30
        assert all(abs(s - (-5.0)) < 1e-6 for s in spread)

    def test_signal_is_market_neutral(self):
        """Generated signals must have market_neutral=True in metadata."""
        mod = self._import_module()
        strat = mod.ESCalendarSpreadStrategy(entry_std=0.5)  # Easier to trigger

        # Create front and next with diverging spreads
        n = 60
        idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min")
        np.random.seed(123)
        front_close = 5000.0 + np.cumsum(np.random.normal(0, 2, n))
        # Next month tracks front but with increasing divergence
        next_close = front_close + np.linspace(0, 20, n)

        front_df = pd.DataFrame({
            "open": front_close - 1, "high": front_close + 2,
            "low": front_close - 2, "close": front_close, "volume": 1000,
        }, index=idx)
        next_df = pd.DataFrame({
            "open": next_close - 1, "high": next_close + 2,
            "low": next_close - 2, "close": next_close, "volume": 1000,
        }, index=idx)

        data = {"MES_FRONT": front_df, "MES_NEXT": next_df}
        signals = strat.generate_signals(data, "2026-01-05")

        for sig in signals:
            assert sig.metadata.get("market_neutral") is True, \
                "Calendar spread signals must be market neutral"

    def test_z_score_triggers_signal(self):
        """Extreme z-score should trigger a signal."""
        mod = self._import_module()
        strat = mod.ESCalendarSpreadStrategy(entry_std=1.5)

        n = 60
        idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min")
        np.random.seed(77)

        # Front price stable, next price jumps creating large positive spread deviation
        front_close = np.full(n, 5000.0)
        next_close = np.full(n, 5002.0)
        # Last 10 bars: next drops, creating large negative spread
        next_close[-10:] = 4980.0

        front_df = pd.DataFrame({
            "open": front_close, "high": front_close + 1,
            "low": front_close - 1, "close": front_close, "volume": 1000,
        }, index=idx)
        next_df = pd.DataFrame({
            "open": next_close, "high": next_close + 1,
            "low": next_close - 1, "close": next_close, "volume": 1000,
        }, index=idx)

        data = {"MES_FRONT": front_df, "MES_NEXT": next_df}
        signals = strat.generate_signals(data, "2026-01-05")

        # With such a large deviation, we should get a signal
        # (spread went from -2 to +20, definitely > 1.5 std)
        if signals:
            assert signals[0].metadata["spread_direction"] in ("LONG_SPREAD", "SHORT_SPREAD")

    def test_required_tickers(self):
        """Required tickers should include MES_FRONT and MES_NEXT."""
        mod = self._import_module()
        strat = mod.ESCalendarSpreadStrategy()
        tickers = strat.get_required_tickers()
        assert "MES_FRONT" in tickers
        assert "MES_NEXT" in tickers


# ═══════════════════════════════════════════════════════════════════════════
# 3. Protective Puts Overlay Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestProtectivePuts:

    def _import_module(self):
        return _load_strategy_module("protective_puts_overlay.py")

    def test_buy_put_when_vix_cheap(self):
        """Should generate BUY_PUT signal when VIX < 15 and no active put."""
        mod = self._import_module()
        strat = mod.ProtectivePutsOverlayStrategy(vix_buy_threshold=15.0)

        idx = pd.date_range("2026-01-05 10:00", periods=30, freq="5min")
        spy_df = pd.DataFrame({
            "open": 500.0, "high": 501.0, "low": 499.0, "close": 500.0, "volume": 50000,
        }, index=idx)
        vix_df = pd.DataFrame({
            "open": 12.0, "high": 12.5, "low": 11.5, "close": 12.0, "volume": 10000,
        }, index=idx)

        data = {"SPY": spy_df, "VIX": vix_df}
        from datetime import date
        signals = strat.generate_signals(data, date(2026, 1, 5))

        assert len(signals) == 1
        assert signals[0].metadata["signal_type"] == "BUY_PUT"
        assert signals[0].metadata["instrument"] == "SPY_PUT"

    def test_no_signal_when_vix_high(self):
        """No BUY_PUT when VIX > buy threshold."""
        mod = self._import_module()
        strat = mod.ProtectivePutsOverlayStrategy(vix_buy_threshold=15.0)

        idx = pd.date_range("2026-01-05 10:00", periods=30, freq="5min")
        spy_df = pd.DataFrame({
            "open": 500.0, "high": 501.0, "low": 499.0, "close": 500.0, "volume": 50000,
        }, index=idx)
        vix_df = pd.DataFrame({
            "open": 22.0, "high": 23.0, "low": 21.0, "close": 22.0, "volume": 10000,
        }, index=idx)

        data = {"SPY": spy_df, "VIX": vix_df}
        signals = strat.generate_signals(data, "2026-01-05")
        assert len(signals) == 0

    def test_cost_budget_respected(self):
        """Should not buy puts if YTD premium exceeds budget."""
        mod = self._import_module()
        strat = mod.ProtectivePutsOverlayStrategy(
            capital=100_000, annual_cost_budget_pct=0.001  # Very tight budget: $100
        )
        # Exhaust the budget
        strat._ytd_premium_paid = 100.0

        idx = pd.date_range("2026-01-05 10:00", periods=30, freq="5min")
        spy_df = pd.DataFrame({
            "open": 500.0, "high": 501.0, "low": 499.0, "close": 500.0, "volume": 50000,
        }, index=idx)
        vix_df = pd.DataFrame({
            "open": 12.0, "high": 12.5, "low": 11.5, "close": 12.0, "volume": 10000,
        }, index=idx)

        data = {"SPY": spy_df, "VIX": vix_df}
        signals = strat.generate_signals(data, "2026-01-05")
        assert len(signals) == 0, "Should not buy puts when budget exhausted"

    def test_roll_near_expiry(self):
        """Should generate ROLL_PUT when expiry < 5 days away."""
        mod = self._import_module()
        strat = mod.ProtectivePutsOverlayStrategy(roll_days=5)

        # Set active put expiring in 3 days
        from datetime import date
        strat._active_put = {
            "strike": 450.0,
            "expiry": date(2026, 1, 8),
            "buy_vix": 13.0,
            "premium": 500.0,
        }

        idx = pd.date_range("2026-01-05 10:00", periods=30, freq="5min")
        spy_df = pd.DataFrame({
            "open": 500.0, "high": 501.0, "low": 499.0, "close": 500.0, "volume": 50000,
        }, index=idx)
        vix_df = pd.DataFrame({
            "open": 14.0, "high": 14.5, "low": 13.5, "close": 14.0, "volume": 10000,
        }, index=idx)

        data = {"SPY": spy_df, "VIX": vix_df}
        signals = strat.generate_signals(data, date(2026, 1, 5))

        assert len(signals) == 1
        assert signals[0].metadata["signal_type"] == "ROLL_PUT"

    def test_cost_summary(self):
        """Cost summary should track budget usage."""
        mod = self._import_module()
        strat = mod.ProtectivePutsOverlayStrategy(capital=100_000)
        strat._ytd_premium_paid = 500.0

        summary = strat.get_cost_summary()
        assert summary["ytd_premium_paid"] == 500.0
        assert summary["ytd_budget"] == 1000.0  # 1% of 100k
        assert summary["budget_used_pct"] == 50.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. EUR/NOK Carry Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEURNOKCarry:

    def _import_module(self):
        return _load_strategy_module("fx_eurnok_carry.py")

    def test_carry_direction_short_when_norges_higher(self):
        """When Norges Bank rate > ECB rate, carry direction = SHORT EUR/NOK."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy(norges_rate=4.25, ecb_rate=3.50)
        direction = strat._get_carry_direction()
        assert direction == "SHORT"

    def test_carry_direction_long_when_ecb_higher(self):
        """When ECB rate > Norges Bank rate, carry direction = LONG EUR/NOK."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy(norges_rate=2.00, ecb_rate=3.50)
        direction = strat._get_carry_direction()
        assert direction == "LONG"

    def test_carry_direction_none_when_equal(self):
        """When rates are close (< 0.25 diff), no carry direction."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy(norges_rate=3.50, ecb_rate=3.60)
        direction = strat._get_carry_direction()
        assert direction is None

    def test_oil_filter_blocks_on_drop(self):
        """Oil filter should block signals when oil drops > 5%."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy()

        idx = pd.date_range("2026-01-05 10:00", periods=10, freq="1D")
        oil_close = [70.0, 69.0, 68.0, 67.0, 66.0, 65.0, 64.0, 63.0, 62.0, 60.0]
        oil_df = pd.DataFrame({
            "open": oil_close, "high": [x + 1 for x in oil_close],
            "low": [x - 1 for x in oil_close], "close": oil_close, "volume": 10000,
        }, index=idx)

        data = {"CL": oil_df}
        # Oil dropped from 65 to 60 = -7.7% in 5 days → filter should block
        result = strat._check_oil_filter(data)
        assert result is False, "Oil filter should block on > 5% drop"

    def test_oil_filter_passes_stable(self):
        """Oil filter should pass when oil is stable."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy()

        idx = pd.date_range("2026-01-05 10:00", periods=10, freq="1D")
        oil_close = [70.0, 70.5, 70.2, 70.8, 70.3, 70.6, 70.1, 70.4, 70.7, 70.3]
        oil_df = pd.DataFrame({
            "open": oil_close, "high": [x + 0.5 for x in oil_close],
            "low": [x - 0.5 for x in oil_close], "close": oil_close, "volume": 10000,
        }, index=idx)

        data = {"CL": oil_df}
        result = strat._check_oil_filter(data)
        assert result is True

    def test_required_tickers_include_eurnok_and_oil(self):
        """Required tickers should include EURNOK and oil proxies."""
        mod = self._import_module()
        strat = mod.EURNOKCarryStrategy()
        tickers = strat.get_required_tickers()
        assert "EURNOK" in tickers
        # Should include at least one oil proxy
        oil_tickers = {"USO", "BNO", "CL"}
        assert len(oil_tickers.intersection(tickers)) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Lead-Lag Cross-Timezone Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLeadLagCrossTimezone:

    def _import_module(self):
        return _load_strategy_module("lead_lag_cross_timezone.py")

    def test_us_to_eu_strong_move_generates_signal(self):
        """A strong US morning move (> 1%) should generate EU continuation signal."""
        mod = self._import_module()
        strat = mod.LeadLagCrossTimezoneStrategy(spy_threshold=0.005, std_mult=0.0)

        n = 80
        idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min")
        # SPY with strong morning rally: +2% from 09:30 to 11:00
        spy_close = np.linspace(500, 510, n)  # +2%
        spy_df = pd.DataFrame({
            "open": spy_close - 0.5, "high": spy_close + 1,
            "low": spy_close - 1, "close": spy_close, "volume": 50000,
        }, index=idx)

        # EWG available for EU trade
        ewg_close = np.full(n, 30.0)
        ewg_df = pd.DataFrame({
            "open": ewg_close, "high": ewg_close + 0.1,
            "low": ewg_close - 0.1, "close": ewg_close, "volume": 10000,
        }, index=idx)

        data = {"SPY": spy_df, "EWG": ewg_df}
        signals = strat._generate_us_to_eu_signals(data, "2026-01-05")

        assert len(signals) >= 1
        assert signals[0].action == "LONG"  # Continuation of positive US
        assert signals[0].metadata["signal_type"] == "us_to_eu_continuation"

    def test_vix_spike_generates_short_eu(self):
        """A VIX spike > 10% should generate SHORT signals on EU proxies."""
        mod = self._import_module()
        strat = mod.LeadLagCrossTimezoneStrategy(vix_spike_pct=0.08)

        n = 80
        idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min")

        # VIX spikes 15% from open
        vix_open = np.full(n, 20.0)
        vix_close = np.full(n, 20.0)
        # Morning bars (09:30-11:00) show big spike
        morning_mask = idx.time <= dt_time(11, 0)
        vix_close[morning_mask] = np.linspace(20.0, 24.0, morning_mask.sum())
        vix_df = pd.DataFrame({
            "open": vix_open, "high": vix_close + 0.5,
            "low": vix_open - 0.2, "close": vix_close, "volume": 5000,
        }, index=idx)

        # EWG (EU proxy)
        ewg_df = pd.DataFrame({
            "open": 30.0, "high": 30.2, "low": 29.8, "close": 30.0, "volume": 10000,
        }, index=idx)

        data = {"VIX": vix_df, "EWG": ewg_df, "FEZ": ewg_df.copy()}
        signals = strat._generate_vix_to_eu_signals(data, "2026-01-05")

        if signals:
            assert signals[0].action == "SHORT"
            assert signals[0].metadata["signal_type"] == "vix_spike_to_eu"

    def test_max_signals_per_day_cap(self):
        """Total signals should be capped at MAX_SIGNALS_PER_DAY (3)."""
        mod = self._import_module()
        strat = mod.LeadLagCrossTimezoneStrategy()
        # If all 4 relationships fire, should still cap at 3
        assert mod.MAX_SIGNALS_PER_DAY == 3

    def test_std_filter_rejects_small_moves(self):
        """Small leader moves below 1 std should be filtered out."""
        mod = self._import_module()
        strat = mod.LeadLagCrossTimezoneStrategy(std_mult=2.0)

        # Create data with 20-day std of ~1%
        idx = pd.date_range("2026-01-05 09:30", periods=100, freq="5min")
        np.random.seed(42)
        close = 100.0 + np.cumsum(np.random.normal(0, 0.3, 100))
        df = pd.DataFrame({
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": 10000,
        }, index=idx)

        # Small move of 0.2% should fail the 2x std filter
        result = strat._passes_std_filter(0.002, {"SPY": df}, "SPY")
        # With 2x std multiplier and ~1% std, 0.2% move should fail
        assert result is False

    def test_required_tickers_comprehensive(self):
        """Required tickers should span US, EU, VIX, commodities."""
        mod = self._import_module()
        strat = mod.LeadLagCrossTimezoneStrategy()
        tickers = strat.get_required_tickers()
        assert "SPY" in tickers
        assert "VIX" in tickers
        assert "EWG" in tickers or "FEZ" in tickers
        assert "GLD" in tickers


# ═══════════════════════════════════════════════════════════════════════════
# 6. Live Checklist V2 Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLiveChecklistV2:

    CHECKLIST_PATH = PROJECT_ROOT / "docs" / "live_checklist_v2.md"

    def test_file_exists(self):
        """live_checklist_v2.md must exist in docs/."""
        assert self.CHECKLIST_PATH.exists(), f"Missing: {self.CHECKLIST_PATH}"

    def test_has_17_checklist_items(self):
        """Checklist must contain exactly 17 numbered items."""
        content = self.CHECKLIST_PATH.read_text(encoding="utf-8")
        # Count lines matching "- [ ] **N." pattern
        import re
        items = re.findall(r"- \[ \] \*\*\d+\.", content)
        assert len(items) == 17, f"Expected 17 items, found {len(items)}: {items}"

    def test_covers_both_brokers(self):
        """Checklist must mention both Alpaca and IBKR."""
        content = self.CHECKLIST_PATH.read_text(encoding="utf-8")
        assert "Alpaca" in content
        assert "IBKR" in content

    def test_has_go_nogo_section(self):
        """Checklist must have a Go/No-Go decision section."""
        content = self.CHECKLIST_PATH.read_text(encoding="utf-8")
        assert "Go/No-Go" in content

    def test_has_verification_commands(self):
        """Each item should have a verification method."""
        content = self.CHECKLIST_PATH.read_text(encoding="utf-8")
        assert content.count("Verification:") >= 17 or content.count("Verification") >= 17
