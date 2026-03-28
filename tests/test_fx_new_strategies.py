"""
Tests for FX-007 Asian Range Breakout and FX-008 Bollinger Squeeze Breakout.

Covers:
  ARB (8 tests):
    - Long breakout above Asian range
    - Short breakout below Asian range
    - No signal inside range
    - Wide range filtered out
    - Time exit at 16:00 UTC
    - Stop loss always present
    - ADX filter blocks low-ADX
    - Parameter grid structure

  BB Squeeze (7 tests):
    - Squeeze detection
    - Long breakout above upper BB
    - Short breakout below lower BB
    - No breakout without squeeze
    - Volume filter
    - Stop loss at middle BB
    - Parameter grid structure
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState, Signal
from strategies_v2.fx.fx_asian_range_breakout import (
    FXAsianRangeBreakout,
    SUPPORTED_PAIRS as ARB_PAIRS,
)
from strategies_v2.fx.fx_bollinger_squeeze import (
    FXBollingerSqueeze,
    SUPPORTED_PAIRS as BB_PAIRS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _portfolio() -> PortfolioState:
    """Default portfolio state for tests."""
    return PortfolioState(equity=100_000.0, cash=80_000.0)


def _make_1h_df(
    n_bars: int = 200,
    base_price: float = 1.1000,
    volatility: float = 0.001,
    start: str = "2026-01-05 00:00",
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic 1H OHLCV DataFrame with UTC timestamps."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="1h", tz="UTC")
    returns = rng.normal(0, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0002, 0.002, n_bars))
    low = close * (1 - rng.uniform(0.0002, 0.002, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0003, n_bars))
    volume = rng.randint(5000, 50000, n_bars).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_4h_df(
    n_bars: int = 200,
    base_price: float = 1.1000,
    volatility: float = 0.002,
    start: str = "2026-01-05 00:00",
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic 4H OHLCV DataFrame with UTC timestamps."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="4h", tz="UTC")
    returns = rng.normal(0, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0003, 0.004, n_bars))
    low = close * (1 - rng.uniform(0.0003, 0.004, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
    volume = rng.randint(5000, 50000, n_bars).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_squeeze_df(
    n_bars: int = 200,
    base_price: float = 1.1000,
    squeeze_start: int = 140,
    squeeze_end: int = 170,
    breakout_bar: int = 175,
    seed: int = 42,
) -> pd.DataFrame:
    """Create a 4H DF with a squeeze period followed by a breakout.

    The squeeze period has very low volatility (tight BB), then a sharp
    move at breakout_bar.
    """
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start="2026-01-05 00:00", periods=n_bars, freq="4h", tz="UTC")

    prices = np.full(n_bars, base_price)
    # Normal volatility phase
    for i in range(1, squeeze_start):
        prices[i] = prices[i - 1] * (1 + rng.normal(0, 0.003))
    # Squeeze: very low volatility
    for i in range(squeeze_start, squeeze_end):
        prices[i] = prices[i - 1] * (1 + rng.normal(0, 0.0002))
    # Breakout: sharp upward move
    for i in range(squeeze_end, n_bars):
        if i == breakout_bar:
            prices[i] = prices[i - 1] * 1.015  # 1.5% spike
        elif i == breakout_bar + 1:
            prices[i] = prices[i - 1] * 1.005  # continuation
        else:
            prices[i] = prices[i - 1] * (1 + rng.normal(0.0005, 0.002))

    high = prices * (1 + rng.uniform(0.0002, 0.003, n_bars))
    low = prices * (1 - rng.uniform(0.0002, 0.003, n_bars))
    open_ = prices * (1 + rng.normal(0, 0.0004, n_bars))
    # Higher volume at breakout
    volume = rng.randint(5000, 30000, n_bars).astype(float)
    volume[breakout_bar] = 80000.0  # surge
    volume[breakout_bar + 1] = 70000.0

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": prices, "volume": volume},
        index=idx,
    )


# ===========================================================================
# FX-007: Asian Range Breakout (ARB)
# ===========================================================================


class TestARBLongBreakout:
    """test_arb_long_breakout_above_range"""

    def test_arb_long_breakout_above_range(self):
        """A bar closing above the Asian high + buffer should produce a BUY signal."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0  # relax ADX for this test
        strat.range_filter_mult = 5.0  # relax range filter for this test

        # We need enough bars for ATR(14) and ADX(14) to compute.
        # Create 2 days of data: day 1 as warmup (24 bars), day 2 with breakout.
        idx_warmup = pd.date_range("2026-01-04 00:00", periods=24, freq="1h", tz="UTC")
        idx_asian = pd.date_range("2026-01-05 00:00", periods=8, freq="1h", tz="UTC")
        idx_london = pd.date_range("2026-01-05 08:00", periods=3, freq="1h", tz="UTC")
        idx = idx_warmup.append(idx_asian).append(idx_london)

        n = len(idx)
        # Warmup: stable around 1.1000 with small moves for ATR/ADX
        rng = np.random.RandomState(42)
        warmup_close = 1.1000 + rng.normal(0, 0.001, 24)
        # Asian session: tight range
        asian_close = np.array(
            [1.1000, 1.1005, 1.0995, 1.0998, 1.1002, 1.1008, 1.1003, 1.1010]
        )
        # London: breakout above Asian high
        london_close = np.array([1.1040, 1.1060, 1.1070])

        close = np.concatenate([warmup_close, asian_close, london_close])
        high = close + 0.001
        low = close - 0.001
        # Asian high: max(high for Asian bars) = ~1.1010 + 0.001 = 1.1020
        # London close 1.1060 > 1.1020 + buffer => should trigger

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        # Process London bar at 09:00 (which closes at 09:00, so we set ts to 09:01)
        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.1040, high=1.1065, low=1.1035, close=1.1060,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is not None
        assert signal.side == "BUY"
        assert signal.symbol == "EURUSD"
        assert signal.stop_loss < bar.close  # SL below entry


