"""Tests us_sector_ls_40_5 — paper-only T3-B1 validated strategy."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies_v2.us.us_sector_ls import (
    DEFAULT_HOLD_DAYS,
    DEFAULT_LOOKBACK,
    SectorLSPositions,
    compute_momentum,
    select_new_positions,
    tick,
)


def _make_sector_returns(n_days: int = 100, n_sectors: int = 5, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B")
    sectors = [f"Sector_{i}" for i in range(n_sectors)]
    # Different drifts to ensure clear ranking
    drifts = np.linspace(-0.001, 0.001, n_sectors)
    data = {}
    for sec, drift in zip(sectors, drifts):
        returns = drift + rng.normal(0, 0.01, n_days)
        data[sec] = returns
    return pd.DataFrame(data, index=idx)


class TestComputeMomentum:

    def test_cumulative_return(self):
        returns = _make_sector_returns(n_days=100, n_sectors=3, seed=1)
        momentum = compute_momentum(returns, lookback=40)
        assert momentum.shape == returns.shape
        # First 39 rows should be NaN
        assert momentum.iloc[:39].isna().all().all()
        # Row 40 should not be NaN
        assert momentum.iloc[39].notna().all()


class TestSelectNewPositions:

    def test_picks_top_long_bottom_short(self):
        m = pd.Series([0.05, -0.02, 0.10, -0.08, 0.03], index=["A", "B", "C", "D", "E"])
        positions, long_s, short_s = select_new_positions(m)
        assert long_s == "C"
        assert short_s == "D"
        assert positions["C"] == 1.0
        assert positions["D"] == -1.0
        assert positions["A"] == 0.0
        assert positions["B"] == 0.0
        assert positions["E"] == 0.0

    def test_raises_on_insufficient_sectors(self):
        m = pd.Series([0.01], index=["A"])
        with pytest.raises(ValueError):
            select_new_positions(m)

    def test_handles_ties(self):
        """2 sectors avec meme momentum -> ordre deterministe (pandas sort stable)."""
        m = pd.Series([0.05, 0.05, -0.03], index=["A", "B", "C"])
        positions, long_s, short_s = select_new_positions(m)
        # Long = first of the 2 tied highest
        assert long_s in ("A", "B")
        assert short_s == "C"


class TestTick:

    def test_init_rebalance_on_first_tick(self):
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        state = SectorLSPositions()
        as_of = returns.index[50]  # enough history past lookback=40
        new_state, result = tick(state, as_of, returns, lookback=40, hold_days=5)
        assert result.action == "init"
        assert result.long_sector is not None
        assert result.short_sector is not None
        assert new_state.last_rebalance == as_of
        assert abs(sum(abs(v) for v in new_state.positions.values())) == 2.0  # 1 long + 1 short

    def test_hold_between_rebalances(self):
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        state = SectorLSPositions()
        # First tick: init on day 50
        state, _ = tick(state, returns.index[50], returns, lookback=40, hold_days=5)
        # Next day: hold
        new_state, result = tick(state, returns.index[51], returns, lookback=40, hold_days=5)
        assert result.action == "hold"
        assert result.long_sector is None
        assert result.short_sector is None
        assert new_state.positions == state.positions
        assert new_state.last_rebalance == state.last_rebalance

    def test_rebalance_after_hold_days(self):
        returns = _make_sector_returns(n_days=70, n_sectors=5, seed=1)
        state = SectorLSPositions()
        state, _ = tick(state, returns.index[50], returns, lookback=40, hold_days=5)
        # +5 business days should trigger rebalance
        state, result = tick(state, returns.index[55], returns, lookback=40, hold_days=5)
        assert result.action == "rebalance"
        assert state.last_rebalance == returns.index[55]

    def test_pnl_computation_long_sector_positive_return(self):
        """Long sector returns X% -> pnl ~= X% * capital_per_leg (+ short side)."""
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        # Force as_of day: sector momentum predetermined by seed
        state = SectorLSPositions()
        as_of = returns.index[45]
        state, result = tick(state, as_of, returns, lookback=40, hold_days=5,
                             capital_per_leg=1_000.0, rt_cost_pct=0.0)
        # Day PnL = long_ret * 1000 + short_ret * -1000
        long_ret = float(returns.loc[as_of, result.long_sector])
        short_ret = float(returns.loc[as_of, result.short_sector])
        expected_pnl = (long_ret - short_ret) * 1_000.0
        assert pytest.approx(result.day_pnl_usd, rel=1e-6) == expected_pnl

    def test_turnover_cost_on_rebalance(self):
        """Rebalance = turnover cost > 0."""
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        state = SectorLSPositions()
        as_of = returns.index[50]
        state, result = tick(state, as_of, returns, lookback=40, hold_days=5,
                             capital_per_leg=1_000.0, rt_cost_pct=0.0010)
        # Init: turnover = 2 (full long + full short from zero) -> cost = 2*1000*0.001 = $2
        assert result.turnover_cost_usd == pytest.approx(2.0, rel=1e-6)

    def test_no_cost_on_hold(self):
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        state = SectorLSPositions()
        state, _ = tick(state, returns.index[50], returns, lookback=40, hold_days=5)
        state, result = tick(state, returns.index[51], returns, lookback=40, hold_days=5)
        assert result.turnover_cost_usd == 0.0

    def test_hold_if_insufficient_history(self):
        """Before lookback+1 bars -> hold (no rebalance, no position)."""
        returns = _make_sector_returns(n_days=100, n_sectors=5, seed=1)
        state = SectorLSPositions()
        as_of = returns.index[10]  # well before lookback=40
        new_state, result = tick(state, as_of, returns, lookback=40, hold_days=5)
        assert result.action == "hold"
        assert new_state.positions == {} or all(v == 0.0 for v in new_state.positions.values())

    def test_raises_if_as_of_not_in_index(self):
        returns = _make_sector_returns(n_days=60, n_sectors=5, seed=1)
        state = SectorLSPositions()
        future = pd.Timestamp("2099-01-01")
        with pytest.raises(ValueError):
            tick(state, future, returns, lookback=40, hold_days=5)
