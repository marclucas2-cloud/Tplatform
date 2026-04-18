"""Tests MCLOvernightMonTrend — paper-only T3-A1 validated strategy.

Couvre:
  - Interface StrategyBase (name, asset_class, broker, on_bar, get_parameters)
  - Pattern day: vendredi uniquement (dayofweek == 4) pour capturer weekend gap
  - Trend filter: close.pct_change(10) > 0 requis
  - Data freshness guard: skip si bar stale (MAX_BAR_AGE_DAYS)
  - Pas de signal sans data feed
  - Pas de signal sans assez de barres (lookback+1)
  - Signal BUY avec SL/TP quand conditions remplies
  - Idempotence (un signal max par vendredi)
  - Parametres complets

Note: le signal trigger au VENDREDI (pas lundi) pour capturer fidelement
le weekend gap du backtest (entry a l'open dimanche soir futures, proche du
close vendredi). Cf docstring de la strat pour le mapping backtest -> runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.types import Bar, PortfolioState
from strategies_v2.futures.mcl_overnight_mon_trend import MCLOvernightMonTrend


def _make_mcl_df(
    n_days: int = 30,
    end: pd.Timestamp | None = None,
    base_price: float = 75.0,
    trend: float = 0.002,
    volatility: float = 0.008,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic daily MCL OHLCV data ending near `end` (default today)."""
    if end is None:
        end = pd.Timestamp.utcnow().tz_localize(None).normalize()
    rng = np.random.RandomState(seed)
    # Business days ending at or just before `end`
    idx = pd.date_range(end=end, periods=n_days, freq="B")
    closes = [base_price]
    for _ in range(1, n_days):
        change = trend + rng.normal(0, volatility)
        closes.append(closes[-1] * (1 + change))
    closes = np.array(closes)
    highs = closes * (1 + rng.uniform(0.002, 0.010, n_days))
    lows = closes * (1 - rng.uniform(0.002, 0.010, n_days))
    opens = closes * (1 + rng.uniform(-0.005, 0.005, n_days))
    volumes = rng.randint(1000, 5000, n_days).astype(float)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _last_friday(df: pd.DataFrame) -> pd.Timestamp:
    """Pick the last Friday in the DataFrame index."""
    fridays = [ts for ts in df.index if ts.dayofweek == 4]
    assert len(fridays) > 0, "No Friday in test data"
    return fridays[-1]


def _make_portfolio() -> PortfolioState:
    return PortfolioState(
        equity=10000.0,
        cash=10000.0,
        positions={},
        exposure_long=0.0,
        exposure_short=0.0,
        drawdown_pct=0.0,
        margin_used=0.0,
    )


def _build_bar(df: pd.DataFrame, ts: pd.Timestamp) -> Bar:
    row = df.loc[ts]
    return Bar(
        symbol="MCL", timestamp=ts,
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=float(row["close"]),
        volume=float(row["volume"]),
    )


class TestInterface:

    def test_name(self):
        assert MCLOvernightMonTrend().name == "mcl_overnight_mon_trend10"

    def test_asset_class(self):
        assert MCLOvernightMonTrend().asset_class == "futures"

    def test_broker(self):
        assert MCLOvernightMonTrend().broker == "ibkr"

    def test_trigger_dayofweek_is_friday(self):
        """Confirme que le trigger est vendredi (pas lundi) - cf review N2."""
        assert MCLOvernightMonTrend.TRIGGER_DAYOFWEEK == 4

    def test_get_parameters_complete(self):
        params = MCLOvernightMonTrend().get_parameters()
        assert set(params.keys()) == {"symbol", "lookback", "sl_pct", "tp_pct"}
        assert params["symbol"] == "MCL"
        assert params["lookback"] == 10
        assert params["sl_pct"] == 0.012
        assert params["tp_pct"] == 0.018


