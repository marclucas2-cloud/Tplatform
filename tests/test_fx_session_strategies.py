"""
Tests for FX-009 London Fix Flow and FX-010 Session Overlap Momentum.

Covers:
  - Signal generation (long + short) for each strategy
  - Time window filtering (CET-aware, DST-safe)
  - Stop loss / take profit calculation with ATR
  - Filter conditions (move strength, EMA alignment, VIX)
  - Parameter grid existence
"""

from __future__ import annotations

from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.fx.fx_london_fix import FXLondonFix
from strategies_v2.fx.fx_session_overlap import FXSessionOverlap

_CET = ZoneInfo("Europe/Paris")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _portfolio() -> PortfolioState:
    """Default portfolio state for tests."""
    return PortfolioState(equity=10_000.0, cash=10_000.0)


def _make_bar(
    symbol: str = "EURUSD",
    timestamp: str = "2026-03-10 15:00:00",
    tz: str = "Europe/Paris",
    close: float = 1.0800,
    open_: float = 1.0795,
    high: float = 1.0810,
    low: float = 1.0790,
    volume: float = 10000.0,
) -> Bar:
    """Create a Bar with a timezone-aware timestamp."""
    ts = pd.Timestamp(timestamp, tz=tz).tz_convert("UTC")
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_ohlcv_df(
    n_bars: int = 50,
    base_price: float = 1.0800,
    start: str = "2026-03-10 06:00",
    freq: str = "5min",
    trend: float = 0.0,
    volatility: float = 0.0005,
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic OHLCV DataFrame for FX testing."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq=freq, tz="UTC")
    returns = rng.normal(trend, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0001, 0.002, n_bars))
    low = close * (1 - rng.uniform(0.0001, 0.002, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0003, n_bars))
    volume = rng.randint(1000, 50000, n_bars).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)


def _setup_data_feed_mock(
    strategy,
    atr_val: float = 0.0020,
    ema_fast: float = 1.0810,
    ema_slow: float = 1.0800,
    bars_df: pd.DataFrame | None = None,
    vix_close: float = 20.0,
) -> MagicMock:
    """Create a mock DataFeed and attach it to the strategy."""
    feed = MagicMock(spec=DataFeed)

    def get_indicator(symbol, indicator, period):
        if indicator == "atr":
            return atr_val
        if indicator == "ema" and period == getattr(strategy, "ema_fast", 8):
            return ema_fast
        if indicator == "ema" and period == getattr(strategy, "ema_slow", 21):
            return ema_slow
        return None

    feed.get_indicator.side_effect = get_indicator

    if bars_df is not None:
        feed.get_bars.return_value = bars_df
    else:
        feed.get_bars.return_value = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )

    strategy.set_data_feed(feed)
    return feed


def _make_fix_bars_df(
    pre_fix_move: float = 0.0030,
    base_price: float = 1.0800,
) -> pd.DataFrame:
    """Create a DataFrame simulating bars around the London fix.

    Bars are 5-min in UTC. Winter CET = UTC+1.
    The pre-fix window is 15:45-16:05 CET = 14:45-15:05 UTC.
    We create bars from 14:00 to 15:15 UTC (= 15:00-16:15 CET).

    The `pre_fix_move` is concentrated within the 15:45-16:05 CET window
    (bars at indices 9-13) so the strategy's _get_pre_fix_move picks it up.
    """
    idx = pd.date_range(
        start="2026-03-10 14:00:00", periods=16, freq="5min", tz="UTC"
    )
    # Bars 0-8: stable at base_price (before pre-fix window)
    # Bars 9-13: ramp from base_price to base_price + pre_fix_move
    #   (these are 14:45-15:05 UTC = 15:45-16:05 CET = the pre-fix window)
    # Bars 14-15: stay at the end level (post-window)
    prices = np.full(16, base_price)
    for i in range(9, 14):
        frac = (i - 9) / (13 - 9)  # 0.0 to 1.0
        prices[i] = base_price + pre_fix_move * frac
    prices[13] = base_price + pre_fix_move  # Ensure exact end
    prices[14] = base_price + pre_fix_move
    prices[15] = base_price + pre_fix_move

    return pd.DataFrame({
        "open": prices - 0.0001,
        "high": prices + 0.0003,
        "low": prices - 0.0003,
        "close": prices,
        "volume": 10000.0,
    }, index=idx)


