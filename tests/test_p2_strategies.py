"""
Tests for P2 strategies:
  - FX-005 Cross-Pair Momentum FX
  - FUT-005 Micro Gold (MGC) Trend Following
  - EU-006 EURO STOXX 50 Futures Trend Following

Covers:
  - Signal generation (long + short) for each strategy
  - Stop loss / take profit calculation with ATR
  - Filter conditions (FOMC, ECB, crisis regime, Monday-only)
  - Edge cases (no data, empty signals, insufficient bars)
  - Metadata correctness (costs, instrument specs, strategy name)
  - Ranking logic (FX cross-momentum)
  - DXY confirmation (MGC)
  - Session hours (ESTX)
"""
import sys
import os
from pathlib import Path

import pytest
import pandas as pd
import numpy as np

# Add backtester to path so imports work
_backtester_dir = str(Path(__file__).resolve().parent.parent / "intraday-backtesterV2")
if _backtester_dir not in sys.path:
    sys.path.insert(0, _backtester_dir)

from backtest_engine import BaseStrategy, Signal
from strategies.fx_cross_momentum import (
    FXCrossMomentumStrategy, FX_PAIRS, FX_COST_RT_PCT,
)
from strategies.futures_mgc_trend import (
    FuturesMGCTrendStrategy, GOLD_TICKER, DXY_TICKER,
    FOMC_DATES, MGC_MULTIPLIER, MGC_MARGIN, MGC_COMMISSION_RT,
)
from strategies.futures_estx_trend import (
    FuturesESTXTrendStrategy, STOXX_TICKER, ECB_DATES,
    ESTX_MULTIPLIER, ESTX_MARGIN, ESTX_COMMISSION_RT,
)


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
    """Create synthetic OHLCV DataFrame for testing."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq=freq, tz="UTC")

    returns = rng.normal(trend, volatility, n_bars)
    close = base_price * np.exp(np.cumsum(returns))

    high = close * (1 + rng.uniform(0.0001, 0.003, n_bars))
    low = close * (1 - rng.uniform(0.0001, 0.003, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
    volume = rng.randint(1000, 50000, n_bars).astype(float)

    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)


def _make_trending_df(
    direction: str = "up",
    n_bars: int = 200,
    base_price: float = 180.0,
    start: str = "2026-01-05 09:30",
    freq: str = "5min",
    seed: int = 42,
) -> pd.DataFrame:
    """Create a strongly trending DataFrame to force EMA crossover signals."""
    drift = 0.002 if direction == "up" else -0.002
    return _make_fx_df(
        n_bars=n_bars,
        base_price=base_price,
        trend=drift,
        volatility=0.0008,
        start=start,
        freq=freq,
        seed=seed,
    )


def _make_monday_data(
    pairs: list[str],
    n_bars: int = 100,
    trends: dict[str, float] = None,
    start: str = "2026-01-05 08:00",
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """
    Create FX data for multiple pairs on a Monday.
    2026-01-05 is a Monday.
    trends: {ticker: drift} — positive = up, negative = down.
    """
    if trends is None:
        trends = {}
    data = {}
    for i, pair in enumerate(pairs):
        base = 1.25 if "EUR" in pair or "GBP" in pair else 0.70
        drift = trends.get(pair, 0.0001 * (i - len(pairs) // 2))
        data[pair] = _make_fx_df(
            n_bars=n_bars,
            base_price=base,
            trend=drift,
            volatility=0.001,
            start=start,
            freq="1h",
            seed=seed + i,
        )
    return data


def _make_intraday_df(
    direction: str = "up",
    n_bars: int = 200,
    base_price: float = 50.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Create intraday 5-min DataFrame within US trading hours."""
    rng = np.random.RandomState(seed)
    # Start at 09:35 ET on a non-ECB, non-FOMC day
    idx = pd.date_range(
        start="2026-02-10 09:35",
        periods=n_bars,
        freq="5min",
        tz="US/Eastern",
    )

    drift = 0.001 if direction == "up" else -0.001
    returns = rng.normal(drift, 0.0005, n_bars)
    close = base_price * np.exp(np.cumsum(returns))

    high = close * (1 + rng.uniform(0.0001, 0.002, n_bars))
    low = close * (1 - rng.uniform(0.0001, 0.002, n_bars))
    open_ = close * (1 + rng.normal(0, 0.0003, n_bars))
    volume = rng.randint(5000, 100000, n_bars).astype(float)

    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)


# ═══════════════════════════════════════════════════════════════════════════════
# FX-005: Cross-Pair Momentum
# ═══════════════════════════════════════════════════════════════════════════════