class TestSignalGating:

    def test_no_signal_without_data_feed(self):
        strat = MCLOvernightMonTrend()
        # Pick a real Friday in the past for determinism
        bar = Bar(
            symbol="MCL",
            timestamp=pd.Timestamp("2026-04-17"),  # vendredi
            open=75.0, high=75.5, low=74.5, close=75.2, volume=2000.0,
        )
        assert strat.on_bar(bar, _make_portfolio()) is None

    def test_no_signal_on_non_friday(self):
        """dayofweek != 4 -> None meme avec trend positif."""
        strat = MCLOvernightMonTrend()
        df = _make_mcl_df(n_days=30, trend=0.003, seed=1)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        # Pick a Monday (dayofweek == 0)
        mondays = [ts for ts in df.index if ts.dayofweek == 0]
        assert len(mondays) > 0
        bar_ts = mondays[-1]
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        assert strat.on_bar(bar, _make_portfolio()) is None

    def test_no_signal_on_negative_trend(self):
        """Vendredi mais trend 10j <= 0 -> None."""
        strat = MCLOvernightMonTrend()
        df = _make_mcl_df(n_days=30, trend=-0.003, volatility=0.002, seed=7)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        bar_ts = _last_friday(df)
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        # Verify downtrend actually present
        close = df["close"].astype(float)
        idx_now = df.index.get_loc(bar_ts)
        trend = close.iloc[idx_now] / close.iloc[idx_now - 10] - 1.0
        assert trend <= 0, f"Setup invalid: trend={trend:.4f}, expected < 0"
        assert strat.on_bar(bar, _make_portfolio()) is None

    def test_no_signal_insufficient_data(self):
        """Moins de lookback+1 barres -> None."""
        strat = MCLOvernightMonTrend()
        df = _make_mcl_df(n_days=5, trend=0.005, seed=3)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        fridays = [ts for ts in df.index if ts.dayofweek == 4]
        if not fridays:
            # Short window may not include Friday — skip
            return
        bar_ts = fridays[-1]
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        assert strat.on_bar(bar, _make_portfolio()) is None

    def test_no_signal_on_stale_data(self):
        """Bar > MAX_BAR_AGE_DAYS -> skip silencieux (protection MCL_1D stale)."""
        strat = MCLOvernightMonTrend()
        # Data ending 30 days ago
        stale_end = pd.Timestamp.utcnow().tz_localize(None).normalize() - pd.Timedelta(days=30)
        df = _make_mcl_df(n_days=30, end=stale_end, trend=0.005, seed=42)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        bar_ts = _last_friday(df)
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        # Should skip silently (data stale)
        assert strat.on_bar(bar, _make_portfolio()) is None


class TestSignalEmission:

    def test_buy_signal_on_friday_positive_trend(self):
        """Vendredi + trend 10j > 0 -> BUY avec SL/TP coherents."""
        strat = MCLOvernightMonTrend()
        df = _make_mcl_df(n_days=40, trend=0.004, volatility=0.003, seed=42)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        bar_ts = _last_friday(df)
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        close = df["close"].astype(float)
        idx_now = df.index.get_loc(bar_ts)
        trend = close.iloc[idx_now] / close.iloc[idx_now - 10] - 1.0
        assert trend > 0, f"Setup invalid: trend={trend:.4f}, expected > 0"
        bar = _build_bar(df, bar_ts)
        sig = strat.on_bar(bar, _make_portfolio())
        assert sig is not None
        assert sig.symbol == "MCL"
        assert sig.side == "BUY"
        assert sig.strategy_name == "mcl_overnight_mon_trend10"
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        assert sig.stop_loss < bar.close
        assert sig.take_profit > bar.close
        assert pytest.approx(sig.stop_loss, rel=1e-6) == bar.close * (1 - 0.012)
        assert pytest.approx(sig.take_profit, rel=1e-6) == bar.close * (1 + 0.018)

    def test_idempotence_no_duplicate_same_day(self):
        """Deux appels le meme vendredi -> un seul signal."""
        strat = MCLOvernightMonTrend()
        df = _make_mcl_df(n_days=40, trend=0.004, volatility=0.003, seed=42)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        bar_ts = _last_friday(df)
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        sig1 = strat.on_bar(bar, _make_portfolio())
        sig2 = strat.on_bar(bar, _make_portfolio())
        assert sig1 is not None
        assert sig2 is None

    def test_custom_params_applied(self):
        """Les parametres custom (lookback, sl_pct, tp_pct) sont respectes."""
        strat = MCLOvernightMonTrend(lookback=5, sl_pct=0.02, tp_pct=0.03)
        df = _make_mcl_df(n_days=40, trend=0.005, volatility=0.003, seed=42)
        feed = DataFeed({"MCL": df})
        strat.set_data_feed(feed)
        bar_ts = _last_friday(df)
        feed.set_timestamp(bar_ts + pd.Timedelta(days=1))
        bar = _build_bar(df, bar_ts)
        sig = strat.on_bar(bar, _make_portfolio())
        if sig is not None:
            assert pytest.approx(sig.stop_loss, rel=1e-6) == bar.close * (1 - 0.02)
            assert pytest.approx(sig.take_profit, rel=1e-6) == bar.close * (1 + 0.03)
        params = strat.get_parameters()
        assert params["lookback"] == 5
        assert params["sl_pct"] == 0.02
        assert params["tp_pct"] == 0.03
