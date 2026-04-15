"""Unit tests for the 3 US stock monthly strategies (tom, rs_spy, sector_rot_us).

Uses synthetic price data to verify:
  - Target portfolio is empty outside the hold window
  - Target portfolio has the right number of positions inside the window
  - Sides (BUY/SELL) match the expected cross-sectional logic
  - Notional sums respect the capital budget
  - Strategies degrade gracefully with insufficient data
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategies_v2.us.tom import TOMStrategy, TOMConfig
from strategies_v2.us.rs_spy import RSSpyStrategy, RSSpyConfig
from strategies_v2.us.sector_rot_us import SectorRotStrategy, SectorRotConfig
from strategies_v2.us._common import USPosition


def _make_synthetic_prices(
    n_days: int = 500,
    n_stocks: int = 30,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Generate synthetic daily OHLC close for n_stocks over n_days business days."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2023-01-02")
    dates = pd.bdate_range(start, periods=n_days)
    out = {}
    for i in range(n_stocks):
        ticker = f"STK{i:02d}"
        drift = rng.uniform(-0.0003, 0.0008)
        vol = rng.uniform(0.008, 0.025)
        rets = rng.normal(drift, vol, n_days)
        prices = 100 * np.exp(np.cumsum(rets))
        out[ticker] = pd.DataFrame(
            {
                "open": prices * (1 + rng.normal(0, 0.002, n_days)),
                "high": prices * (1 + abs(rng.normal(0, 0.005, n_days))),
                "low": prices * (1 - abs(rng.normal(0, 0.005, n_days))),
                "close": prices,
                "adj_close": prices,
                "volume": rng.integers(1_000_000, 10_000_000, n_days),
            },
            index=dates,
        )
    # SPY benchmark for rs_spy
    spy_rets = rng.normal(0.0004, 0.012, n_days)
    spy_prices = 400 * np.exp(np.cumsum(spy_rets))
    out["SPY"] = pd.DataFrame(
        {
            "open": spy_prices,
            "high": spy_prices * 1.005,
            "low": spy_prices * 0.995,
            "close": spy_prices,
            "adj_close": spy_prices,
            "volume": rng.integers(50_000_000, 100_000_000, n_days),
        },
        index=dates,
    )
    return out


def _patch_universe_loader(monkeypatch, tickers: list[str]):
    """Redirect load_universe() to return the synthetic tickers."""
    from strategies_v2.us import _common, tom, rs_spy, sector_rot_us

    monkeypatch.setattr(_common, "load_universe", lambda: tickers)
    monkeypatch.setattr(tom, "load_universe", lambda: tickers)
    monkeypatch.setattr(rs_spy, "load_universe", lambda: tickers)
    monkeypatch.setattr(sector_rot_us, "load_universe", lambda: tickers)


def _patch_sector_map(monkeypatch, tickers: list[str]):
    """Assign synthetic sectors to tickers, 5 stocks per sector rotating."""
    sectors = ["Technology", "Financials", "Health Care", "Energy", "Industrials", "Consumer Staples"]
    smap = {t: sectors[i % len(sectors)] for i, t in enumerate(tickers)}
    from strategies_v2.us import _common, sector_rot_us

    monkeypatch.setattr(_common, "load_sector_map", lambda: smap)
    monkeypatch.setattr(sector_rot_us, "load_sector_map", lambda: smap)


# ============================================================
# TOM tests
# ============================================================
class TestTOM:
    def test_empty_outside_hold_window(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        strat = TOMStrategy()
        # Pick a date mid-month, clearly outside the [month_end, +3d] window
        as_of = date(2023, 6, 15)
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=as_of)
        assert positions == []

    def test_returns_positions_on_month_end(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        strat = TOMStrategy(TOMConfig(n_stocks=10))
        # 2023-06-30 is a Friday (last trading day of June)
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 6, 30))
        assert len(positions) == 10
        assert all(p.side == "BUY" for p in positions)
        assert all(p.notional == pytest.approx(1_000, rel=1e-3) for p in positions)
        assert all(isinstance(p, USPosition) for p in positions)

    def test_total_notional_matches_capital(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        strat = TOMStrategy(TOMConfig(n_stocks=10))
        positions = strat.compute_target_portfolio(prices, capital=8_333, as_of=date(2023, 6, 30))
        total = sum(p.notional for p in positions)
        assert total == pytest.approx(8_333, rel=1e-3)


# ============================================================
# RS SPY tests
# ============================================================
class TestRSSpy:
    def test_returns_long_and_short_positions(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        strat = RSSpyStrategy(RSSpyConfig(top_n=5))
        positions = strat.compute_target_portfolio(prices, capital=8_333, as_of=date(2023, 6, 30))
        assert len(positions) == 10  # 5 long + 5 short
        n_long = sum(1 for p in positions if p.side == "BUY")
        n_short = sum(1 for p in positions if p.side == "SELL")
        assert n_long == 5
        assert n_short == 5

    def test_dollar_neutral_sizing(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        strat = RSSpyStrategy(RSSpyConfig(top_n=5))
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 6, 30))
        long_notional = sum(p.notional for p in positions if p.side == "BUY")
        short_notional = sum(p.notional for p in positions if p.side == "SELL")
        assert long_notional == pytest.approx(short_notional, rel=1e-3)
        assert (long_notional + short_notional) == pytest.approx(10_000, rel=1e-3)

    def test_returns_empty_when_spy_missing(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        del prices["SPY"]
        tickers = list(prices.keys())
        _patch_universe_loader(monkeypatch, tickers)
        strat = RSSpyStrategy()
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 6, 30))
        assert positions == []


# ============================================================
# Sector Rot tests
# ============================================================
class TestSectorRot:
    def test_returns_one_long_one_short(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        _patch_sector_map(monkeypatch, tickers)
        strat = SectorRotStrategy()
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 6, 30))
        assert len(positions) == 2
        assert {p.side for p in positions} == {"BUY", "SELL"}

    def test_dollar_neutral(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=300, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        _patch_sector_map(monkeypatch, tickers)
        strat = SectorRotStrategy()
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 6, 30))
        long_n = sum(p.notional for p in positions if p.side == "BUY")
        short_n = sum(p.notional for p in positions if p.side == "SELL")
        assert long_n == pytest.approx(5_000, rel=1e-3)
        assert short_n == pytest.approx(5_000, rel=1e-3)

    def test_empty_with_insufficient_history(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=30, n_stocks=30)  # too short
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        _patch_sector_map(monkeypatch, tickers)
        strat = SectorRotStrategy()
        positions = strat.compute_target_portfolio(prices, capital=10_000, as_of=date(2023, 1, 31))
        assert positions == []


# ============================================================
# Production-day scenarios (critical: no future data available)
# ============================================================
class TestProductionDay:
    """Regression tests for the bug where strats required future data (next_month_end)
    in the index to trigger. In live paper trading on Alpaca, the script runs at
    22:55 Paris on day T with data only up to day T — no T+1, T+2, ..., so the
    strats must make decisions with prev_end + last_end + as_of only.
    """

    @staticmethod
    def _truncate_prices(prices, max_date):
        """Truncate all price series to <= max_date."""
        cutoff = pd.Timestamp(max_date)
        return {t: df[df.index <= cutoff] for t, df in prices.items()}

    def test_tom_triggers_on_exact_month_end_day(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=500, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)

        # 2023-06-30 is a Friday = last business day of June 2023
        truncated = self._truncate_prices(prices, "2023-06-30")
        strat = TOMStrategy()
        positions = strat.compute_target_portfolio(truncated, capital=8_333, as_of=date(2023, 6, 30))
        assert len(positions) == 10, "TOM should trigger on the actual month-end day"

    def test_rs_spy_triggers_on_exact_month_end_day(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=500, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)

        truncated = self._truncate_prices(prices, "2023-06-30")
        strat = RSSpyStrategy()
        positions = strat.compute_target_portfolio(truncated, capital=8_333, as_of=date(2023, 6, 30))
        assert len(positions) == 10

    def test_sector_rot_triggers_on_exact_month_end_day(self, monkeypatch):
        prices = _make_synthetic_prices(n_days=500, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)
        _patch_sector_map(monkeypatch, tickers)

        truncated = self._truncate_prices(prices, "2023-06-30")
        strat = SectorRotStrategy()
        positions = strat.compute_target_portfolio(truncated, capital=8_333, as_of=date(2023, 6, 30))
        assert len(positions) == 2

    def test_tom_exits_after_hold_days(self, monkeypatch):
        """TOM should return [] once N trading days have elapsed past month-end."""
        prices = _make_synthetic_prices(n_days=500, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)

        # 2023-07-10 is Monday, ~6 business days after 2023-06-30 → TOM should be flat
        truncated = self._truncate_prices(prices, "2023-07-10")
        strat = TOMStrategy(TOMConfig(hold_days=3))
        positions = strat.compute_target_portfolio(truncated, capital=8_333, as_of=date(2023, 7, 10))
        assert positions == []

    def test_rs_spy_holds_through_month(self, monkeypatch):
        """rs_spy should keep returning the same portfolio mid-month (stateless hold)."""
        prices = _make_synthetic_prices(n_days=500, n_stocks=30)
        tickers = [t for t in prices.keys() if t != "SPY"]
        _patch_universe_loader(monkeypatch, tickers)

        truncated = self._truncate_prices(prices, "2023-07-14")  # mid July
        strat = RSSpyStrategy()
        positions = strat.compute_target_portfolio(truncated, capital=8_333, as_of=date(2023, 7, 14))
        assert len(positions) == 10  # still 5 long + 5 short from the June month-end rebal