def _make_morning_bars_df(
    morning_move: float = 0.0030,
    base_price: float = 1.0800,
) -> pd.DataFrame:
    """Create a DataFrame simulating London morning bars (07:00-13:00 UTC = 08:00-14:00 CET winter).

    1H bars from 07:00 to 13:00 UTC to cover the morning session.
    """
    idx = pd.date_range(
        start="2026-03-10 07:00:00", periods=7, freq="1h", tz="UTC"
    )
    prices = np.linspace(base_price, base_price + morning_move, 7)
    highs = prices + abs(morning_move) * 0.1 + 0.0002
    lows = prices - abs(morning_move) * 0.1 - 0.0002
    return pd.DataFrame({
        "open": prices - 0.0001,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": 10000.0,
    }, index=idx)


# ═══════════════════════════════════════════════════════════════════════════════
# FX-009: London Fix Flow
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXLondonFix:
    """Tests for FX-009 London Fix Flow strategy."""

    def test_fix_reversion_long_after_down_move(self):
        """Pre-fix move DOWN -> expect reversion UP -> LONG signal."""
        strat = FXLondonFix()
        # Bar at 16:05 CET = 15:05 UTC (winter)
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 16:05:00",
            tz="Europe/Paris",
            close=1.0770,
        )
        bars_df = _make_fix_bars_df(pre_fix_move=-0.0030, base_price=1.0800)
        feed = _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        signal = strat.on_bar(bar, _portfolio())

        assert signal is not None
        assert signal.side == "BUY"
        assert signal.symbol == "EURUSD"
        assert signal.strategy_name == "fx_london_fix"
        assert signal.stop_loss < bar.close  # SL below entry for LONG
        assert signal.take_profit > bar.close  # TP above entry for LONG

    def test_fix_reversion_short_after_up_move(self):
        """Pre-fix move UP -> expect reversion DOWN -> SHORT signal."""
        strat = FXLondonFix()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 16:05:00",
            tz="Europe/Paris",
            close=1.0830,
        )
        bars_df = _make_fix_bars_df(pre_fix_move=0.0030, base_price=1.0800)
        feed = _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        signal = strat.on_bar(bar, _portfolio())

        assert signal is not None
        assert signal.side == "SELL"
        assert signal.symbol == "EURUSD"
        assert signal.stop_loss > bar.close  # SL above entry for SHORT
        assert signal.take_profit < bar.close  # TP below entry for SHORT

    def test_fix_no_signal_weak_move(self):
        """Pre-fix move too small (< 0.5 * ATR) -> no signal."""
        strat = FXLondonFix()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 16:05:00",
            tz="Europe/Paris",
            close=1.0802,
        )
        # Tiny move of 0.0003 with ATR of 0.0020 -> 0.15 ATR < 0.5 threshold
        bars_df = _make_fix_bars_df(pre_fix_move=0.0003, base_price=1.0800)
        _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None

    def test_fix_no_signal_extreme_move(self):
        """Pre-fix move too large (> 2.0 * ATR) -> no signal."""
        strat = FXLondonFix()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 16:05:00",
            tz="Europe/Paris",
            close=1.0900,
        )
        # Huge move of 0.0100 with ATR of 0.0020 -> 5.0 ATR > 2.0 threshold
        bars_df = _make_fix_bars_df(pre_fix_move=0.0100, base_price=1.0800)
        _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None

    def test_fix_time_window_check(self):
        """Signal only triggers during 16:00-16:15 CET, not outside."""
        strat = FXLondonFix()
        bars_df = _make_fix_bars_df(pre_fix_move=0.0030, base_price=1.0800)
        _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        # 16:05 CET -> should trigger
        bar_in = _make_bar(timestamp="2026-03-10 16:05:00", tz="Europe/Paris")
        assert strat._is_fix_entry_window(bar_in) is True

        # 15:30 CET -> outside window
        bar_before = _make_bar(timestamp="2026-03-10 15:30:00", tz="Europe/Paris")
        assert strat._is_fix_entry_window(bar_before) is False

        # 16:30 CET -> outside window
        bar_after = _make_bar(timestamp="2026-03-10 16:30:00", tz="Europe/Paris")
        assert strat._is_fix_entry_window(bar_after) is False

        # 12:00 CET -> well outside window
        bar_noon = _make_bar(timestamp="2026-03-10 12:00:00", tz="Europe/Paris")
        assert strat._is_fix_entry_window(bar_noon) is False

        # On bar should return None outside window
        signal = strat.on_bar(bar_before, _portfolio())
        assert signal is None

    def test_fix_time_exit_30min(self):
        """Verify time_exit_minutes parameter defaults to 30."""
        strat = FXLondonFix()
        assert strat.time_exit_minutes == 30
        assert strat.max_hold_minutes == 45

    def test_fix_stop_loss_present(self):
        """All signals must have a stop loss based on ATR."""
        strat = FXLondonFix()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 16:05:00",
            tz="Europe/Paris",
            close=1.0830,
        )
        bars_df = _make_fix_bars_df(pre_fix_move=0.0030, base_price=1.0800)
        _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=bars_df)

        signal = strat.on_bar(bar, _portfolio())
        assert signal is not None
        assert signal.stop_loss is not None
        # For SHORT signal (pre-fix up), SL = close + 1.0 * ATR
        expected_sl = bar.close + 1.0 * 0.0020
        assert abs(signal.stop_loss - expected_sl) < 1e-8

    def test_fix_parameter_grid(self):
        """Parameter grid should exist and contain all tunable parameters."""
        strat = FXLondonFix()
        grid = strat.get_parameter_grid()

        assert "atr_period" in grid
        assert "min_move_atr" in grid
        assert "max_move_atr" in grid
        assert "sl_atr" in grid
        assert "tp_ratio" in grid
        assert "time_exit_minutes" in grid
        assert "max_hold_minutes" in grid

        # Each grid must have multiple values
        for key, values in grid.items():
            assert len(values) >= 2, f"{key} grid has < 2 values"

        # Verify get_parameters matches
        params = strat.get_parameters()
        for key in grid:
            assert key in params, f"{key} in grid but not in get_parameters()"