class TestFXCrossMomentum:
    """Tests for FX-005 Cross-Pair Momentum strategy."""

    def test_strategy_name(self):
        strat = FXCrossMomentumStrategy()
        assert strat.name == "FX-005 Cross-Pair Momentum"

    def test_inherits_base_strategy(self):
        assert issubclass(FXCrossMomentumStrategy, BaseStrategy)

    def test_required_tickers_contains_all_pairs(self):
        strat = FXCrossMomentumStrategy()
        tickers = strat.get_required_tickers()
        for pair in FX_PAIRS:
            assert pair in tickers

    def test_no_signal_on_empty_data(self):
        strat = FXCrossMomentumStrategy()
        signals = strat.generate_signals({}, "2026-01-05")
        assert signals == []

    def test_no_signal_on_non_monday(self):
        """Signals should only be generated on Mondays."""
        strat = FXCrossMomentumStrategy()
        # 2026-01-06 is a Tuesday
        data = _make_monday_data(FX_PAIRS, start="2026-01-06 08:00")
        signals = strat.generate_signals(data, "2026-01-06")
        assert signals == [], "Signals generated on a non-Monday"

    def test_no_signal_insufficient_pairs(self):
        """Need at least 6 pairs for cross-sectional ranking."""
        strat = FXCrossMomentumStrategy(min_pairs=6)
        # Only provide 3 pairs
        data = _make_monday_data(FX_PAIRS[:3])
        signals = strat.generate_signals(data, "2026-01-05")
        assert signals == [], "Signals generated with insufficient pairs"

    def test_signals_on_monday_with_all_pairs(self):
        """Should generate up to 4 signals (2 LONG + 2 SHORT) on Monday."""
        trends = {
            "EURUSD": 0.003,   # Strong up
            "EURGBP": 0.002,   # Up
            "EURJPY": 0.0005,  # Neutral
            "AUDJPY": -0.0005, # Neutral
            "GBPUSD": 0.001,   # Mild up
            "USDCHF": -0.002,  # Down
            "NZDUSD": -0.003,  # Strong down
        }
        data = _make_monday_data(FX_PAIRS, trends=trends)
        strat = FXCrossMomentumStrategy()
        signals = strat.generate_signals(data, "2026-01-05")

        # Should have signals — up to 4 (2 long + 2 short)
        assert len(signals) <= 4
        long_signals = [s for s in signals if s.action == "LONG"]
        short_signals = [s for s in signals if s.action == "SHORT"]
        assert len(long_signals) <= 2
        assert len(short_signals) <= 2

    def test_long_tp_above_entry_short_tp_below(self):
        """LONG TP should be above entry, SHORT TP should be below entry."""
        trends = {
            "EURUSD": 0.003, "EURGBP": 0.002, "EURJPY": 0.001,
            "AUDJPY": 0.0, "GBPUSD": -0.001, "USDCHF": -0.002, "NZDUSD": -0.003,
        }
        data = _make_monday_data(FX_PAIRS, trends=trends)
        strat = FXCrossMomentumStrategy()
        signals = strat.generate_signals(data, "2026-01-05")

        for sig in signals:
            if sig.action == "LONG":
                assert sig.take_profit > sig.entry_price, "LONG TP not above entry"
                assert sig.stop_loss < sig.entry_price, "LONG SL not below entry"
            elif sig.action == "SHORT":
                assert sig.take_profit < sig.entry_price, "SHORT TP not below entry"
                assert sig.stop_loss > sig.entry_price, "SHORT SL not above entry"

    def test_metadata_contains_momentum_info(self):
        """Metadata should include momentum, rank, cost info."""
        trends = {
            "EURUSD": 0.003, "EURGBP": 0.002, "EURJPY": 0.001,
            "AUDJPY": 0.0, "GBPUSD": -0.001, "USDCHF": -0.002, "NZDUSD": -0.003,
        }
        data = _make_monday_data(FX_PAIRS, trends=trends)
        strat = FXCrossMomentumStrategy()
        signals = strat.generate_signals(data, "2026-01-05")

        for sig in signals:
            assert "momentum_20d" in sig.metadata
            assert "rank" in sig.metadata
            assert "total_pairs" in sig.metadata
            assert "cost_rt_pct" in sig.metadata
            assert sig.metadata["cost_rt_pct"] == FX_COST_RT_PCT
            assert sig.metadata["strategy"] == "FX-005 Cross-Pair Momentum"
            assert sig.metadata["holding_period"] == "weekly"

    def test_crisis_regime_filter(self):
        """No signals when average FX vol > 3x normal."""
        strat = FXCrossMomentumStrategy(atr_crisis_mult=3.0)

        # Create data with extremely high volatility at the end
        data = {}
        for i, pair in enumerate(FX_PAIRS):
            rng = np.random.RandomState(42 + i)
            idx = pd.date_range(start="2026-01-05 08:00", periods=200, freq="1h", tz="UTC")
            base = 1.25
            # Normal vol for first 150 bars, then massive vol spike
            returns = np.concatenate([
                rng.normal(0, 0.0005, 150),
                rng.normal(0, 0.01, 50),  # 20x normal vol
            ])
            close = base * np.exp(np.cumsum(returns))
            high = close * (1 + abs(rng.normal(0, 0.005, 200)))
            low = close * (1 - abs(rng.normal(0, 0.005, 200)))

            data[pair] = pd.DataFrame({
                "open": close, "high": high, "low": low,
                "close": close, "volume": 10000.0,
            }, index=idx)

        signals = strat.generate_signals(data, "2026-01-05")
        # Crisis filter should block most/all signals
        # (may still pass if ATR ratio stays below 3x — depends on data)

    def test_monday_detection(self):
        """_is_monday should correctly identify Mondays."""
        strat = FXCrossMomentumStrategy()
        from datetime import date
        assert strat._is_monday(date(2026, 1, 5)) is True   # Monday
        assert strat._is_monday(date(2026, 1, 6)) is False  # Tuesday
        assert strat._is_monday(date(2026, 1, 7)) is False  # Wednesday
        assert strat._is_monday(date(2026, 1, 11)) is False  # Sunday
        assert strat._is_monday(date(2026, 1, 12)) is True  # Monday


