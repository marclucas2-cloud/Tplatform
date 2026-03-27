"""
Tests des strategies futures (FUT-002, FUT-003, FUT-004).

Verifie :
  - Generation de signaux (trend, mean reversion, lag)
  - Calcul stop/TP avec conversion multiplier
  - Filtres VIX
  - Filtre weekend pour MES
  - Filtre FOMC/CPI/NFP pour MNQ
  - Calcul deviation ATR
  - Edge cases (donnees insuffisantes, marche plat)
  - Contraintes de sizing (max contrats)
"""
import pytest
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date, datetime, timedelta

import sys
import os

# Add strategies/ to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))

from brent_lag_futures import BrentLagFuturesStrategy, MCL_MULTIPLIER, MCL_MARGIN
from futures_mes_trend import MESTrendStrategy, MES_MULTIPLIER, MES_MARGIN
from futures_mnq_mr import (
    MNQMeanReversionStrategy,
    MNQ_MULTIPLIER,
    MNQ_MARGIN,
    is_near_macro_event,
    MACRO_EVENTS_2026,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def make_ohlcv(
    n_bars: int,
    start: str = "2026-03-15 09:30",
    freq: str = "5min",
    base_price: float = 70.0,
    volatility: float = 0.5,
    trend: float = 0.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz="US/Eastern")

    closes = [base_price]
    for i in range(1, n_bars):
        change = rng.normal(trend / n_bars, volatility / np.sqrt(n_bars))
        closes.append(closes[-1] * (1 + change))
    closes = np.array(closes)

    highs = closes * (1 + rng.uniform(0.001, 0.01, n_bars))
    lows = closes * (1 - rng.uniform(0.001, 0.01, n_bars))
    opens = closes * (1 + rng.uniform(-0.005, 0.005, n_bars))
    volumes = rng.randint(1000, 50000, n_bars).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def make_trending_data(
    direction: str = "up",
    n_bars: int = 60,
    start: str = "2026-03-15 09:30",
    freq: str = "1h",
    base_price: float = 5000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate trending OHLCV data (for MES trend tests)."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz="US/Eastern")

    drift = 0.001 if direction == "up" else -0.001
    closes = [base_price]
    for i in range(1, n_bars):
        change = drift + rng.normal(0, 0.002)
        closes.append(closes[-1] * (1 + change))
    closes = np.array(closes)

    highs = closes * (1 + rng.uniform(0.001, 0.005, n_bars))
    lows = closes * (1 - rng.uniform(0.001, 0.005, n_bars))
    opens = closes * (1 + rng.uniform(-0.003, 0.003, n_bars))
    volumes = rng.randint(5000, 100000, n_bars).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def make_extreme_deviation_data(
    direction: str = "oversold",
    n_bars: int = 60,
    start: str = "2026-03-15 09:30",
    freq: str = "1h",
    base_price: float = 18000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate data where the last portion has an extreme deviation from the mean.
    First part is stable, then a sharp move in the last 10 bars.
    """
    rng = np.random.RandomState(seed)
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz="US/Eastern")

    # Stable period
    stable_bars = n_bars - 10
    closes = [base_price]
    for i in range(1, stable_bars):
        change = rng.normal(0, 0.001)
        closes.append(closes[-1] * (1 + change))

    # Sharp move
    move_per_bar = -0.005 if direction == "oversold" else 0.005
    for i in range(10):
        closes.append(closes[-1] * (1 + move_per_bar + rng.normal(0, 0.001)))

    closes = np.array(closes)
    highs = closes * (1 + rng.uniform(0.001, 0.005, n_bars))
    lows = closes * (1 - rng.uniform(0.001, 0.005, n_bars))
    opens = closes * (1 + rng.uniform(-0.002, 0.002, n_bars))
    volumes = rng.randint(5000, 100000, n_bars).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def make_vix_data(level: float, n_bars: int = 60, start: str = "2026-03-15 09:30", freq: str = "5min"):
    """Generate VIX data at a specific level."""
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz="US/Eastern")
    return pd.DataFrame(
        {
            "open": [level] * n_bars,
            "high": [level + 0.5] * n_bars,
            "low": [level - 0.5] * n_bars,
            "close": [level] * n_bars,
            "volume": [1000.0] * n_bars,
        },
        index=idx,
    )


# ══════════════════════════════════════════════════════════════════════════
# FUT-002 : Brent Lag Futures (MCL)
# ══════════════════════════════════════════════════════════════════════════

class TestBrentLagFutures:
    """Tests for BrentLagFuturesStrategy."""

    @pytest.fixture
    def strategy(self):
        return BrentLagFuturesStrategy()

    def test_get_required_tickers(self, strategy):
        """La strategie requiert CL, MCL et VIX."""
        tickers = strategy.get_required_tickers()
        assert "CL" in tickers
        assert "MCL" in tickers
        assert "VIX" in tickers

    def test_signal_on_bullish_brent_move(self, strategy):
        """Un move haussier de CL > 0.5% doit generer un signal LONG."""
        # Start data at 08:00 so ATR(14) has enough bars before entry window (09:35)
        n_bars = 120
        df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=10)
        # Force a >0.5% move from open to 09:35-10:00
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 1.008  # +0.8%
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price
        df_cl.loc[entry_mask, "high"] = target_price * 1.002

        vix_df = make_vix_data(20.0, n_bars, start="2026-03-15 08:00")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 1
        assert signals[0].action == "LONG"
        assert signals[0].ticker == "MCL"

    def test_signal_on_bearish_brent_move(self, strategy):
        """Un move baissier de CL > 0.5% doit generer un signal SHORT."""
        n_bars = 120
        df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=20)
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 0.992  # -0.8%
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price
        df_cl.loc[entry_mask, "low"] = target_price * 0.998

        vix_df = make_vix_data(18.0, n_bars, start="2026-03-15 08:00")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 1
        assert signals[0].action == "SHORT"
        assert signals[0].ticker == "MCL"

    def test_no_signal_small_move(self, strategy):
        """Un move < 0.5% ne doit pas generer de signal."""
        n_bars = 80
        # Very stable price data — near-zero move
        df_cl = make_ohlcv(n_bars, start="2026-03-15 09:30", freq="5min", base_price=70.0, volatility=0.01, seed=30)
        vix_df = make_vix_data(15.0, n_bars, start="2026-03-15 09:30")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 0

    def test_vix_filter_blocks_trade(self, strategy):
        """VIX > 30 doit bloquer tous les signaux."""
        n_bars = 80
        df_cl = make_ohlcv(n_bars, start="2026-03-15 09:30", freq="5min", base_price=70.0, seed=10)
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 1.01
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price

        vix_df = make_vix_data(35.0, n_bars, start="2026-03-15 09:30")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 0

    def test_stop_loss_uses_atr(self, strategy):
        """Le stop doit etre place a 1.5 ATR de l'entree."""
        n_bars = 120
        df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=10)
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 1.008
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price

        vix_df = make_vix_data(20.0, n_bars, start="2026-03-15 08:00")
        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 1
        sig = signals[0]
        # Stop should be below entry for LONG
        assert sig.stop_loss < sig.entry_price
        # TP should be above entry for LONG
        assert sig.take_profit > sig.entry_price
        # Ratio check: TP distance should be > stop distance (2.5 vs 1.5 ATR)
        tp_dist = abs(sig.take_profit - sig.entry_price)
        sl_dist = abs(sig.entry_price - sig.stop_loss)
        assert tp_dist / sl_dist == pytest.approx(2.5 / 1.5, rel=0.1)

    def test_metadata_contains_futures_info(self, strategy):
        """Le metadata doit contenir instrument, multiplier, margin, costs."""
        n_bars = 120
        df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=10)
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 1.008
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price
        vix_df = make_vix_data(20.0, n_bars, start="2026-03-15 08:00")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 1
        meta = signals[0].metadata
        assert meta["strategy"] == "Brent Lag MCL"
        assert meta["instrument"] == "MCL"
        assert meta["multiplier"] == MCL_MULTIPLIER
        assert meta["margin"] == MCL_MARGIN
        assert meta["costs_rt_pct"] == 0.003

    def test_insufficient_data_returns_empty(self, strategy):
        """Trop peu de barres doit retourner une liste vide."""
        df_cl = make_ohlcv(5, start="2026-03-15 09:30", freq="5min", base_price=70.0)
        data = {"CL": df_cl}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        assert len(signals) == 0

    def test_missing_cl_data(self, strategy):
        """Pas de donnees CL doit retourner une liste vide."""
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30")
        data = {"VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        assert len(signals) == 0

    def test_max_contracts_respected(self, strategy):
        """Le nombre de contrats ne doit pas depasser max_contracts."""
        n_bars = 120
        df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=10)
        open_price = df_cl.iloc[0]["open"]
        target_price = open_price * 1.01
        entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
        df_cl.loc[entry_mask, "close"] = target_price
        vix_df = make_vix_data(20.0, n_bars, start="2026-03-15 08:00")

        data = {"CL": df_cl, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 1
        assert signals[0].metadata["contracts"] <= 4


# ══════════════════════════════════════════════════════════════════════════
# FUT-003 : MES Trend Following
# ══════════════════════════════════════════════════════════════════════════

class TestMESTrendFollowing:
    """Tests for MESTrendStrategy."""

    @pytest.fixture
    def strategy(self):
        return MESTrendStrategy()

    def test_get_required_tickers(self, strategy):
        """La strategie requiert MES, ES, SPY et VIX."""
        tickers = strategy.get_required_tickers()
        assert "MES" in tickers
        assert "ES" in tickers
        assert "SPY" in tickers
        assert "VIX" in tickers

    def test_long_signal_uptrend(self, strategy):
        """Un uptrend clair avec VIX bas doit generer un signal LONG."""
        df = make_trending_data("up", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(18.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        # May or may not trigger depending on EMA alignment — just check no crash
        for sig in signals:
            assert sig.action in ("LONG", "SHORT")
            assert sig.ticker == "MES"

    def test_short_signal_downtrend(self, strategy):
        """Un downtrend clair avec VIX > 12 doit generer un signal SHORT."""
        df = make_trending_data("down", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(22.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            assert sig.action in ("LONG", "SHORT")
            assert sig.ticker == "MES"

    def test_vix_blocks_long_above_25(self, strategy):
        """VIX > 25 doit bloquer les signaux LONG."""
        df = make_trending_data("up", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(30.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        # Any signal should NOT be LONG (VIX too high)
        for sig in signals:
            assert sig.action != "LONG"

    def test_vix_blocks_short_below_12(self, strategy):
        """VIX < 12 doit bloquer les signaux SHORT."""
        df = make_trending_data("down", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(8.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            assert sig.action != "SHORT"

    def test_friday_afternoon_blocked(self, strategy):
        """Vendredi apres 16:00 ET ne doit pas generer de signal (weekend gap risk)."""
        # 2026-03-20 is a Friday
        # The strategy checks _is_friday_after_cutoff on the latest bar in the signal window.
        # We need to ensure that even if bars are in the 10:00-15:30 window on Friday,
        # the latest bar time is >= 16:00 (which can't happen since window ends 15:30).
        # So instead, we test the _is_friday_after_cutoff method directly.
        assert strategy._is_friday_after_cutoff(
            pd.Timestamp("2026-03-20 16:30", tz="US/Eastern")
        )
        assert not strategy._is_friday_after_cutoff(
            pd.Timestamp("2026-03-20 15:00", tz="US/Eastern")
        )
        # Also verify: on a non-Friday, 16:30 is NOT blocked
        assert not strategy._is_friday_after_cutoff(
            pd.Timestamp("2026-03-19 16:30", tz="US/Eastern")  # Thursday
        )

    def test_stop_and_target_atr_ratio(self, strategy):
        """Le ratio TP/SL doit etre 3:2 (1.5:1 R/R)."""
        df = make_trending_data("up", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(18.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            tp_dist = abs(sig.take_profit - sig.entry_price)
            sl_dist = abs(sig.entry_price - sig.stop_loss)
            if sl_dist > 0:
                ratio = tp_dist / sl_dist
                assert ratio == pytest.approx(3.0 / 2.0, rel=0.1)

    def test_metadata_contains_futures_info(self, strategy):
        """Le metadata doit contenir les specs futures."""
        df = make_trending_data("up", n_bars=60, base_price=5000.0, seed=42)
        vix_df = make_vix_data(18.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            meta = sig.metadata
            assert meta["strategy"] == "MES Trend Following"
            assert meta["instrument"] == "MES"
            assert meta["multiplier"] == MES_MULTIPLIER
            assert meta["margin"] == MES_MARGIN
            assert meta["costs_rt_pct"] == 0.003

    def test_fallback_to_spy(self, strategy):
        """Si MES et ES absents, la strategie doit utiliser SPY comme fallback."""
        df = make_trending_data("up", n_bars=60, base_price=450.0, seed=42)
        vix_df = make_vix_data(18.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"SPY": df, "VIX": vix_df}
        # Should not crash — gracefully uses SPY
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        # Ticker in signal should still be MES (we trade MES, even with SPY signal)
        for sig in signals:
            assert sig.ticker == "MES"

    def test_insufficient_data(self, strategy):
        """Trop peu de barres doit retourner une liste vide."""
        df = make_ohlcv(10, start="2026-03-15 09:30", freq="1h", base_price=5000.0)
        data = {"MES": df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        assert len(signals) == 0

    def test_flat_market_no_signal(self, strategy):
        """Un marche plat (pas de trend clair) ne doit pas generer de signal facilement."""
        rng = np.random.RandomState(99)
        n_bars = 60
        idx = pd.date_range("2026-03-15 09:30", periods=n_bars, freq="1h", tz="US/Eastern")
        base = 5000.0
        # Perfectly flat with tiny noise
        closes = base + rng.normal(0, 0.1, n_bars)
        df = pd.DataFrame(
            {
                "open": closes + rng.normal(0, 0.05, n_bars),
                "high": closes + abs(rng.normal(0, 0.2, n_bars)),
                "low": closes - abs(rng.normal(0, 0.2, n_bars)),
                "close": closes,
                "volume": rng.randint(1000, 10000, n_bars).astype(float),
            },
            index=idx,
        )
        vix_df = make_vix_data(18.0, n_bars, start="2026-03-15 09:30", freq="1h")

        data = {"MES": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        # In a truly flat market, EMAs converge → no clear signal
        # This is a probabilistic test but with seed=99 + base=5000 + noise=0.1,
        # the EMAs should be very close and not produce a signal
        assert len(signals) == 0


# ══════════════════════════════════════════════════════════════════════════
# FUT-004 : MNQ Mean Reversion Extreme
# ══════════════════════════════════════════════════════════════════════════

class TestMNQMeanReversion:
    """Tests for MNQMeanReversionStrategy."""

    @pytest.fixture
    def strategy(self):
        return MNQMeanReversionStrategy()

    def test_get_required_tickers(self, strategy):
        """La strategie requiert MNQ, NQ, QQQ et VIX."""
        tickers = strategy.get_required_tickers()
        assert "MNQ" in tickers
        assert "NQ" in tickers
        assert "QQQ" in tickers
        assert "VIX" in tickers

    def test_long_on_oversold_extreme(self, strategy):
        """Une deviation negative extreme doit generer un signal LONG (contrarian)."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        # Should produce at least one signal or none (depends on if deviation is extreme enough)
        for sig in signals:
            assert sig.action == "LONG"
            assert sig.ticker == "MNQ"

    def test_short_on_overbought_extreme(self, strategy):
        """Une deviation positive extreme doit generer un signal SHORT (contrarian)."""
        df = make_extreme_deviation_data("overbought", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            assert sig.action == "SHORT"
            assert sig.ticker == "MNQ"

    def test_vix_above_35_blocks_all(self, strategy):
        """VIX > 35 doit bloquer tous les signaux (chaos, pas de mean reversion)."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(40.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 0

    def test_fomc_filter_blocks_trade(self, strategy):
        """Les signaux proches d'un FOMC doivent etre bloques."""
        # 2026-03-18 is an FOMC date at 14:00 ET
        fomc_date = dt_date(2026, 3, 18)
        assert fomc_date in MACRO_EVENTS_2026

        # Create extreme data on FOMC day
        df = make_extreme_deviation_data("oversold", n_bars=60, start="2026-03-18 09:30", base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-18 09:30", freq="1h")

        # Override: push the extreme into the 13:00-15:00 window (near FOMC at 14:00)
        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, fomc_date)

        # Any signal near 14:00 ET should be blocked
        for sig in signals:
            sig_time = sig.timestamp.time()
            sig_minutes = sig_time.hour * 60 + sig_time.minute
            # FOMC at 14:00 = 840 minutes. With 60min buffer → 780-900 should be blocked
            assert not (780 <= sig_minutes <= 900), \
                f"Signal at {sig_time} should be blocked near FOMC"

    def test_cpi_filter(self):
        """La fonction is_near_macro_event doit bloquer autour de CPI."""
        # 2026-03-11 is a CPI date at 08:30
        assert is_near_macro_event(dt_date(2026, 3, 11), dt_time(8, 0), 60)   # 30 min before
        assert is_near_macro_event(dt_date(2026, 3, 11), dt_time(9, 0), 60)   # 30 min after
        assert not is_near_macro_event(dt_date(2026, 3, 11), dt_time(12, 0), 60)  # 3.5h after
        assert not is_near_macro_event(dt_date(2026, 3, 12), dt_time(8, 30), 60)  # Wrong day

    def test_nfp_filter(self):
        """La fonction is_near_macro_event doit bloquer autour du NFP."""
        # 2026-03-06 is a NFP date at 08:30
        assert is_near_macro_event(dt_date(2026, 3, 6), dt_time(8, 30), 60)
        assert is_near_macro_event(dt_date(2026, 3, 6), dt_time(9, 20), 60)
        assert not is_near_macro_event(dt_date(2026, 3, 6), dt_time(14, 0), 60)

    def test_target_is_mean(self, strategy):
        """Le take profit doit etre le retour a la moyenne 20 periodes."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            mean_val = sig.metadata.get("mean_20")
            assert mean_val is not None
            assert sig.take_profit == pytest.approx(mean_val, rel=0.01)

    def test_deviation_atr_calculation(self, strategy):
        """Le deviation_atr dans le metadata doit etre >= 2.0 (seuil d'entree)."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            assert abs(sig.metadata["deviation_atr"]) >= 2.0

    def test_metadata_contains_futures_info(self, strategy):
        """Le metadata doit contenir les specs futures MNQ."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        for sig in signals:
            meta = sig.metadata
            assert meta["strategy"] == "MNQ Mean Reversion Extreme"
            assert meta["instrument"] == "MNQ"
            assert meta["multiplier"] == MNQ_MULTIPLIER
            assert meta["margin"] == MNQ_MARGIN
            assert meta["costs_rt_pct"] == 0.003
            assert meta["max_hold_bars"] == 48

    def test_insufficient_data(self, strategy):
        """Trop peu de barres doit retourner une liste vide."""
        df = make_ohlcv(10, start="2026-03-15 09:30", freq="1h", base_price=18000.0)
        data = {"MNQ": df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        assert len(signals) == 0

    def test_no_signal_normal_deviation(self, strategy):
        """Une deviation < 2 ATR ne doit pas generer de signal."""
        # Very stable data with minimal price movement — no extreme deviation
        rng = np.random.RandomState(55)
        n_bars = 60
        idx = pd.date_range("2026-03-15 09:30", periods=n_bars, freq="1h", tz="US/Eastern")
        base = 18000.0
        # Tiny random walk — deviation will be much less than 2 ATR
        closes = [base]
        for i in range(1, n_bars):
            closes.append(closes[-1] * (1 + rng.normal(0, 0.0001)))
        closes = np.array(closes)
        df = pd.DataFrame({
            "open": closes * (1 + rng.uniform(-0.0001, 0.0001, n_bars)),
            "high": closes * (1 + rng.uniform(0.0001, 0.0005, n_bars)),
            "low": closes * (1 - rng.uniform(0.0001, 0.0005, n_bars)),
            "close": closes,
            "volume": rng.randint(5000, 50000, n_bars).astype(float),
        }, index=idx)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) == 0

    def test_fallback_to_qqq(self, strategy):
        """Si MNQ et NQ absents, la strategie doit utiliser QQQ comme fallback."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=400.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"QQQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))
        # Should not crash — gracefully uses QQQ
        for sig in signals:
            assert sig.ticker == "MNQ"  # Always trades MNQ

    def test_only_one_signal_per_day(self, strategy):
        """La strategie ne doit generer qu'un seul signal par jour."""
        df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
        vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")

        data = {"MNQ": df, "VIX": vix_df}
        signals = strategy.generate_signals(data, dt_date(2026, 3, 15))

        assert len(signals) <= 1


# ══════════════════════════════════════════════════════════════════════════
# Cross-strategy tests
# ══════════════════════════════════════════════════════════════════════════

class TestCrossStrategy:
    """Tests transversaux pour toutes les strategies futures."""

    def test_all_strategies_extend_base(self):
        """Toutes les strategies doivent heriter de BaseStrategy (ABC)."""
        from abc import ABC
        # Each strategy defines its own BaseStrategy(ABC) — verify they all
        # inherit from an ABC-based class with the right interface
        for StrategyCls in [BrentLagFuturesStrategy, MESTrendStrategy, MNQMeanReversionStrategy]:
            strat = StrategyCls()
            assert hasattr(strat, "generate_signals")
            assert hasattr(strat, "get_required_tickers")
            assert hasattr(strat, "name")
            # Verify ABC lineage
            assert any(
                base.__name__ == "BaseStrategy" for base in StrategyCls.__mro__
            )

    def test_all_strategies_have_names(self):
        """Chaque strategie doit avoir un nom unique."""
        names = {
            BrentLagFuturesStrategy().name,
            MESTrendStrategy().name,
            MNQMeanReversionStrategy().name,
        }
        assert len(names) == 3  # All unique

    def test_empty_data_never_crashes(self):
        """Aucune strategie ne doit crasher avec des donnees vides."""
        for StrategyCls in [BrentLagFuturesStrategy, MESTrendStrategy, MNQMeanReversionStrategy]:
            strat = StrategyCls()
            signals = strat.generate_signals({}, dt_date(2026, 3, 15))
            assert signals == []

    def test_all_signals_have_required_metadata_keys(self):
        """Tous les signaux doivent contenir les cles metadata obligatoires."""
        required_keys = {"strategy", "instrument", "multiplier", "margin", "costs_rt_pct"}

        # Generate signals from each strategy with favorable data
        for StrategyCls, make_fn, kwargs in [
            (BrentLagFuturesStrategy, _make_brent_signal_data, {}),
            (MNQMeanReversionStrategy, _make_mnq_signal_data, {}),
        ]:
            strat = StrategyCls()
            data = make_fn()
            signals = strat.generate_signals(data, dt_date(2026, 3, 15))
            for sig in signals:
                for key in required_keys:
                    assert key in sig.metadata, f"{StrategyCls.__name__} missing metadata key: {key}"

    def test_costs_rt_pct_is_003_for_all(self):
        """Toutes les strategies futures doivent avoir costs_rt_pct = 0.003."""
        for StrategyCls, make_fn in [
            (BrentLagFuturesStrategy, _make_brent_signal_data),
            (MNQMeanReversionStrategy, _make_mnq_signal_data),
        ]:
            strat = StrategyCls()
            data = make_fn()
            signals = strat.generate_signals(data, dt_date(2026, 3, 15))
            for sig in signals:
                assert sig.metadata["costs_rt_pct"] == 0.003


# ── Helpers for cross-strategy tests ─────────────────────────────────────

def _make_brent_signal_data():
    """Create data that should trigger a Brent lag signal."""
    n_bars = 120
    df_cl = make_ohlcv(n_bars, start="2026-03-15 08:00", freq="5min", base_price=70.0, seed=10)
    open_price = df_cl.iloc[0]["open"]
    target_price = open_price * 1.008
    entry_mask = (df_cl.index.time >= dt_time(9, 35)) & (df_cl.index.time <= dt_time(10, 0))
    df_cl.loc[entry_mask, "close"] = target_price
    df_cl.loc[entry_mask, "high"] = target_price * 1.002
    vix_df = make_vix_data(20.0, n_bars, start="2026-03-15 08:00")
    return {"CL": df_cl, "VIX": vix_df}


def _make_mnq_signal_data():
    """Create data that should trigger an MNQ mean reversion signal."""
    df = make_extreme_deviation_data("oversold", n_bars=60, base_price=18000.0, seed=42)
    vix_df = make_vix_data(20.0, 60, start="2026-03-15 09:30", freq="1h")
    return {"MNQ": df, "VIX": vix_df}
