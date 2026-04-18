"""Tests eu_relmom_40_3 — paper-only T3-A3 validated strategy.

Verifie la reutilisation du core generique via wrapper EU + le load EU specific.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies_v2.eu.eu_relmom import (
    DEFAULT_HOLD_DAYS,
    DEFAULT_LOOKBACK,
    EU_UNIVERSE,
    RelMomPositions,
    load_eu_returns,
    tick,
)


class TestModuleConstants:

    def test_universe_is_4_indices(self):
        assert set(EU_UNIVERSE) == {"DAX", "CAC40", "ESTX50", "MIB"}

    def test_defaults_match_validated_variant(self):
        """VALIDATED = 40_3 per T3-A3 report."""
        assert DEFAULT_LOOKBACK == 40
        assert DEFAULT_HOLD_DAYS == 3


class TestLoadEuReturns:

    def test_raises_if_no_parquet_found(self, tmp_path):
        # Empty dir -> no parquet files
        with pytest.raises(ValueError):
            load_eu_returns(tmp_path)

    def test_loads_real_data_if_available(self):
        data_dir = ROOT / "data" / "futures"
        # Skip if data not present (CI without fixtures)
        for sym in EU_UNIVERSE:
            if not (data_dir / f"{sym}_1D.parquet").exists():
                pytest.skip(f"{sym}_1D.parquet missing in test env")
        returns = load_eu_returns(data_dir)
        assert set(returns.columns) == set(EU_UNIVERSE)
        assert len(returns) > 100
        # Daily returns should be roughly in [-0.1, 0.1] range (normal market)
        assert returns.abs().max().max() < 0.5


class TestMomentumFormulaEquivalence:
    """Verifie que compute_momentum sur les returns donne le meme resultat
    que px.pct_change(lookback) du backtest (review N2 concern R1)."""

    def test_compound_returns_equals_pct_change_prices(self):
        """(1+r).rolling(N).prod - 1 === px.pct_change(N) sur data sans gaps.

        Le backtest calcule `momentum = px.pct_change(lookback)` sur les prix.
        Le runtime via tick() calcule `(1+returns).rolling(lookback).prod - 1`
        sur les returns. Mathematiquement identiques sur data continue.
        """
        from strategies_v2.us.us_sector_ls import compute_momentum

        rng = np.random.RandomState(42)
        n = 100
        px = pd.Series(100.0 * np.cumprod(1 + rng.normal(0.0005, 0.01, n)))
        returns = px.pct_change().fillna(0.0)

        mom_backtest = px.pct_change(40)
        mom_runtime = compute_momentum(returns.to_frame("X"), 40)["X"]

        diff = (mom_runtime - mom_backtest).abs().dropna()
        # Floating point noise only
        assert diff.max() < 1e-10, f"max diff {diff.max()} exceeds FP noise"


class TestTickReusesCore:
    """Verifie que tick() avec params EU fonctionne correctement."""

    def _make_eu_returns(self, n_days: int = 100, seed: int = 42) -> pd.DataFrame:
        rng = np.random.RandomState(seed)
        idx = pd.date_range("2024-01-01", periods=n_days, freq="B")
        data = {}
        drifts = {"DAX": 0.001, "CAC40": 0.0005, "ESTX50": 0.0003, "MIB": -0.0002}
        for sym, drift in drifts.items():
            data[sym] = drift + rng.normal(0, 0.008, n_days)
        return pd.DataFrame(data, index=idx)

    def test_init_rebalance(self):
        returns = self._make_eu_returns(n_days=60)
        state = RelMomPositions()
        as_of = returns.index[45]
        new_state, result = tick(state, as_of, returns,
                                 lookback=DEFAULT_LOOKBACK, hold_days=DEFAULT_HOLD_DAYS)
        assert result.action == "init"
        assert result.long_sector in EU_UNIVERSE
        assert result.short_sector in EU_UNIVERSE
        assert result.long_sector != result.short_sector

    def test_hold_days_3_not_5(self):
        """eu_relmom rebalance tous les 3 jours (pas 5 comme us_sector_ls)."""
        returns = self._make_eu_returns(n_days=60)
        state = RelMomPositions()
        # Init on day 45
        state, _ = tick(state, returns.index[45], returns,
                        lookback=DEFAULT_LOOKBACK, hold_days=DEFAULT_HOLD_DAYS)
        # +3 business days = rebalance
        state, result = tick(state, returns.index[48], returns,
                             lookback=DEFAULT_LOOKBACK, hold_days=DEFAULT_HOLD_DAYS)
        assert result.action == "rebalance"