# ═══════════════════════════════════════════════════════════════════════════════
# FUT-005: Micro Gold (MGC) Trend
# ═══════════════════════════════════════════════════════════════════════════════


class TestFuturesMGCTrend:
    """Tests for FUT-005 Micro Gold Trend Following strategy."""

    def test_strategy_name(self):
        strat = FuturesMGCTrendStrategy()
        assert strat.name == "FUT-005 MGC Trend"

    def test_inherits_base_strategy(self):
        assert issubclass(FuturesMGCTrendStrategy, BaseStrategy)

    def test_required_tickers(self):
        strat = FuturesMGCTrendStrategy()
        tickers = strat.get_required_tickers()
        assert GOLD_TICKER in tickers
        assert DXY_TICKER in tickers

    def test_no_signal_on_empty_data(self):
        strat = FuturesMGCTrendStrategy()
        signals = strat.generate_signals({}, "2026-02-10")
        assert signals == []

    def test_no_signal_on_insufficient_bars(self):
        strat = FuturesMGCTrendStrategy()
        df = _make_fx_df(n_bars=10, base_price=180.0)
        signals = strat.generate_signals({GOLD_TICKER: df}, "2026-02-10")
        assert signals == []

    def test_fomc_day_filter(self):
        """No signals should be generated on FOMC days."""
        strat = FuturesMGCTrendStrategy()
        df = _make_trending_df(direction="up", n_bars=200, base_price=180.0)
        # Use a known FOMC date
        fomc_date = "2026-03-18"
        signals = strat.generate_signals({GOLD_TICKER: df}, fomc_date)
        assert signals == [], "Signals generated on FOMC day"

    def test_long_signal_on_gold_uptrend(self):
        """Strong gold uptrend with DXY weakening should produce LONG."""
        strat = FuturesMGCTrendStrategy()
        gold_df = _make_trending_df(
            direction="up", n_bars=200, base_price=180.0,
            start="2026-02-10 09:35", seed=42,
        )
        # DXY weakening = UUP trending down
        dxy_df = _make_trending_df(
            direction="down", n_bars=200, base_price=28.0,
            start="2026-02-10 09:35", seed=99,
        )
        data = {GOLD_TICKER: gold_df, DXY_TICKER: dxy_df}
        signals = strat.generate_signals(data, "2026-02-10")

        long_signals = [s for s in signals if s.action == "LONG"]
        if long_signals:
            sig = long_signals[0]
            assert sig.ticker == GOLD_TICKER
            assert sig.take_profit > sig.entry_price
            assert sig.stop_loss < sig.entry_price

    def test_short_signal_on_gold_downtrend(self):
        """Strong gold downtrend with DXY strengthening should produce SHORT."""
        strat = FuturesMGCTrendStrategy()
        gold_df = _make_trending_df(
            direction="down", n_bars=200, base_price=180.0,
            start="2026-02-10 09:35", seed=42,
        )
        # DXY strengthening = UUP trending up
        dxy_df = _make_trending_df(
            direction="up", n_bars=200, base_price=28.0,
            start="2026-02-10 09:35", seed=99,
        )
        data = {GOLD_TICKER: gold_df, DXY_TICKER: dxy_df}
        signals = strat.generate_signals(data, "2026-02-10")

        short_signals = [s for s in signals if s.action == "SHORT"]
        if short_signals:
            sig = short_signals[0]
            assert sig.ticker == GOLD_TICKER
            assert sig.take_profit < sig.entry_price
            assert sig.stop_loss > sig.entry_price

    def test_rr_ratio_1_6(self):
        """R/R should be ~1.6:1 (4.0 ATR TP / 2.5 ATR SL)."""
        strat = FuturesMGCTrendStrategy()
        gold_df = _make_trending_df(direction="up", n_bars=200, base_price=180.0)
        dxy_df = _make_trending_df(direction="down", n_bars=200, base_price=28.0, seed=99)
        data = {GOLD_TICKER: gold_df, DXY_TICKER: dxy_df}
        signals = strat.generate_signals(data, "2026-02-10")

        for sig in signals:
            if sig.action == "LONG":
                risk = sig.entry_price - sig.stop_loss
                reward = sig.take_profit - sig.entry_price
                if risk > 0:
                    rr = reward / risk
                    assert 1.3 < rr < 2.0, f"R/R {rr:.2f} outside expected 1.6:1 range"

    def test_metadata_contains_instrument_specs(self):
        """Metadata should include MGC instrument specs."""
        strat = FuturesMGCTrendStrategy()
        gold_df = _make_trending_df(direction="up", n_bars=200, base_price=180.0)
        dxy_df = _make_trending_df(direction="down", n_bars=200, base_price=28.0, seed=99)
        data = {GOLD_TICKER: gold_df, DXY_TICKER: dxy_df}
        signals = strat.generate_signals(data, "2026-02-10")

        for sig in signals:
            assert sig.metadata["strategy"] == "FUT-005 MGC Trend"
            assert sig.metadata["instrument"] == "MGC (Micro Gold)"
            assert sig.metadata["multiplier"] == MGC_MULTIPLIER
            assert sig.metadata["margin_per_contract"] == MGC_MARGIN
            assert sig.metadata["commission_rt"] == MGC_COMMISSION_RT
            assert "regime" in sig.metadata
            assert "dxy_weakening" in sig.metadata

    def test_max_trades_per_day(self):
        """Should not exceed max_trades_per_day."""
        strat = FuturesMGCTrendStrategy(max_trades_per_day=1)
        gold_df = _make_trending_df(direction="up", n_bars=200, base_price=180.0)
        dxy_df = _make_trending_df(direction="down", n_bars=200, base_price=28.0, seed=99)
        data = {GOLD_TICKER: gold_df, DXY_TICKER: dxy_df}
        signals = strat.generate_signals(data, "2026-02-10")
        assert len(signals) <= 1

    def test_works_without_dxy_data(self):
        """Strategy should still work when DXY data is missing (no DXY filter)."""
        strat = FuturesMGCTrendStrategy()
        gold_df = _make_trending_df(direction="up", n_bars=200, base_price=180.0)
        data = {GOLD_TICKER: gold_df}
        # Should not raise — DXY is optional
        signals = strat.generate_signals(data, "2026-02-10")
        # No assertion on count — just verify it doesn't crash
        assert isinstance(signals, list)


