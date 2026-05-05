"""Tests for the correlation-aware portfolio risk aggregator.

The previous futures risk gate added every leg's risk-if-stopped, which is
the worst-case rho=1 scenario. The new helper allows the gate to recognise
that decorrelated legs do not all stop on the same day, while still failing
closed (returning None) when correlation data is missing or stale.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.risk.portfolio_var import (
    PositionRisk,
    correlation_matrix,
    naive_risk_sum,
    portfolio_risk_var,
)


def _write_parquet(path: Path, returns: np.ndarray, *, start: float = 100.0) -> None:
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * math.exp(r))
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame({"close": closes}, index=dates)
    df.to_parquet(path)


@pytest.fixture
def synthetic_parquet_dir(tmp_path: Path) -> Path:
    rng = np.random.default_rng(seed=42)
    n = 200
    # Two independent return series — population corr should be near 0
    a = rng.normal(0, 0.01, size=n)
    b = rng.normal(0, 0.012, size=n)
    # Third series perfectly anti-correlated to A
    c = -a
    _write_parquet(tmp_path / "AAA_1D.parquet", a)
    _write_parquet(tmp_path / "BBB_1D.parquet", b)
    _write_parquet(tmp_path / "CCC_1D.parquet", c)
    return tmp_path


# ---------------------------------------------------------------------------
# Naive sum
# ---------------------------------------------------------------------------

class TestNaiveRiskSum:
    def test_sum_matches_simple_addition(self):
        positions = [
            PositionRisk("MGC", 915.0),
            PositionRisk("MNQ", 1665.0),
        ]
        assert naive_risk_sum(positions) == pytest.approx(2580.0)

    def test_clamps_negative_legs_to_zero(self):
        positions = [
            PositionRisk("MGC", 500.0),
            PositionRisk("MNQ", -200.0),  # invariant violation
        ]
        assert naive_risk_sum(positions) == pytest.approx(500.0)

    def test_empty_returns_zero(self):
        assert naive_risk_sum([]) == 0.0


# ---------------------------------------------------------------------------
# Correlation matrix loader
# ---------------------------------------------------------------------------

class TestCorrelationMatrix:
    def test_returns_symbols_in_input_order(self, synthetic_parquet_dir: Path):
        result = correlation_matrix(["AAA", "BBB"], parquet_dir=synthetic_parquet_dir)
        assert result is not None
        syms, matrix = result
        assert syms == ["AAA", "BBB"]
        assert matrix.shape == (2, 2)
        assert matrix[0, 0] == pytest.approx(1.0)
        assert matrix[1, 1] == pytest.approx(1.0)

    def test_uppercases_symbols(self, synthetic_parquet_dir: Path):
        result = correlation_matrix(["aaa", "bbb"], parquet_dir=synthetic_parquet_dir)
        assert result is not None
        syms, _ = result
        assert syms == ["AAA", "BBB"]

    def test_independent_series_have_low_correlation(self, synthetic_parquet_dir: Path):
        # Default lookback (60) on 200 IID returns yields a noisy sample corr;
        # widen the window so the estimate is closer to its zero mean.
        _, matrix = correlation_matrix(
            ["AAA", "BBB"], parquet_dir=synthetic_parquet_dir, lookback_days=180
        )
        assert abs(matrix[0, 1]) < 0.2

    def test_anti_correlated_series_returns_minus_one(self, synthetic_parquet_dir: Path):
        _, matrix = correlation_matrix(["AAA", "CCC"], parquet_dir=synthetic_parquet_dir)
        assert matrix[0, 1] == pytest.approx(-1.0)

    def test_missing_parquet_returns_none(self, synthetic_parquet_dir: Path):
        assert correlation_matrix(
            ["AAA", "ZZZ"], parquet_dir=synthetic_parquet_dir
        ) is None

    def test_short_history_returns_none(self, tmp_path: Path):
        rng = np.random.default_rng(seed=1)
        _write_parquet(tmp_path / "SHORT_1D.parquet", rng.normal(0, 0.01, size=10))
        _write_parquet(tmp_path / "OTHER_1D.parquet", rng.normal(0, 0.01, size=10))
        assert correlation_matrix(
            ["SHORT", "OTHER"], parquet_dir=tmp_path
        ) is None


# ---------------------------------------------------------------------------
# Portfolio VaR aggregation
# ---------------------------------------------------------------------------

class TestPortfolioRiskVar:
    def test_perfect_correlation_collapses_to_sum(self):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("BBB", 500.0),
        ]
        var = portfolio_risk_var(
            positions,
            correlation_override={("AAA", "BBB"): 1.0},
        )
        assert var == pytest.approx(1500.0, rel=1e-9)

    def test_zero_correlation_gives_l2_norm(self):
        positions = [
            PositionRisk("AAA", 900.0),
            PositionRisk("BBB", 1200.0),
        ]
        var = portfolio_risk_var(
            positions,
            correlation_override={("AAA", "BBB"): 0.0},
        )
        expected = math.hypot(900.0, 1200.0)
        assert var == pytest.approx(expected, rel=1e-9)
        # The reproduction of the operator scenario: MGC+MNQ-style
        positions2 = [PositionRisk("MGC", 915.0), PositionRisk("MNQ", 1665.0)]
        var2 = portfolio_risk_var(
            positions2, correlation_override={("MGC", "MNQ"): 0.0}
        )
        # ~$1900 vs naive $2580
        assert var2 < naive_risk_sum(positions2)
        assert var2 == pytest.approx(math.hypot(915.0, 1665.0), rel=1e-9)

    def test_negative_correlation_reduces_total_risk(self):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("BBB", 1000.0),
        ]
        var = portfolio_risk_var(
            positions,
            correlation_override={("AAA", "BBB"): -0.5},
        )
        # sqrt(1e6 + 1e6 - 1e6) = 1000
        assert var == pytest.approx(1000.0, rel=1e-9)

    def test_single_leg_returns_its_own_risk(self):
        var = portfolio_risk_var([PositionRisk("AAA", 750.0)])
        assert var == pytest.approx(750.0)

    def test_empty_returns_zero(self):
        assert portfolio_risk_var([]) == 0.0

    def test_missing_data_returns_none_for_safe_fallback(self, tmp_path: Path):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("ZZZ", 500.0),  # parquet absent
        ]
        assert portfolio_risk_var(positions, parquet_dir=tmp_path) is None

    def test_uses_real_parquet_when_no_override(self, synthetic_parquet_dir: Path):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("CCC", 1000.0),  # built as -AAA
        ]
        var = portfolio_risk_var(positions, parquet_dir=synthetic_parquet_dir)
        # Anti-correlated legs should net to roughly zero risk
        assert var is not None
        assert var < 50.0

    def test_negative_leg_clamped(self):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("BBB", -250.0),
        ]
        var = portfolio_risk_var(
            positions, correlation_override={("AAA", "BBB"): 0.0}
        )
        # Negative leg ignored: only AAA contributes
        assert var == pytest.approx(1000.0, rel=1e-9)

    def test_partial_override_returns_none(self):
        positions = [
            PositionRisk("AAA", 1000.0),
            PositionRisk("BBB", 500.0),
            PositionRisk("CCC", 750.0),
        ]
        # Missing the (AAA, CCC) and (BBB, CCC) pairs entirely
        var = portfolio_risk_var(
            positions, correlation_override={("AAA", "BBB"): 0.0}
        )
        assert var is None
