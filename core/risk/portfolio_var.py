"""Portfolio risk-if-stopped under correlation.

The legacy futures risk gate sums `(entry - SL) * mult * qty` across positions
and rejects a new signal when the additive total exceeds the daily risk budget.
That is mathematically the worst case where every SL fires simultaneously, i.e.
implicit `rho = 1` between all instruments. For a portfolio of MGC (gold) plus
MNQ (Nasdaq) — historically near-zero correlation — that overstates the true
downside materially.

This module provides a correlation-aware aggregator:

  portfolio_var = sqrt(L' R L)

where `L` is the vector of per-position max losses (in $) and `R` is the
correlation matrix of daily returns over a recent lookback. When the matrix is
unavailable (missing parquet, insufficient history, NaN), callers receive
``None`` and must fall back to the additive sum so the platform never relaxes
risk based on stale or missing data.

Design constraints:
  - Pure stdlib + numpy + pandas (already in the project deps).
  - No network calls; reads `data/futures/{SYM}_1D.parquet` only.
  - Deterministic and cacheable (one read per cycle is enough).
  - Callers retain the additive number for logging and comparison.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET_DIR = ROOT / "data" / "futures"
DEFAULT_LOOKBACK_DAYS = 60
MIN_OBSERVATIONS = 30


@dataclass(frozen=True)
class PositionRisk:
    """Per-leg max loss expressed in $ at the worst-case stop fill."""
    symbol: str
    risk_usd: float


def _load_daily_returns(
    symbol: str,
    parquet_dir: Path,
    lookback_days: int,
) -> np.ndarray | None:
    """Return last ``lookback_days`` log-returns for ``symbol`` or None."""
    path = parquet_dir / f"{symbol}_1D.parquet"
    if not path.exists():
        logger.debug("portfolio_var: %s 1D parquet missing at %s", symbol, path)
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("portfolio_var: %s parquet read failed: %s", symbol, exc)
        return None

    close_col = next(
        (c for c in ("close", "Close", "adj_close", "Adj Close") if c in df.columns),
        None,
    )
    if close_col is None:
        logger.warning("portfolio_var: %s parquet has no close column (%s)", symbol, list(df.columns))
        return None

    closes = df[close_col].astype(float).dropna().to_numpy()
    if closes.size < MIN_OBSERVATIONS + 1:
        logger.debug("portfolio_var: %s only %d closes, below MIN_OBSERVATIONS", symbol, closes.size)
        return None

    closes = closes[-(lookback_days + 1):]
    rets = np.diff(np.log(closes))
    if rets.size < MIN_OBSERVATIONS:
        return None
    return rets


def correlation_matrix(
    symbols: Iterable[str],
    *,
    parquet_dir: Path = DEFAULT_PARQUET_DIR,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[list[str], np.ndarray] | None:
    """Compute the correlation matrix of daily returns for ``symbols``.

    Returns ``(symbols_in_order, R)`` or ``None`` if any symbol cannot be
    loaded or the alignment leaves fewer than ``MIN_OBSERVATIONS`` joint days.
    """
    syms = [s.upper() for s in symbols]
    series: dict[str, np.ndarray] = {}
    for sym in syms:
        rets = _load_daily_returns(sym, parquet_dir, lookback_days)
        if rets is None:
            return None
        series[sym] = rets

    n = min(arr.size for arr in series.values())
    if n < MIN_OBSERVATIONS:
        return None

    matrix = np.vstack([series[s][-n:] for s in syms])
    if not np.all(np.isfinite(matrix)):
        return None
    if np.any(matrix.std(axis=1) == 0):
        return None

    corr = np.corrcoef(matrix)
    if not np.all(np.isfinite(corr)):
        return None
    np.fill_diagonal(corr, 1.0)
    return syms, corr


def portfolio_risk_var(
    positions: list[PositionRisk],
    *,
    parquet_dir: Path = DEFAULT_PARQUET_DIR,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    correlation_override: dict[tuple[str, str], float] | None = None,
) -> float | None:
    """Return correlation-aware portfolio risk-if-stopped, or ``None``.

    ``L = [risk_usd_i]`` are treated as comparable-quantile losses (each leg's
    SL is roughly the same probabilistic event). The portfolio worst-case at
    that quantile is ``sqrt(L^T R L)``. With ``R = I`` (no correlation) this
    collapses to the L2 norm; with ``R = 1`` (perfect correlation) it returns
    the additive sum.

    Returns ``None`` when the correlation matrix is unavailable so callers can
    fall back to the conservative additive total instead of inventing risk.
    """
    if not positions:
        return 0.0
    if len(positions) == 1:
        return float(max(0.0, positions[0].risk_usd))

    risks = np.array([max(0.0, p.risk_usd) for p in positions], dtype=float)
    syms = [p.symbol for p in positions]

    if correlation_override is not None:
        n = len(syms)
        corr = np.eye(n)
        for i, si in enumerate(syms):
            for j, sj in enumerate(syms):
                if i == j:
                    continue
                rho = correlation_override.get((si, sj))
                if rho is None:
                    rho = correlation_override.get((sj, si))
                if rho is None:
                    return None
                corr[i, j] = float(rho)
    else:
        loaded = correlation_matrix(
            syms, parquet_dir=parquet_dir, lookback_days=lookback_days
        )
        if loaded is None:
            return None
        _, corr = loaded

    variance = float(risks @ corr @ risks)
    if variance < 0:
        # Numerical noise on near-singular matrices; clamp.
        variance = 0.0
    return float(np.sqrt(variance))


def naive_risk_sum(positions: list[PositionRisk]) -> float:
    """Additive worst-case (legacy semantics, equivalent to rho=1)."""
    return float(sum(max(0.0, p.risk_usd) for p in positions))