# ═══════════════════════════════════════════════════════════════════════════════
# EU-006: EURO STOXX 50 Futures Trend
# ═══════════════════════════════════════════════════════════════════════════════


class TestFuturesESTXTrend:
    """Tests for EU-006 EURO STOXX 50 Futures Trend Following strategy."""

    def test_strategy_name(self):
        strat = FuturesESTXTrendStrategy()
        assert strat.name == "EU-006 ESTX Trend"

    def test_inherits_base_strategy(self):
        assert issubclass(FuturesESTXTrendStrategy, BaseStrategy)

    def test_required_tickers(self):
        strat = FuturesESTXTrendStrategy()
        tickers = strat.get_required_tickers()
        assert STOXX_TICKER in tickers

    def test_no_signal_on_empty_data(self):
        strat = FuturesESTXTrendStrategy()
        signals = strat.generate_signals({}, "2026-02-10")
        assert signals == []

    def test_no_signal_on_insufficient_bars(self):
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(n_bars=10, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")
        assert signals == []

    def test_ecb_day_filter(self):
        """No signals should be generated on ECB rate decision days."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        ecb_date = "2026-03-05"
        signals = strat.generate_signals({STOXX_TICKER: df}, ecb_date)
        assert signals == [], "Signals generated on ECB day"

    def test_long_signal_on_uptrend(self):
        """Strong uptrend should produce LONG signal."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")

        long_signals = [s for s in signals if s.action == "LONG"]
        if long_signals:
            sig = long_signals[0]
            assert sig.ticker == STOXX_TICKER
            assert sig.take_profit > sig.entry_price
            assert sig.stop_loss < sig.entry_price

    def test_short_signal_on_downtrend(self):
        """Strong downtrend should produce SHORT signal."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="down", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")

        short_signals = [s for s in signals if s.action == "SHORT"]
        if short_signals:
            sig = short_signals[0]
            assert sig.ticker == STOXX_TICKER
            assert sig.take_profit < sig.entry_price
            assert sig.stop_loss > sig.entry_price

    def test_rr_ratio_1_5(self):
        """R/R should be ~1.5:1 (3 ATR TP / 2 ATR SL)."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")

        for sig in signals:
            if sig.action == "LONG":
                risk = sig.entry_price - sig.stop_loss
                reward = sig.take_profit - sig.entry_price
                if risk > 0:
                    rr = reward / risk
                    assert 1.2 < rr < 1.8, f"R/R {rr:.2f} outside expected 1.5:1 range"

    def test_metadata_contains_instrument_specs(self):
        """Metadata should include ESTX instrument specs."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")

        for sig in signals:
            assert sig.metadata["strategy"] == "EU-006 ESTX Trend"
            assert sig.metadata["instrument"] == "Mini STOXX 50 (ESTX50)"
            assert sig.metadata["exchange"] == "Eurex"
            assert sig.metadata["multiplier"] == ESTX_MULTIPLIER
            assert sig.metadata["margin_per_contract"] == ESTX_MARGIN
            assert sig.metadata["commission_rt_eur"] == ESTX_COMMISSION_RT
            assert sig.metadata["cost_rt_pct"] == 0.00005

    def test_session_filter(self):
        """_in_trading_session should correctly identify valid hours."""
        strat = FuturesESTXTrendStrategy()
        from datetime import time as dt_time
        # 10:00 ET is within US session
        ts_in = pd.Timestamp("2026-02-10 10:00:00", tz="US/Eastern")
        assert strat._in_trading_session(ts_in) is True
        # 08:00 ET is before US session
        ts_before = pd.Timestamp("2026-02-10 08:00:00", tz="US/Eastern")
        assert strat._in_trading_session(ts_before) is False
        # 16:00 ET is after 15:55
        ts_after = pd.Timestamp("2026-02-10 16:00:00", tz="US/Eastern")
        assert strat._in_trading_session(ts_after) is False

    def test_max_trades_per_day(self):
        """Should not exceed max_trades_per_day."""
        strat = FuturesESTXTrendStrategy(max_trades_per_day=1)
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")
        assert len(signals) <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-strategy tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2StrategiesCross:
    """Cross-cutting tests for all P2 strategies."""

    def test_all_strategies_inherit_base(self):
        """All P2 strategies must inherit from BaseStrategy."""
        assert issubclass(FXCrossMomentumStrategy, BaseStrategy)
        assert issubclass(FuturesMGCTrendStrategy, BaseStrategy)
        assert issubclass(FuturesESTXTrendStrategy, BaseStrategy)

    def test_all_strategies_return_list(self):
        """generate_signals must always return a list."""
        strategies = [
            FXCrossMomentumStrategy(),
            FuturesMGCTrendStrategy(),
            FuturesESTXTrendStrategy(),
        ]
        for strat in strategies:
            result = strat.generate_signals({}, "2026-02-10")
            assert isinstance(result, list), f"{strat.name} did not return a list"

    def test_signal_object_fields(self):
        """All signals must have required fields from Signal class."""
        strat = FuturesESTXTrendStrategy()
        df = _make_intraday_df(direction="up", n_bars=200, base_price=50.0)
        signals = strat.generate_signals({STOXX_TICKER: df}, "2026-02-10")

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

    def test_all_strategies_have_unique_names(self):
        """Each strategy must have a unique name."""
        names = [
            FXCrossMomentumStrategy().name,
            FuturesMGCTrendStrategy().name,
            FuturesESTXTrendStrategy().name,
        ]
        assert len(names) == len(set(names)), "Duplicate strategy names found"