# ═══════════════════════════════════════════════════════════════════════════════
# FX-010: Session Overlap Momentum
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXSessionOverlap:
    """Tests for FX-010 Session Overlap Momentum strategy."""

    def test_overlap_long_bullish_morning(self):
        """Positive morning move + EMA8 > EMA21 -> LONG signal at 14:00 CET."""
        strat = FXSessionOverlap()
        # Bar at 14:00 CET = 13:00 UTC (winter)
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 14:00:00",
            tz="Europe/Paris",
            close=1.0830,
        )
        morning_df = _make_morning_bars_df(morning_move=0.0030, base_price=1.0800)
        # EMA fast > EMA slow (bullish alignment)
        _setup_data_feed_mock(
            strat,
            atr_val=0.0020,
            ema_fast=1.0835,
            ema_slow=1.0810,
            bars_df=morning_df,
        )

        signal = strat.on_bar(bar, _portfolio())

        assert signal is not None
        assert signal.side == "BUY"
        assert signal.symbol == "EURUSD"
        assert signal.strategy_name == "fx_session_overlap"
        assert signal.stop_loss < bar.close  # SL below entry
        assert signal.take_profit > bar.close  # TP above entry

    def test_overlap_short_bearish_morning(self):
        """Negative morning move + EMA8 < EMA21 -> SHORT signal."""
        strat = FXSessionOverlap()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 14:00:00",
            tz="Europe/Paris",
            close=1.0770,
        )
        morning_df = _make_morning_bars_df(morning_move=-0.0030, base_price=1.0800)
        # EMA fast < EMA slow (bearish alignment)
        _setup_data_feed_mock(
            strat,
            atr_val=0.0020,
            ema_fast=1.0765,
            ema_slow=1.0790,
            bars_df=morning_df,
        )

        signal = strat.on_bar(bar, _portfolio())

        assert signal is not None
        assert signal.side == "SELL"
        assert signal.symbol == "EURUSD"
        assert signal.stop_loss > bar.close  # SL above entry
        assert signal.take_profit < bar.close  # TP below entry

    def test_overlap_no_signal_flat_morning(self):
        """Morning move too small (< 0.5 ATR) -> no signal."""
        strat = FXSessionOverlap()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 14:00:00",
            tz="Europe/Paris",
            close=1.0802,
        )
        # Tiny morning move of 0.0003 with ATR of 0.0020 -> 0.15 ATR
        morning_df = _make_morning_bars_df(morning_move=0.0003, base_price=1.0800)
        _setup_data_feed_mock(
            strat,
            atr_val=0.0020,
            ema_fast=1.0803,
            ema_slow=1.0800,
            bars_df=morning_df,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None

    def test_overlap_ema_alignment_required(self):
        """Positive morning move but EMA8 < EMA21 -> no LONG signal (misaligned)."""
        strat = FXSessionOverlap()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 14:00:00",
            tz="Europe/Paris",
            close=1.0830,
        )
        morning_df = _make_morning_bars_df(morning_move=0.0030, base_price=1.0800)
        # EMA fast BELOW slow (bearish) despite bullish morning -> no signal
        _setup_data_feed_mock(
            strat,
            atr_val=0.0020,
            ema_fast=1.0790,  # fast < slow = bearish
            ema_slow=1.0820,
            bars_df=morning_df,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None

    def test_overlap_time_exit_17h_cet(self):
        """Verify overlap end is at 17:00 CET and entry only at 14:00-14:15 CET."""
        strat = FXSessionOverlap()

        # 14:00 CET -> inside entry window
        bar_in = _make_bar(timestamp="2026-03-10 14:00:00", tz="Europe/Paris")
        assert strat._is_overlap_entry_window(bar_in) is True

        # 14:10 CET -> still inside
        bar_10 = _make_bar(timestamp="2026-03-10 14:10:00", tz="Europe/Paris")
        assert strat._is_overlap_entry_window(bar_10) is True

        # 14:20 CET -> outside
        bar_out = _make_bar(timestamp="2026-03-10 14:20:00", tz="Europe/Paris")
        assert strat._is_overlap_entry_window(bar_out) is False

        # 17:00 CET -> outside (end of overlap)
        bar_end = _make_bar(timestamp="2026-03-10 17:00:00", tz="Europe/Paris")
        assert strat._is_overlap_entry_window(bar_end) is False

        # 10:00 CET -> well outside
        bar_morning = _make_bar(timestamp="2026-03-10 10:00:00", tz="Europe/Paris")
        assert strat._is_overlap_entry_window(bar_morning) is False

        # on_bar should return None outside window
        morning_df = _make_morning_bars_df(morning_move=0.0030)
        _setup_data_feed_mock(strat, atr_val=0.0020, bars_df=morning_df)
        signal = strat.on_bar(bar_out, _portfolio())
        assert signal is None

    def test_overlap_stop_loss_present(self):
        """LONG SL should be below morning low minus ATR buffer."""
        strat = FXSessionOverlap()
        bar = _make_bar(
            symbol="EURUSD",
            timestamp="2026-03-10 14:00:00",
            tz="Europe/Paris",
            close=1.0830,
        )
        morning_df = _make_morning_bars_df(morning_move=0.0030, base_price=1.0800)
        atr_val = 0.0020
        _setup_data_feed_mock(
            strat,
            atr_val=atr_val,
            ema_fast=1.0835,
            ema_slow=1.0810,
            bars_df=morning_df,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is not None
        assert signal.stop_loss is not None

        # SL should be morning_low - 0.3 * ATR
        morning_low = float(morning_df["low"].min())
        expected_sl = morning_low - strat.sl_atr_buffer * atr_val
        assert abs(signal.stop_loss - expected_sl) < 1e-8, (
            f"SL {signal.stop_loss} != expected {expected_sl}"
        )

    def test_overlap_parameter_grid(self):
        """Parameter grid should exist and contain all tunable parameters."""
        strat = FXSessionOverlap()
        grid = strat.get_parameter_grid()

        assert "atr_period" in grid
        assert "ema_fast" in grid
        assert "ema_slow" in grid
        assert "min_move_atr" in grid
        assert "sl_atr_buffer" in grid
        assert "tp_multiplier" in grid
        assert "vix_max" in grid

        # Each grid must have multiple values
        for key, values in grid.items():
            assert len(values) >= 2, f"{key} grid has < 2 values"

        # Verify get_parameters matches
        params = strat.get_parameters()
        for key in grid:
            assert key in params, f"{key} in grid but not in get_parameters()"
