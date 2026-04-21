"""EU indices relative momentum 40_3 — paper-only strategy (T3-A3 validated).

Source de validation:
  - scripts/research/backtest_t3a_eu_indices_relmom.py (2021-2026, 1346 days)
  - docs/research/wf_reports/T3A-03_eu_indices_relmom.md
  - docs/research/wf_reports/INT-B_discovery_batch.md
    -> Sharpe +0.71, MaxDD -0.8%, WF 4/5 OOS pass, MC P(DD>30%) 0% -> VALIDATED

Thesis:
  Country-index spreads in Europe can be traded as relative strength rather
  than outright direction. Missing regional relative-value slot in the book
  without using production routing (paper-only).

Logic:
  - Universe: DAX, CAC40, ESTX50, MIB (4 indices)
  - Compute 40-day momentum per index
  - Rebalance every 3 days (1v1): LONG top, SHORT bottom
  - Capital per leg: $1,000 paper notional, cost 0.10% RT

Runtime (paper_only):
  - Book ibkr_eu (paper_only par doctrine). Pas de live tant que book change.
  - Reutilise le core generique de strategies_v2.us.us_sector_ls (tick +
    compute_momentum + select_new_positions) avec univers EU indices.
  - Log-only retrospective via worker.run_eu_relmom_paper_cycle().

Caveats:
  - Closes depuis data/futures/{DAX,CAC40,ESTX50,MIB}_1D.parquet (cron yfinance).
  - Tol 48h freshness (cf books_registry.yaml ibkr_eu data_freshness).
  - Shorts indices: non-trivial a implementer live (CFD IBKR ou futures mini).
    Log-only paper evite cette question pour l'instant.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

# Reutilise le core generique (us_sector_ls core fonctionne pour tout univers)
from strategies_v2.us.us_sector_ls import (
    SectorLSPositions as RelMomPositions,
    SectorLSTickResult as RelMomTickResult,
    compute_momentum,
    select_new_positions,
    tick,
)

logger = logging.getLogger(__name__)

EU_UNIVERSE = ["DAX", "CAC40", "ESTX50", "MIB"]
DEFAULT_LOOKBACK = 40
DEFAULT_HOLD_DAYS = 3
DEFAULT_CAPITAL_PER_LEG = 1_000.0
DEFAULT_RT_COST_PCT = 0.0010

_MIN_VALID_DATE = pd.Timestamp("2000-01-01")


def load_eu_returns(data_dir: Path) -> pd.DataFrame:
    """Load EU indices closes from parquet files and compute daily returns.

    Defensif : deduplique l'index (bug observe 2026-04-20 sur ESTX50 ou
    99.9% des dates == 1970-01-01 epoch) et rejette les series dont l'index
    date est manifestement corrompu (< 2000-01-01).

    Args:
        data_dir: directory containing {SYM}_1D.parquet files
    Returns:
        DataFrame indexed by date, columns = EU_UNIVERSE, daily returns.
    """
    closes: dict[str, pd.Series] = {}
    for symbol in EU_UNIVERSE:
        path = data_dir / f"{symbol}_1D.parquet"
        if not path.exists():
            logger.warning(f"eu_relmom: {symbol} parquet missing at {path}")
            continue
        df = pd.read_parquet(path)
        idx = pd.to_datetime(df.index)
        try:
            idx = idx.tz_localize(None)
        except TypeError:
            pass
        idx = idx.normalize()
        series = pd.Series(df["close"].values, index=idx, name=symbol)
        # Deduplicate (keep last = most recent value for same date)
        series = series[~series.index.duplicated(keep="last")]
        # Reject epoch-corrupted data (yfinance parse fail -> all 1970-01-01)
        if len(series) == 0 or series.index.min() < _MIN_VALID_DATE:
            logger.warning(
                f"eu_relmom: {symbol} parquet corrupted "
                f"(min_date={series.index.min() if len(series) else 'empty'} "
                f"< {_MIN_VALID_DATE.date()}) -- skipping"
            )
            continue
        closes[symbol] = series
    if len(closes) < 2:
        raise ValueError(f"Insufficient EU indices loaded: {list(closes.keys())}")
    px = pd.DataFrame(closes).sort_index().ffill().dropna()
    return px.pct_change().fillna(0.0)


# Re-export generic functions for worker/tests convenience
__all__ = [
    "EU_UNIVERSE",
    "DEFAULT_LOOKBACK",
    "DEFAULT_HOLD_DAYS",
    "DEFAULT_CAPITAL_PER_LEG",
    "DEFAULT_RT_COST_PCT",
    "RelMomPositions",
    "RelMomTickResult",
    "load_eu_returns",
    "compute_momentum",
    "select_new_positions",
    "tick",
]