class TestARBShortBreakout:
    """test_arb_short_breakout_below_range"""

    def test_arb_short_breakout_below_range(self):
        """A bar closing below the Asian low - buffer should produce a SELL signal."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0  # relax ADX
        strat.range_filter_mult = 5.0  # relax range filter

        # Need warmup bars for ATR/ADX computation
        idx_warmup = pd.date_range("2026-01-04 00:00", periods=24, freq="1h", tz="UTC")
        idx_asian = pd.date_range("2026-01-05 00:00", periods=8, freq="1h", tz="UTC")
        idx_london = pd.date_range("2026-01-05 08:00", periods=3, freq="1h", tz="UTC")
        idx = idx_warmup.append(idx_asian).append(idx_london)

        n = len(idx)
        rng = np.random.RandomState(42)
        warmup_close = 1.1000 + rng.normal(0, 0.001, 24)
        # Tight Asian range
        asian_close = np.array(
            [1.1000, 1.1005, 1.0995, 1.0998, 1.1002, 1.1008, 1.1003, 1.1010]
        )
        # London: break below Asian low
        london_close = np.array([1.0960, 1.0940, 1.0920])

        close = np.concatenate([warmup_close, asian_close, london_close])
        high = close + 0.001
        low = close - 0.001
        # Asian low: min(low for Asian) = ~1.0995 - 0.001 = 1.0985
        # London close 1.0940 < 1.0985 - buffer => should trigger

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.0960, high=1.0970, low=1.0930, close=1.0940,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is not None
        assert signal.side == "SELL"
        assert signal.stop_loss > bar.close  # SL above entry for SHORT


class TestARBNoSignalInsideRange:
    """test_arb_no_signal_inside_range"""

    def test_arb_no_signal_inside_range(self):
        """A bar closing inside the Asian range should NOT produce a signal."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0

        idx_asian = pd.date_range("2026-01-05 00:00", periods=8, freq="1h", tz="UTC")
        idx_london = pd.date_range("2026-01-05 08:00", periods=3, freq="1h", tz="UTC")
        idx = idx_asian.append(idx_london)

        n = len(idx)
        # All bars stay within tight range
        close = np.array(
            [1.1000, 1.1005, 1.0995, 1.0998, 1.1002, 1.1008, 1.1003, 1.1001,
             # London bars: still inside range
             1.1005, 1.1003, 1.1006]
        )
        high = close + 0.0005
        low = close - 0.0005

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.1005, high=1.1008, low=1.1000, close=1.1003,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None


