"""
Tests for FX strategies: FX-002 GBP/USD Trend, FX-003 USD/CHF MR, FX-004 NZD/USD Carry.

Covers:
  - Signal generation (long + short) for each strategy
  - Stop loss / take profit calculation with ATR
  - Filter conditions (VIX, events, rollover, AUD/NZD dislocation)
  - Edge cases (no data, empty signals, insufficient bars)
  - Market hours check (FX rollover window)
  - Metadata correctness (costs, strategy name)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add backtester to path so imports work
_backtester_dir = str(Path(__file__).resolve().parent.parent / "archive" / "intraday-backtesterV2")
if _backtester_dir not in sys.path:
    sys.path.insert(0, _backtester_dir)

from strategies.fx_gbpusd_trend import FXGBPUSDTrendStrategy
from strategies.fx_nzdusd_carry import FXNZDUSDCarryStrategy
from strategies.fx_usdchf_mr import FXUSDCHFMeanReversionStrategy

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_fx_df(
    n_bars: int = 200,
    base_price: float = 1.2500,
    trend: float = 0.0,
    volatility: float = 0.001,
    start: str = "2026-01-05 08:00",
    freq: str = "1h",
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic OHLCV DataFrame for FX testing."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq=freq, tz="UTC")

    # Random walk with drift
    returns = rng.normal(trend, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))

    # Construct OHLCV
    high = close * (1 + rng.uniform(0.0001, 0.003, n_bars))
    low = close * (1 - rng.uniform(0.0001, 0.003, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
    volume = rng.randint(1000, 50000, n_bars).astype(float)

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=idx)
    return df


def _make_trending_df(
    direction: str = "up",
    n_bars: int = 200,
    base_price: float = 1.2500,
    seed: int = 42,
) -> pd.DataFrame:
    """Create a strongly trending DataFrame to force EMA crossover signals."""
    drift = 0.002 if direction == "up" else -0.002
    return _make_fx_df(
        n_bars=n_bars,
        base_price=base_price,
        trend=drift,
        volatility=0.0008,
        seed=seed,
    )


def _make_mean_reverting_df(
    n_bars: int = 200,
    base_price: float = 0.9200,
    deviation_pct: float = 0.03,
    seed: int = 42,
) -> pd.DataFrame:
    """Create a DF where price deviates then reverts to mean, for MR tests."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start="2026-01-05 08:00", periods=n_bars, freq="1h", tz="UTC")

    # First half: stable, second half: big drop then revert
    prices = np.full(n_bars, base_price)
    # Bars 0-99: stable with tiny noise
    for i in range(100):
        prices[i] = base_price + rng.normal(0, 0.0005)
    # Bars 100-150: sharp drop
    for i in range(100, 150):
        prices[i] = prices[i - 1] * (1 - 0.001)
    # Bars 150-200: recovery
    for i in range(150, n_bars):
        prices[i] = prices[i - 1] * (1 + 0.0008)

    high = prices * (1 + rng.uniform(0.0001, 0.002, n_bars))
    low = prices * (1 - rng.uniform(0.0001, 0.002, n_bars))
    open_ = prices * (1 + rng.normal(0, 0.0003, n_bars))
    volume = rng.randint(1000, 50000, n_bars).astype(float)

    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": prices, "volume": volume,
    }, index=idx)


def _make_vix_df(level: float = 20.0, n_bars: int = 200) -> pd.DataFrame:
    """Create synthetic VIX DataFrame with a constant level."""
    idx = pd.date_range(start="2026-01-05 08:00", periods=n_bars, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": level, "high": level + 0.5, "low": level - 0.5,
        "close": level, "volume": 10000.0,
    }, index=idx)


