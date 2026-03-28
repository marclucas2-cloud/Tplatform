"""
Tests for FX-011 End-of-Month Flow Rebalancing strategy.

Covers 7 tests:
  1. test_eom_long_eur_positive_spy_month — BUY EUR/USD when SPY month > +1%
  2. test_eom_short_eur_negative_spy_month — SELL EUR/USD when SPY month < -1%
  3. test_eom_no_signal_flat_month — no signal when |SPY return| < 1%
  4. test_eom_window_detection — last 3 trading days + first day of next month
  5. test_eom_time_exit_2nd_day — 2nd trading day of month is exit day
  6. test_eom_stop_loss_present — every signal has SL and TP
  7. test_eom_parameter_grid — grid has all expected keys with multiple values
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState, Signal
from strategies_v2.fx.fx_eom_flow import FXEOMFlow, SUPPORTED_PAIRS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _portfolio() -> PortfolioState:
    """Default portfolio state for tests."""
    return PortfolioState(equity=100_000.0, cash=80_000.0)


def _make_daily_df(
    n_bars: int = 200,
    base_price: float = 1.1000,
    volatility: float = 0.002,
    start: str = "2025-10-01",
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic daily OHLCV DataFrame with UTC timestamps."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="B", tz="UTC")
    returns = rng.normal(0, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0002, 0.003, n_bars))
    low = close * (1 - rng.uniform(0.0002, 0.003, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
    volume = rng.randint(5000, 50000, n_bars).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_spy_df_positive(n_bars: int = 40) -> pd.DataFrame:
    """Create SPY data with a positive monthly return (~+5% over last 40 bars).

    The strategy reads get_bars('SPY', 40) so the return must be visible
    within the last 40 bars. We use a strong upward slope.
    """
    idx = pd.date_range("2025-12-01", periods=n_bars, freq="B", tz="UTC")
    # +10% over the full range so that any 40-bar window shows > +1%
    close = np.linspace(440.0, 484.0, n_bars)  # +10% total
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.2
    volume = np.full(n_bars, 50_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_spy_df_negative(n_bars: int = 40) -> pd.DataFrame:
    """Create SPY data with a negative monthly return (~-5% over last 40 bars)."""
    idx = pd.date_range("2025-12-01", periods=n_bars, freq="B", tz="UTC")
    close = np.linspace(484.0, 440.0, n_bars)  # -10% total
    high = close + 1.0
    low = close - 1.0
    open_ = close + 0.2
    volume = np.full(n_bars, 50_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_spy_df_flat(n_bars: int = 40) -> pd.DataFrame:
    """Create SPY data with a flat monthly return (~+0.3%)."""
    idx = pd.date_range("2025-12-01", periods=n_bars, freq="B", tz="UTC")
    close = np.linspace(480.0, 481.44, n_bars)  # +0.3%
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.1
    volume = np.full(n_bars, 50_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ===========================================================================
# Test 1: Long EUR/USD on positive SPY month
# ===========================================================================


class TestEOMLongEurPositiveSPY:
    """test_eom_long_eur_positive_spy_month"""

    def test_eom_long_eur_positive_spy_month(self):
        """When SPY monthly return > +1%, should BUY EUR/USD (sell USD)."""
        strat = FXEOMFlow()

        # Create FX data for all supported pairs
        eurusd_df = _make_daily_df(n_bars=200, base_price=1.1000, start="2025-10-01")
        gbpusd_df = _make_daily_df(n_bars=200, base_price=1.2700, start="2025-10-01", seed=43)
        usdjpy_df = _make_daily_df(n_bars=200, base_price=150.0, volatility=0.003, start="2025-10-01", seed=44)
        spy_df = _make_spy_df_positive(n_bars=200)
        spy_df.index = eurusd_df.index  # align dates

        data_sources = {
            "EURUSD": eurusd_df,
            "GBPUSD": gbpusd_df,
            "USDJPY": usdjpy_df,
            "SPY": spy_df,
        }
        feed = DataFeed(data_sources)
        strat.set_data_feed(feed)

        # Pick a bar on the last trading day of December 2025
        # December 31, 2025 is a Wednesday (weekday)
        target_ts = pd.Timestamp("2025-12-31", tz="UTC")

        # Find the nearest bar to target
        mask = eurusd_df.index <= target_ts
        if not mask.any():
            pytest.skip("No bar near target date")

        bar_idx = eurusd_df.index[mask][-1]
        feed.set_timestamp(bar_idx + pd.Timedelta(minutes=1))

        row = eurusd_df.loc[bar_idx]
        bar = Bar(
            symbol="EURUSD",
            timestamp=bar_idx,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

        signal = strat.on_bar(bar, _portfolio())

        # With positive SPY month, expect BUY on EURUSD (USD selling)
        assert signal is not None, "Expected BUY signal on positive SPY month"
        assert signal.side == "BUY"
        assert signal.symbol == "EURUSD"
        assert signal.strategy_name == "fx_eom_flow"


# ===========================================================================
# Test 2: Short EUR/USD on negative SPY month
# ===========================================================================


class TestEOMShortEurNegativeSPY:
    """test_eom_short_eur_negative_spy_month"""

    def test_eom_short_eur_negative_spy_month(self):
        """When SPY monthly return < -1%, should SELL EUR/USD (buy USD)."""
        strat = FXEOMFlow()

        eurusd_df = _make_daily_df(n_bars=200, base_price=1.1000, start="2025-10-01")
        gbpusd_df = _make_daily_df(n_bars=200, base_price=1.2700, start="2025-10-01", seed=43)
        usdjpy_df = _make_daily_df(n_bars=200, base_price=150.0, volatility=0.003, start="2025-10-01", seed=44)
        spy_df = _make_spy_df_negative(n_bars=200)
        spy_df.index = eurusd_df.index

        data_sources = {
            "EURUSD": eurusd_df,
            "GBPUSD": gbpusd_df,
            "USDJPY": usdjpy_df,
            "SPY": spy_df,
        }
        feed = DataFeed(data_sources)
        strat.set_data_feed(feed)

        target_ts = pd.Timestamp("2025-12-31", tz="UTC")
        mask = eurusd_df.index <= target_ts
        if not mask.any():
            pytest.skip("No bar near target date")

        bar_idx = eurusd_df.index[mask][-1]
        feed.set_timestamp(bar_idx + pd.Timedelta(minutes=1))

        row = eurusd_df.loc[bar_idx]
        bar = Bar(
            symbol="EURUSD",
            timestamp=bar_idx,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

        signal = strat.on_bar(bar, _portfolio())

        # With negative SPY month, expect SELL on EURUSD (USD buying)
        assert signal is not None, "Expected SELL signal on negative SPY month"
        assert signal.side == "SELL"
        assert signal.symbol == "EURUSD"


# ===========================================================================
# Test 3: No signal on flat SPY month
# ===========================================================================


class TestEOMNoSignalFlatMonth:
    """test_eom_no_signal_flat_month"""

    def test_eom_no_signal_flat_month(self):
        """When |SPY monthly return| < 1%, no signal should be generated."""
        strat = FXEOMFlow()

        eurusd_df = _make_daily_df(n_bars=200, base_price=1.1000, start="2025-10-01")
        gbpusd_df = _make_daily_df(n_bars=200, base_price=1.2700, start="2025-10-01", seed=43)
        usdjpy_df = _make_daily_df(n_bars=200, base_price=150.0, volatility=0.003, start="2025-10-01", seed=44)
        spy_df = _make_spy_df_flat(n_bars=200)
        spy_df.index = eurusd_df.index

        data_sources = {
            "EURUSD": eurusd_df,
            "GBPUSD": gbpusd_df,
            "USDJPY": usdjpy_df,
            "SPY": spy_df,
        }
        feed = DataFeed(data_sources)
        strat.set_data_feed(feed)

        target_ts = pd.Timestamp("2025-12-31", tz="UTC")
        mask = eurusd_df.index <= target_ts
        if not mask.any():
            pytest.skip("No bar near target date")

        bar_idx = eurusd_df.index[mask][-1]
        feed.set_timestamp(bar_idx + pd.Timedelta(minutes=1))

        row = eurusd_df.loc[bar_idx]
        bar = Bar(
            symbol="EURUSD",
            timestamp=bar_idx,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None, (
            f"Signal generated despite flat SPY month (< 1% return): {signal}"
        )


# ===========================================================================
# Test 4: EOM window detection
# ===========================================================================


class TestEOMWindowDetection:
    """test_eom_window_detection"""

    def test_eom_window_detection(self):
        """EOM window should include last 3 trading days + first day of next month."""
        strat = FXEOMFlow()

        # December 2025 calendar:
        # Last day: Dec 31 (Wed) — last 3 trading days: Mon 29, Tue 30, Wed 31
        # Jan 2026: first trading day is Thu Jan 1? No, Jan 1 is holiday.
        # For simplicity, our calendar only skips weekends (not holidays).
        # Jan 1, 2026 = Thursday (weekday) -> first trading day

        # Last 3 trading days of Dec 2025
        assert strat.is_eom_window(date(2025, 12, 29)) is True   # Mon
        assert strat.is_eom_window(date(2025, 12, 30)) is True   # Tue
        assert strat.is_eom_window(date(2025, 12, 31)) is True   # Wed

        # First trading day of Jan 2026
        assert strat.is_eom_window(date(2026, 1, 1)) is True     # Thu (first weekday)

        # Mid-month dates should NOT be in the window
        assert strat.is_eom_window(date(2025, 12, 15)) is False  # Mon, mid-month
        assert strat.is_eom_window(date(2025, 12, 22)) is False  # Mon
        assert strat.is_eom_window(date(2026, 1, 5)) is False    # Mon, 2nd week

        # Test another month: November 2025
        # Nov 30 = Sunday -> last 3 trading days: Wed 26, Thu 27, Fri 28
        assert strat.is_eom_window(date(2025, 11, 26)) is True   # Wed
        assert strat.is_eom_window(date(2025, 11, 27)) is True   # Thu
        assert strat.is_eom_window(date(2025, 11, 28)) is True   # Fri
        assert strat.is_eom_window(date(2025, 11, 25)) is False  # Tue, not in window

        # First trading day of Dec 2025: Mon Dec 1
        assert strat.is_eom_window(date(2025, 12, 1)) is True

    def test_eom_window_with_custom_days(self):
        """Adjusting eom_window_days should change the detection window."""
        strat = FXEOMFlow()
        strat.eom_window_days = 2  # Only last 2 trading days

        # With 2-day window in Dec 2025: Tue 30 and Wed 31
        assert strat.is_eom_window(date(2025, 12, 29)) is False  # Mon, outside
        assert strat.is_eom_window(date(2025, 12, 30)) is True   # Tue
        assert strat.is_eom_window(date(2025, 12, 31)) is True   # Wed


# ===========================================================================
# Test 5: Time exit on 2nd trading day
# ===========================================================================


class TestEOMTimeExit:
    """test_eom_time_exit_2nd_day"""

    def test_eom_time_exit_2nd_day(self):
        """The 2nd trading day of the month should be detected as exit day."""
        strat = FXEOMFlow()

        # January 2026:
        # Thu 1 = 1st trading day
        # Fri 2 = 2nd trading day (exit day)
        assert strat.is_exit_day(date(2026, 1, 2)) is True

        # Other days should NOT be exit days
        assert strat.is_exit_day(date(2026, 1, 1)) is False   # 1st trading day
        assert strat.is_exit_day(date(2026, 1, 5)) is False   # 3rd trading day (Mon)
        assert strat.is_exit_day(date(2025, 12, 31)) is False # Last day of month

        # February 2026: Feb 1 = Sunday -> first trading day = Mon Feb 2
        # 2nd trading day = Tue Feb 3
        assert strat.is_exit_day(date(2026, 2, 3)) is True
        assert strat.is_exit_day(date(2026, 2, 2)) is False  # 1st trading day

    def test_exit_day_with_custom_n(self):
        """Adjusting exit_trading_day should change the exit detection."""
        strat = FXEOMFlow()
        strat.exit_trading_day = 3  # Exit on 3rd trading day

        # Jan 2026: Thu 1, Fri 2, Mon 5
        assert strat.is_exit_day(date(2026, 1, 5)) is True
        assert strat.is_exit_day(date(2026, 1, 2)) is False


# ===========================================================================
# Test 6: Stop loss always present
# ===========================================================================


class TestEOMStopLossPresent:
    """test_eom_stop_loss_present"""

    def test_eom_stop_loss_present(self):
        """Every signal from FXEOMFlow must include stop_loss and take_profit."""
        strat = FXEOMFlow()

        eurusd_df = _make_daily_df(n_bars=200, base_price=1.1000, start="2025-10-01")
        gbpusd_df = _make_daily_df(n_bars=200, base_price=1.2700, start="2025-10-01", seed=43)
        usdjpy_df = _make_daily_df(n_bars=200, base_price=150.0, volatility=0.003, start="2025-10-01", seed=44)
        spy_df = _make_spy_df_positive(n_bars=200)
        spy_df.index = eurusd_df.index

        data_sources = {
            "EURUSD": eurusd_df,
            "GBPUSD": gbpusd_df,
            "USDJPY": usdjpy_df,
            "SPY": spy_df,
        }
        feed = DataFeed(data_sources)
        strat.set_data_feed(feed)

        # Walk through all EOM-window bars and check signals
        signals_found = 0
        for i in range(50, len(eurusd_df)):
            bar_idx = eurusd_df.index[i]
            bar_date = bar_idx.date()

            if not strat.is_eom_window(bar_date):
                continue

            feed.set_timestamp(bar_idx + pd.Timedelta(minutes=1))
            row = eurusd_df.loc[bar_idx]
            bar = Bar(
                symbol="EURUSD",
                timestamp=bar_idx,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )

            signal = strat.on_bar(bar, _portfolio())
            if signal is not None:
                signals_found += 1
                assert signal.stop_loss is not None, (
                    f"Signal missing stop_loss: {signal}"
                )
                assert isinstance(signal.stop_loss, float)
                assert signal.take_profit is not None, (
                    f"Signal missing take_profit: {signal}"
                )
                assert isinstance(signal.take_profit, float)

                # Verify SL/TP relationship based on side
                if signal.side == "BUY":
                    assert signal.stop_loss < bar.close, (
                        f"BUY SL {signal.stop_loss} should be < entry {bar.close}"
                    )
                    assert signal.take_profit > bar.close, (
                        f"BUY TP {signal.take_profit} should be > entry {bar.close}"
                    )
                elif signal.side == "SELL":
                    assert signal.stop_loss > bar.close, (
                        f"SELL SL {signal.stop_loss} should be > entry {bar.close}"
                    )
                    assert signal.take_profit < bar.close, (
                        f"SELL TP {signal.take_profit} should be < entry {bar.close}"
                    )

        assert signals_found > 0, "No signals generated during EOM windows"


# ===========================================================================
# Test 7: Parameter grid
# ===========================================================================


class TestEOMParameterGrid:
    """test_eom_parameter_grid"""

    def test_eom_parameter_grid(self):
        """Parameter grid must have all tunable keys with multiple values."""
        strat = FXEOMFlow()
        grid = strat.get_parameter_grid()

        expected_keys = {
            "atr_period", "sl_atr_mult", "tp_atr_mult",
            "min_monthly_return", "eom_window_days", "exit_trading_day",
        }
        assert expected_keys.issubset(set(grid.keys())), (
            f"Missing grid keys: {expected_keys - set(grid.keys())}"
        )

        # Each key should have at least 2 values
        for key, values in grid.items():
            assert isinstance(values, list), f"{key} is not a list"
            assert len(values) >= 2, f"{key} has fewer than 2 values"

        # Check specific grid values from the spec
        assert grid["sl_atr_mult"] == [1.0, 1.5, 2.0]
        assert grid["tp_atr_mult"] == [1.5, 2.0, 2.5]
        assert grid["atr_period"] == [10, 14, 20]

        # Verify get_parameters returns current values
        params = strat.get_parameters()
        assert params["atr_period"] == 14
        assert params["sl_atr_mult"] == 1.5
        assert params["tp_atr_mult"] == 2.0
        assert params["min_monthly_return"] == 1.0
        assert params["eom_window_days"] == 3
        assert params["exit_trading_day"] == 2

    def test_supported_pairs(self):
        """Strategy should define the correct supported pairs."""
        assert "EURUSD" in SUPPORTED_PAIRS
        assert "GBPUSD" in SUPPORTED_PAIRS
        assert "USDJPY" in SUPPORTED_PAIRS
        assert len(SUPPORTED_PAIRS) == 3

    def test_strategy_metadata(self):
        """Strategy name, asset_class, and broker should be correct."""
        strat = FXEOMFlow()
        assert strat.name == "fx_eom_flow"
        assert strat.asset_class == "fx"
        assert strat.broker == "ibkr"