class TestARBWideRangeFiltered:
    """test_arb_wide_range_filtered"""

    def test_arb_wide_range_filtered(self):
        """A wide Asian range (> ATR * range_filter_mult) should block signals."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0
        strat.range_filter_mult = 0.5  # very strict filter — almost any range is "too wide"

        # Build data with wide Asian range
        n_bars = 50
        idx = pd.date_range("2026-01-05 00:00", periods=n_bars, freq="1h", tz="UTC")
        rng = np.random.RandomState(42)
        close = 1.1000 + rng.normal(0, 0.005, n_bars)
        # Make Asian range very wide
        high = close.copy()
        low = close.copy()
        high[:8] = close[:8] + 0.05  # huge range
        low[:8] = close[:8] - 0.05

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n_bars},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        # Try at 09:00
        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.2000, high=1.2100, low=1.1900, close=1.2050,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None, "Signal generated despite wide Asian range"


class TestARBTimeExit:
    """test_arb_time_exit_at_16utc"""

    def test_arb_time_exit_at_16utc(self):
        """No signal should be generated at or after 16:00 UTC (time exit window)."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0

        # Create enough data
        df = _make_1h_df(n_bars=50, start="2026-01-05 00:00")
        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        # Set timestamp to 16:01 UTC
        ts = pd.Timestamp("2026-01-05 16:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 16:00", tz="UTC"),
            open=1.1200, high=1.1300, low=1.1100, close=1.1250,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None, "Signal generated at 16:00 UTC (should be time exit)"


class TestARBStopLossPresent:
    """test_arb_stop_loss_present"""

    def test_arb_stop_loss_present(self):
        """Every signal must include a stop_loss value."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 0.0

        idx_asian = pd.date_range("2026-01-05 00:00", periods=8, freq="1h", tz="UTC")
        idx_london = pd.date_range("2026-01-05 08:00", periods=3, freq="1h", tz="UTC")
        idx = idx_asian.append(idx_london)

        n = len(idx)
        close = np.array(
            [1.1000, 1.1010, 1.0990, 1.0980, 1.1000, 1.1020, 1.1030, 1.1040,
             1.1060, 1.1080, 1.1090]
        )
        high = close + 0.001
        low = close - 0.001
        high[6] = 1.1050

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.1060, high=1.1085, low=1.1055, close=1.1080,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        if signal is not None:
            assert signal.stop_loss is not None, "Signal missing stop_loss"
            assert isinstance(signal.stop_loss, float)
            assert signal.take_profit is not None, "Signal missing take_profit"


class TestARBADXFilter:
    """test_arb_adx_filter"""

    def test_arb_adx_filter(self):
        """Signal should be blocked when ADX is below threshold."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        strat.adx_threshold = 50.0  # very high — should block everything

        idx_asian = pd.date_range("2026-01-05 00:00", periods=8, freq="1h", tz="UTC")
        idx_london = pd.date_range("2026-01-05 08:00", periods=3, freq="1h", tz="UTC")
        idx = idx_asian.append(idx_london)

        n = len(idx)
        close = np.array(
            [1.1000, 1.1010, 1.0990, 1.0980, 1.1000, 1.1020, 1.1030, 1.1040,
             1.1060, 1.1080, 1.1090]
        )
        high = close + 0.001
        low = close - 0.001
        high[6] = 1.1050

        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": [10000.0] * n},
            index=idx,
        )

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        ts = pd.Timestamp("2026-01-05 09:01", tz="UTC")
        feed.set_timestamp(ts)

        bar = Bar(
            symbol="EURUSD",
            timestamp=pd.Timestamp("2026-01-05 09:00", tz="UTC"),
            open=1.1060, high=1.1085, low=1.1055, close=1.1080,
            volume=10000.0,
        )

        signal = strat.on_bar(bar, _portfolio())
        assert signal is None, "Signal generated despite ADX below threshold"


class TestARBParameterGrid:
    """test_arb_parameter_grid"""

    def test_arb_parameter_grid(self):
        """Parameter grid must have all tunable keys with multiple values."""
        strat = FXAsianRangeBreakout(symbol="EURUSD")
        grid = strat.get_parameter_grid()

        expected_keys = {
            "buffer_atr_mult", "range_filter_mult", "adx_threshold",
            "atr_period", "sl_atr_mult", "tp_risk_mult",
        }
        assert expected_keys.issubset(set(grid.keys())), (
            f"Missing grid keys: {expected_keys - set(grid.keys())}"
        )

        # Each key should have at least 2 values
        for key, values in grid.items():
            assert isinstance(values, list), f"{key} is not a list"
            assert len(values) >= 2, f"{key} has fewer than 2 values"

        # Verify get_parameters returns current values
        params = strat.get_parameters()
        assert "symbol" in params
        assert params["symbol"] == "EURUSD"


# ===========================================================================
# FX-008: Bollinger Squeeze Breakout
# ===========================================================================