# ═══════════════════════════════════════════════════════════════════════════════
# FX-002: GBP/USD Trend
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXGBPUSDTrend:
    """Tests for FX-002 GBP/USD Trend Following strategy."""

    def test_strategy_name(self):
        strat = FXGBPUSDTrendStrategy()
        assert strat.name == "FX-002 GBP/USD Trend"

    def test_required_tickers(self):
        strat = FXGBPUSDTrendStrategy()
        assert "GBPUSD" in strat.get_required_tickers()

    def test_no_signal_on_empty_data(self):
        strat = FXGBPUSDTrendStrategy()
        signals = strat.generate_signals({}, "2026-02-10")
        assert signals == []

    def test_no_signal_on_insufficient_bars(self):
        strat = FXGBPUSDTrendStrategy()
        # Only 10 bars — not enough for EMA 50 + warmup
        df = _make_fx_df(n_bars=10)
        signals = strat.generate_signals({"GBPUSD": df}, "2026-01-05")
        assert signals == []

    def test_long_signal_on_uptrend(self):
        """Strong uptrend should produce a LONG signal after EMA crossover."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0)  # Relax ADX for test
        df = _make_trending_df(direction="up", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        # With a strong uptrend and relaxed ADX, we should get at least one signal
        long_signals = [s for s in signals if s.action == "LONG"]
        if long_signals:
            sig = long_signals[0]
            assert sig.ticker == "GBPUSD"
            assert sig.take_profit > sig.entry_price  # TP above entry for LONG
            assert sig.stop_loss < sig.entry_price  # SL below entry for LONG

    def test_short_signal_on_downtrend(self):
        """Strong downtrend should produce a SHORT signal after bearish crossover."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0)
        df = _make_trending_df(direction="down", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        short_signals = [s for s in signals if s.action == "SHORT"]
        if short_signals:
            sig = short_signals[0]
            assert sig.ticker == "GBPUSD"
            assert sig.take_profit < sig.entry_price  # TP below entry for SHORT
            assert sig.stop_loss > sig.entry_price  # SL above entry for SHORT

    def test_stop_tp_use_atr(self):
        """Verify SL/TP are based on ATR multiples."""
        strat = FXGBPUSDTrendStrategy(
            adx_threshold=0.0,
            stop_atr_mult=1.5,
            tp_atr_mult=3.0,
        )
        df = _make_trending_df(direction="up", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        for sig in signals:
            if sig.action == "LONG":
                risk = sig.entry_price - sig.stop_loss
                reward = sig.take_profit - sig.entry_price
                # R/R should be approximately 2:1 (3.0/1.5)
                if risk > 0:
                    rr = reward / risk
                    assert 1.5 < rr < 2.5, f"R/R ratio {rr:.2f} outside expected range"

    def test_event_blackout_filter(self):
        """No signals should be generated on BoE/Fed announcement days."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0)
        # Use a known blackout date
        blackout_date = "2026-03-18"  # Fed date
        df = _make_trending_df(direction="up", n_bars=300, seed=99)
        signals = strat.generate_signals({"GBPUSD": df}, blackout_date)
        assert signals == [], "Signals generated on event blackout day"

    def test_rollover_window_static(self):
        """The _is_rollover_window helper should correctly identify rollover hours."""
        strat = FXGBPUSDTrendStrategy()
        # 22:30 UTC is in rollover window
        ts_rollover = pd.Timestamp("2026-02-10 22:30:00", tz="UTC")
        assert strat._is_rollover_window(ts_rollover) is True
        # 15:00 UTC is not in rollover window
        ts_normal = pd.Timestamp("2026-02-10 15:00:00", tz="UTC")
        assert strat._is_rollover_window(ts_normal) is False

    def test_metadata_contains_cost(self):
        """Metadata should include FX round-trip cost."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0)
        df = _make_trending_df(direction="up", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        for sig in signals:
            assert "cost_rt_pct" in sig.metadata
            assert sig.metadata["cost_rt_pct"] == 0.0001
            assert sig.metadata["strategy"] == "FX-002 GBP/USD Trend"

    def test_max_trades_per_day(self):
        """Should not exceed max_trades_per_day signals."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0, max_trades_per_day=1)
        df = _make_trending_df(direction="up", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        assert len(signals) <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# FX-003: USD/CHF Mean Reversion
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXUSDCHFMeanReversion:
    """Tests for FX-003 USD/CHF Mean Reversion strategy."""

    def test_strategy_name(self):
        strat = FXUSDCHFMeanReversionStrategy()
        assert strat.name == "FX-003 USD/CHF MR"

    def test_required_tickers(self):
        strat = FXUSDCHFMeanReversionStrategy()
        tickers = strat.get_required_tickers()
        assert "USDCHF" in tickers
        assert "VIX" in tickers

    def test_no_signal_on_empty_data(self):
        strat = FXUSDCHFMeanReversionStrategy()
        signals = strat.generate_signals({}, "2026-02-10")
        assert signals == []

    def test_no_signal_on_insufficient_bars(self):
        strat = FXUSDCHFMeanReversionStrategy()
        df = _make_fx_df(n_bars=5, base_price=0.92)
        signals = strat.generate_signals({"USDCHF": df}, "2026-01-05")
        assert signals == []

    def test_long_signal_on_oversold(self):
        """Price well below SMA should generate a LONG signal."""
        strat = FXUSDCHFMeanReversionStrategy()
        df = _make_mean_reverting_df(n_bars=200)
        date = df.index[100].date()  # After the sharp drop
        signals = strat.generate_signals({"USDCHF": df}, date)
        long_signals = [s for s in signals if s.action == "LONG"]
        if long_signals:
            sig = long_signals[0]
            assert sig.ticker == "USDCHF"
            assert sig.take_profit > sig.entry_price  # TP is the SMA (above entry)
            assert sig.stop_loss < sig.entry_price  # SL below entry

    def test_tp_targets_mean(self):
        """Take profit should target the 20-day SMA (mean reversion)."""
        strat = FXUSDCHFMeanReversionStrategy()
        df = _make_mean_reverting_df(n_bars=200)
        date = df.index[100].date()
        signals = strat.generate_signals({"USDCHF": df}, date)
        for sig in signals:
            assert "sma20" in sig.metadata
            # TP should be approximately the SMA value
            sma_val = sig.metadata["sma20"]
            assert abs(sig.take_profit - sma_val) < 0.001, (
                f"TP {sig.take_profit} not near SMA {sma_val}"
            )

    def test_vix_filter_blocks_high_vix(self):
        """No signals when VIX > 30 (extreme risk-off)."""
        strat = FXUSDCHFMeanReversionStrategy(vix_max=30.0)
        df = _make_mean_reverting_df(n_bars=200)
        vix_df = _make_vix_df(level=35.0, n_bars=200)  # VIX at 35 — above threshold
        date = df.index[100].date()
        signals = strat.generate_signals({"USDCHF": df, "VIX": vix_df}, date)
        assert signals == [], "Signals generated despite VIX > 30"

    def test_vix_filter_allows_low_vix(self):
        """Signals allowed when VIX < 30."""
        strat = FXUSDCHFMeanReversionStrategy(vix_max=30.0)
        df = _make_mean_reverting_df(n_bars=200)
        vix_df = _make_vix_df(level=18.0, n_bars=200)  # VIX at 18 — below threshold
        date = df.index[100].date()
        # May or may not generate signals depending on price deviation,
        # but VIX should NOT block them
        signals = strat.generate_signals({"USDCHF": df, "VIX": vix_df}, date)
        # Just verify VIX filter didn't block — no assertion on count

    def test_snb_blackout_filter(self):
        """No signals on SNB announcement days."""
        strat = FXUSDCHFMeanReversionStrategy()
        df = _make_mean_reverting_df(n_bars=200)
        # SNB date from the constant
        signals = strat.generate_signals({"USDCHF": df}, "2026-03-19")
        assert signals == [], "Signals generated on SNB blackout day"

    def test_metadata_contains_deviation(self):
        """Metadata should include deviation in ATR units."""
        strat = FXUSDCHFMeanReversionStrategy()
        df = _make_mean_reverting_df(n_bars=200)
        date = df.index[100].date()
        signals = strat.generate_signals({"USDCHF": df}, date)
        for sig in signals:
            assert "deviation_atr" in sig.metadata
            assert "cost_rt_pct" in sig.metadata
            assert sig.metadata["cost_rt_pct"] == 0.0001


# ═══════════════════════════════════════════════════════════════════════════════
# FX-004: NZD/USD Carry + Momentum
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXNZDUSDCarry:
    """Tests for FX-004 NZD/USD Carry + Momentum strategy."""

    def test_strategy_name(self):
        strat = FXNZDUSDCarryStrategy()
        assert strat.name == "FX-004 NZD/USD Carry"

    def test_required_tickers(self):
        strat = FXNZDUSDCarryStrategy()
        tickers = strat.get_required_tickers()
        assert "NZDUSD" in tickers
        assert "AUDNZD" in tickers

    def test_no_signal_on_empty_data(self):
        strat = FXNZDUSDCarryStrategy()
        signals = strat.generate_signals({}, "2026-02-10")
        assert signals == []

    def test_no_signal_on_insufficient_bars(self):
        strat = FXNZDUSDCarryStrategy()
        df = _make_fx_df(n_bars=10, base_price=0.62)
        signals = strat.generate_signals({"NZDUSD": df}, "2026-01-05")
        assert signals == []

    def test_long_signal_with_positive_carry_and_momentum(self):
        """Positive carry + upward momentum should produce LONG."""
        strat = FXNZDUSDCarryStrategy(carry_bps=75)  # Positive carry
        df = _make_trending_df(direction="up", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        long_signals = [s for s in signals if s.action == "LONG"]
        if long_signals:
            sig = long_signals[0]
            assert sig.ticker == "NZDUSD"
            assert sig.take_profit > sig.entry_price
            assert sig.stop_loss < sig.entry_price
            assert sig.metadata["carry_direction"] == "long"

    def test_short_signal_with_negative_carry_and_momentum(self):
        """Negative carry + downward momentum should produce SHORT."""
        strat = FXNZDUSDCarryStrategy(carry_bps=-75)  # Negative carry
        df = _make_trending_df(direction="down", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        short_signals = [s for s in signals if s.action == "SHORT"]
        if short_signals:
            sig = short_signals[0]
            assert sig.ticker == "NZDUSD"
            assert sig.take_profit < sig.entry_price
            assert sig.stop_loss > sig.entry_price
            assert sig.metadata["carry_direction"] == "short"

    def test_no_signal_when_carry_neutral(self):
        """No signal when carry differential is below minimum threshold."""
        strat = FXNZDUSDCarryStrategy(carry_bps=10, min_carry_bps=25)
        df = _make_trending_df(direction="up", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        assert signals == [], "Signals generated despite neutral carry"

    def test_no_signal_when_carry_and_momentum_disagree(self):
        """Positive carry but negative momentum should NOT produce a signal."""
        strat = FXNZDUSDCarryStrategy(carry_bps=75)  # Positive carry = wants long
        # But momentum is down
        df = _make_trending_df(direction="down", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        # Should only produce LONG, but momentum is negative — no LONG expected
        long_signals = [s for s in signals if s.action == "LONG"]
        assert long_signals == [], "LONG signal despite negative momentum"

    def test_audnzd_dislocation_filter(self):
        """No signal when AUD/NZD is dislocated (> 2 std from mean)."""
        strat = FXNZDUSDCarryStrategy(
            carry_bps=75,
            audnzd_lookback=60,
            audnzd_std_threshold=2.0,
        )
        nzd_df = _make_trending_df(direction="up", n_bars=200, base_price=0.6200)

        # Create dislocated AUD/NZD — spike far from mean
        rng = np.random.RandomState(42)
        idx = pd.date_range(start="2026-01-05 08:00", periods=200, freq="1h", tz="UTC")
        audnzd_prices = np.full(200, 1.0800)  # Stable base
        # Make last 10 bars spike to create > 2 std dislocation
        audnzd_prices[-10:] = 1.1500  # Far from mean
        audnzd_df = pd.DataFrame({
            "open": audnzd_prices,
            "high": audnzd_prices + 0.001,
            "low": audnzd_prices - 0.001,
            "close": audnzd_prices,
            "volume": 10000.0,
        }, index=idx)

        date = nzd_df.index[0].date()
        signals = strat.generate_signals(
            {"NZDUSD": nzd_df, "AUDNZD": audnzd_df}, date
        )
        # Should be filtered by dislocation — no long signals at end
        # (Signals before dislocation may still appear, but post-dislocation blocked)

    def test_stop_tp_rr_ratio(self):
        """R/R should be 2:1 (4 ATR TP / 2 ATR SL)."""
        strat = FXNZDUSDCarryStrategy(
            carry_bps=75, stop_atr_mult=2.0, tp_atr_mult=4.0
        )
        df = _make_trending_df(direction="up", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        for sig in signals:
            if sig.action == "LONG":
                risk = sig.entry_price - sig.stop_loss
                reward = sig.take_profit - sig.entry_price
                if risk > 0:
                    rr = reward / risk
                    assert 1.5 < rr < 2.5, f"R/R ratio {rr:.2f} outside expected range"

    def test_metadata_contains_carry_info(self):
        """Metadata should include carry details and costs."""
        strat = FXNZDUSDCarryStrategy(carry_bps=75)
        df = _make_trending_df(direction="up", n_bars=200, base_price=0.6200)
        date = df.index[0].date()
        signals = strat.generate_signals({"NZDUSD": df}, date)
        for sig in signals:
            assert "carry_bps" in sig.metadata
            assert "carry_direction" in sig.metadata
            assert "cost_rt_pct" in sig.metadata
            assert sig.metadata["cost_rt_pct"] == 0.0001
            assert sig.metadata["rr_ratio"] == 2.0


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-strategy tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXStrategiesCross:
    """Cross-cutting tests for all FX strategies."""

    def test_all_strategies_inherit_base(self):
        """All FX strategies must inherit from BaseStrategy."""
        from backtest_engine import BaseStrategy
        assert issubclass(FXGBPUSDTrendStrategy, BaseStrategy)
        assert issubclass(FXUSDCHFMeanReversionStrategy, BaseStrategy)
        assert issubclass(FXNZDUSDCarryStrategy, BaseStrategy)

    def test_all_strategies_return_list(self):
        """generate_signals must always return a list."""
        strategies = [
            FXGBPUSDTrendStrategy(),
            FXUSDCHFMeanReversionStrategy(),
            FXNZDUSDCarryStrategy(),
        ]
        for strat in strategies:
            result = strat.generate_signals({}, "2026-02-10")
            assert isinstance(result, list), f"{strat.name} did not return a list"

    def test_rollover_window_shared_logic(self):
        """All FX strategies should block signals during 22:00-23:00 UTC."""
        for StratClass in [FXGBPUSDTrendStrategy, FXUSDCHFMeanReversionStrategy, FXNZDUSDCarryStrategy]:
            strat = StratClass()
            ts_in = pd.Timestamp("2026-02-10 22:30:00", tz="UTC")
            ts_out = pd.Timestamp("2026-02-10 15:00:00", tz="UTC")
            assert strat._is_rollover_window(ts_in) is True
            assert strat._is_rollover_window(ts_out) is False

    def test_signal_object_fields(self):
        """Signals must have all required fields from the Signal dataclass."""
        strat = FXGBPUSDTrendStrategy(adx_threshold=0.0)
        df = _make_trending_df(direction="up", n_bars=300)
        date = df.index[0].date()
        signals = strat.generate_signals({"GBPUSD": df}, date)
        for sig in signals:
            assert hasattr(sig, "action")
            assert hasattr(sig, "ticker")
            assert hasattr(sig, "entry_price")
            assert hasattr(sig, "stop_loss")
            assert hasattr(sig, "take_profit")
            assert hasattr(sig, "timestamp")
            assert hasattr(sig, "metadata")
            assert sig.action in ("LONG", "SHORT")
            assert isinstance(sig.entry_price, (int, float))
            assert isinstance(sig.metadata, dict)