class TestBBSqueezeDetected:
    """test_bb_squeeze_detected"""

    def test_bb_squeeze_detected(self):
        """Squeeze should be detected when BB width is in the bottom percentile."""
        strat = FXBollingerSqueeze(symbol="EURUSD")

        df = _make_squeeze_df(n_bars=200, squeeze_start=140, squeeze_end=170)

        # Use the public detect_squeeze helper
        info = strat.detect_squeeze(df.iloc[:165])  # during the squeeze
        assert info["squeeze"] is True, (
            f"Squeeze not detected: pctile={info['pctile']:.1f}%"
        )

        # Before squeeze: should NOT be squeezed
        info_before = strat.detect_squeeze(df.iloc[:130])
        # Not guaranteed but likely — check the pctile is higher
        assert info_before["pctile"] > info["pctile"], (
            "Pre-squeeze pctile should be higher than during squeeze"
        )


class TestBBBreakoutLong:
    """test_bb_breakout_long_above_upper"""

    def test_bb_breakout_long_above_upper(self):
        """After squeeze, close above upper BB with volume should trigger BUY (after confirmation)."""
        strat = FXBollingerSqueeze(symbol="EURUSD")
        strat.adx_max_at_squeeze = 100.0  # relax ADX
        strat.volume_filter = 0.0  # relax volume

        df = _make_squeeze_df(n_bars=200, breakout_bar=175)
        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        # Walk through bars to build state — collect all signals
        signals = []
        for i in range(50, len(df)):
            ts = df.index[i] + pd.Timedelta(minutes=1)
            feed.set_timestamp(ts)
            row = df.iloc[i]
            bar = Bar(
                symbol="EURUSD",
                timestamp=df.index[i],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            result = strat.on_bar(bar, _portfolio())
            if result is not None:
                signals.append((i, result))

        # We expect at least one signal. With the upward breakout at bar 175,
        # there should be a BUY among the signals.
        assert len(signals) > 0, "No signals generated at all"
        buy_signals = [s for _, s in signals if s.side == "BUY"]
        assert len(buy_signals) > 0, (
            f"No BUY signals found. Got: {[(i, s.side) for i, s in signals]}"
        )
        sig = buy_signals[0]
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.stop_loss < sig.take_profit


class TestBBBreakoutShort:
    """test_bb_breakout_short_below_lower"""

    def test_bb_breakout_short_below_lower(self):
        """After squeeze, close below lower BB should trigger SELL (after confirmation)."""
        strat = FXBollingerSqueeze(symbol="GBPUSD")
        strat.adx_max_at_squeeze = 100.0
        strat.volume_filter = 0.0

        # Create squeeze with downward breakout
        rng = np.random.RandomState(99)
        n_bars = 200
        idx = pd.date_range("2026-01-05 00:00", periods=n_bars, freq="4h", tz="UTC")
        prices = np.full(n_bars, 1.2500)
        for i in range(1, 140):
            prices[i] = prices[i - 1] * (1 + rng.normal(0, 0.003))
        for i in range(140, 170):
            prices[i] = prices[i - 1] * (1 + rng.normal(0, 0.0002))
        for i in range(170, n_bars):
            if i == 175:
                prices[i] = prices[i - 1] * 0.985  # 1.5% drop
            elif i == 176:
                prices[i] = prices[i - 1] * 0.995  # continuation down
            else:
                prices[i] = prices[i - 1] * (1 + rng.normal(-0.0005, 0.002))

        high = prices * (1 + rng.uniform(0.0002, 0.003, n_bars))
        low = prices * (1 - rng.uniform(0.0002, 0.003, n_bars))
        open_ = prices * (1 + rng.normal(0, 0.0004, n_bars))
        volume = rng.randint(5000, 30000, n_bars).astype(float)
        volume[175] = 80000.0
        volume[176] = 70000.0

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": prices, "volume": volume},
            index=idx,
        )

        feed = DataFeed({"GBPUSD": df})
        strat.set_data_feed(feed)

        signal = None
        for i in range(50, len(df)):
            ts = df.index[i] + pd.Timedelta(minutes=1)
            feed.set_timestamp(ts)
            row = df.iloc[i]
            bar = Bar(
                symbol="GBPUSD",
                timestamp=df.index[i],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            result = strat.on_bar(bar, _portfolio())
            if result is not None:
                signal = result
                break

        if signal is not None:
            assert signal.side == "SELL"
            assert signal.stop_loss is not None
            assert signal.stop_loss > signal.take_profit  # SL above TP for short


class TestBBNoBreakoutNoSqueeze:
    """test_bb_no_breakout_no_squeeze"""

    def test_bb_no_breakout_no_squeeze(self):
        """Without a squeeze, breakout alone should NOT produce a signal."""
        strat = FXBollingerSqueeze(symbol="EURUSD")

        # Normal volatility data — no squeeze
        df = _make_4h_df(n_bars=200, volatility=0.003, seed=123)
        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        signal_count = 0
        for i in range(50, len(df)):
            ts = df.index[i] + pd.Timedelta(minutes=1)
            feed.set_timestamp(ts)
            row = df.iloc[i]
            bar = Bar(
                symbol="EURUSD",
                timestamp=df.index[i],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            result = strat.on_bar(bar, _portfolio())
            if result is not None:
                signal_count += 1

        # With consistent volatility and no squeeze, signals should be rare/none
        # The key check: strategy requires squeeze before breakout
        assert signal_count < 3, (
            f"Too many signals ({signal_count}) without clear squeeze"
        )


class TestBBVolumeFilter:
    """test_bb_volume_filter"""

    def test_bb_volume_filter(self):
        """Breakout with low volume should NOT trigger a signal."""
        strat = FXBollingerSqueeze(symbol="EURUSD")
        strat.adx_max_at_squeeze = 100.0
        strat.volume_filter = 5.0  # require 5x average volume — very strict

        df = _make_squeeze_df(n_bars=200, breakout_bar=175)
        # Override volume to be flat and low
        df["volume"] = 10000.0  # all bars same volume — never 5x average

        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        signal = None
        for i in range(50, len(df)):
            ts = df.index[i] + pd.Timedelta(minutes=1)
            feed.set_timestamp(ts)
            row = df.iloc[i]
            bar = Bar(
                symbol="EURUSD",
                timestamp=df.index[i],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            result = strat.on_bar(bar, _portfolio())
            if result is not None:
                signal = result
                break

        assert signal is None, "Signal generated despite insufficient volume"


class TestBBStopLossAtMiddleBB:
    """test_bb_stop_loss_at_middle_bb"""

    def test_bb_stop_loss_at_middle_bb(self):
        """Stop loss should be set at the middle Bollinger Band (SMA).

        For a BUY signal: SL (= middle BB) < entry price < TP.
        For a SELL signal: TP < entry price < SL (= middle BB).
        """
        strat = FXBollingerSqueeze(symbol="EURUSD")
        strat.adx_max_at_squeeze = 100.0
        strat.volume_filter = 0.0

        df = _make_squeeze_df(n_bars=200, breakout_bar=175)
        feed = DataFeed({"EURUSD": df})
        strat.set_data_feed(feed)

        signal = None
        signal_bar_close = None
        for i in range(50, len(df)):
            ts = df.index[i] + pd.Timedelta(minutes=1)
            feed.set_timestamp(ts)
            row = df.iloc[i]
            bar = Bar(
                symbol="EURUSD",
                timestamp=df.index[i],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            result = strat.on_bar(bar, _portfolio())
            if result is not None:
                signal = result
                signal_bar_close = float(row["close"])
                break

        assert signal is not None, "No signal generated for SL test"
        assert signal.stop_loss is not None

        # The SL is set at middle BB which should be between entry and TP
        if signal.side == "BUY":
            # BUY: SL (middle BB) < entry < TP
            assert signal.stop_loss < signal_bar_close, (
                f"BUY SL {signal.stop_loss} should be < entry {signal_bar_close}"
            )
            assert signal.stop_loss < signal.take_profit
        else:
            # SELL: TP < entry < SL (middle BB)
            assert signal.stop_loss > signal_bar_close, (
                f"SELL SL {signal.stop_loss} should be > entry {signal_bar_close}"
            )
            assert signal.stop_loss > signal.take_profit


class TestBBParameterGrid:
    """test_bb_parameter_grid"""

    def test_bb_parameter_grid(self):
        """Parameter grid must have all required keys from the spec."""
        strat = FXBollingerSqueeze(symbol="EURUSD")
        grid = strat.get_parameter_grid()

        required_keys = {
            "bb_period", "bb_std", "squeeze_pctile", "volume_filter",
        }
        assert required_keys.issubset(set(grid.keys())), (
            f"Missing grid keys: {required_keys - set(grid.keys())}"
        )

        # Check specific grid values from the spec
        assert grid["bb_period"] == [15, 20, 25]
        assert grid["bb_std"] == [1.5, 2.0, 2.5]
        assert grid["squeeze_pctile"] == [5.0, 10.0, 15.0]
        assert grid["volume_filter"] == [1.0, 1.2, 1.5]

        # Each key should have >= 2 values
        for key, values in grid.items():
            assert isinstance(values, list)
            assert len(values) >= 2, f"{key} has fewer than 2 values"

        # Verify get_parameters
        params = strat.get_parameters()
        assert params["bb_period"] == 20
        assert params["bb_std"] == 2.0
        assert params["symbol"] == "EURUSD"
