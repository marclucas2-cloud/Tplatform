"""US Sector Long/Short 40_5 — paper-only strategy (T3-B1 validated).

Source de validation:
  - scripts/research/backtest_t3b_us_sector_ls.py (1274 days, 11 sectors)
  - docs/research/wf_reports/T3B-01_us_sector_ls.md
  - docs/research/wf_reports/INT-C_us_batch.md
    -> Sharpe +0.39, MaxDD -2.1%, WF 3/5 OOS pass, MC P(DD>30%) 0% -> VALIDATED

Thesis:
  Sector leadership rotates slower than single-stock noise. A long/short
  sector sleeve is a cleaner US market-neutral candidate than raw PEAD.

Logic:
  1. For each day D:
     - Compute sector_returns[D] = mean of ticker returns in each GICS sector
     - Compute sector_momentum[D] = product(1 + returns[D-lookback+1..D]) - 1
  2. Rebalance every `hold_days`:
     - LONG 1.0 leg on top-momentum sector
     - SHORT 1.0 leg on bottom-momentum sector
     - Pay cost = turnover * capital_per_leg * rt_cost_pct
  3. Each non-rebalance day: pnl = sum(position[s] * returns[s] * capital_per_leg)

Variants validated: lookback=40, hold_days=5 -> us_sector_ls_40_5 (best).

Runtime (paper_only):
  - Book alpaca_us (paper_only par doctrine). Pas de live tant que le book
    ne change pas de statut.
  - Log-only retrospective via worker.run_us_sector_ls_paper_cycle():
    tick quotidien apres close US (22h30 Paris ete / 23h30 hiver), compute
    PnL du jour + decision rebalance eventuel, journal JSONL.
  - Pas de broker, pas d'ordre. state file + journal pour audit.

Caveats:
  - US shorts sector-basket: pas trivial a implementer live (PDT rule + short
    borrow cost). Log-only paper evite cette question pour l'instant.
  - `pass_all=True` filter in metadata: 496/503 tickers eligibles 2026-04-18.
  - Sector map: 11 GICS sectors (Industrials, Financials, IT, Health Care,
    Consumer Disc, Consumer Staples, Real Estate, Utilities, Materials,
    Communication Services, Energy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd


DEFAULT_LOOKBACK = 40
DEFAULT_HOLD_DAYS = 5
DEFAULT_CAPITAL_PER_LEG = 1_000.0
DEFAULT_RT_COST_PCT = 0.0010  # 10 bps


@dataclass
class SectorLSPositions:
    """Current sector positions + last rebalance timestamp."""
    positions: dict[str, float] = field(default_factory=dict)
    last_rebalance: pd.Timestamp | None = None


@dataclass(frozen=True)
class SectorLSTickResult:
    """Result of one paper tick: decision + simulated PnL for as_of_date."""
    as_of_date: pd.Timestamp
    action: Literal["rebalance", "hold", "init"]
    positions_before: dict[str, float]
    positions_after: dict[str, float]
    long_sector: str | None
    short_sector: str | None
    day_pnl_usd: float
    turnover_cost_usd: float
    net_pnl_usd: float


def load_sector_return_matrix(
    us_stocks_dir: Path,
    metadata_path: Path,
) -> pd.DataFrame:
    """Build sector-level daily returns (mean of ticker returns per sector).

    Returns DataFrame indexed by date, columns = sectors.
    """
    meta = pd.read_csv(metadata_path)
    meta = meta[(meta["pass_all"] == True) & meta["sector"].notna()].copy()  # noqa: E712
    sector_map = meta.groupby("sector")["ticker"].apply(list).to_dict()

    sector_returns: dict[str, pd.Series] = {}
    for sector, tickers in sector_map.items():
        series = []
        for ticker in tickers:
            path = us_stocks_dir / f"{ticker}.parquet"
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            if isinstance(df.index, pd.RangeIndex):
                if "timestamp" not in df.columns:
                    continue
                idx = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
            else:
                idx = pd.to_datetime(df.index)
                try:
                    idx = idx.tz_localize(None)
                except TypeError:
                    pass
                idx = idx.normalize()
            close = pd.Series(
                df["close"].values if "close" in df.columns else df.iloc[:, 0].values,
                index=idx, name=ticker,
            ).sort_index()
            series.append(close.pct_change().rename(ticker))
        if series:
            mat = pd.concat(series, axis=1)
            sector_returns[sector] = mat.mean(axis=1).rename(sector)
    return pd.DataFrame(sector_returns).sort_index().dropna(how="all").fillna(0.0)


def compute_momentum(sector_returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Rolling lookback-day cumulative return per sector."""
    return (1.0 + sector_returns).rolling(lookback).apply(np.prod, raw=True) - 1.0


def select_new_positions(momentum_today: pd.Series) -> tuple[dict[str, float], str, str]:
    """Pick long top / short bottom sector.

    Returns (positions_dict, long_sector, short_sector).
    """
    ranks = momentum_today.dropna().sort_values(ascending=False)
    if len(ranks) < 2:
        raise ValueError(f"Insufficient sectors for L/S: {len(ranks)}")
    long_sector = ranks.index[0]
    short_sector = ranks.index[-1]
    positions = {sector: 0.0 for sector in momentum_today.index}
    positions[long_sector] = 1.0
    positions[short_sector] = -1.0
    return positions, long_sector, short_sector


def tick(
    state: SectorLSPositions,
    as_of_date: pd.Timestamp,
    sector_returns: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK,
    hold_days: int = DEFAULT_HOLD_DAYS,
    capital_per_leg: float = DEFAULT_CAPITAL_PER_LEG,
    rt_cost_pct: float = DEFAULT_RT_COST_PCT,
) -> tuple[SectorLSPositions, SectorLSTickResult]:
    """Simulate one paper tick at `as_of_date`.

    Uses sector_returns up to and including as_of_date. Rebalance if needed
    (last_rebalance + hold_days <= as_of_date) or init.
    Returns (new_state, tick_result).
    """
    as_of_date = pd.Timestamp(as_of_date).normalize()
    if as_of_date not in sector_returns.index:
        raise ValueError(f"as_of_date {as_of_date.date()} not in sector_returns index")
    # Compute momentum up to as_of_date
    momentum = compute_momentum(sector_returns.loc[:as_of_date], lookback)
    if as_of_date not in momentum.index or momentum.loc[as_of_date].dropna().empty:
        # Not enough history yet
        new_state = SectorLSPositions(positions=dict(state.positions), last_rebalance=state.last_rebalance)
        result = SectorLSTickResult(
            as_of_date=as_of_date,
            action="hold",
            positions_before=dict(state.positions),
            positions_after=dict(state.positions),
            long_sector=None, short_sector=None,
            day_pnl_usd=0.0, turnover_cost_usd=0.0, net_pnl_usd=0.0,
        )
        return new_state, result

    positions_before = dict(state.positions) if state.positions else {s: 0.0 for s in sector_returns.columns}

    # Rebalance decision
    do_rebalance = state.last_rebalance is None
    if not do_rebalance and state.last_rebalance is not None:
        days_since = (as_of_date - state.last_rebalance).days
        do_rebalance = days_since >= hold_days

    long_sector = short_sector = None
    cost = 0.0
    if do_rebalance:
        new_positions, long_sector, short_sector = select_new_positions(momentum.loc[as_of_date])
        turnover = sum(
            abs(new_positions.get(s, 0.0) - positions_before.get(s, 0.0))
            for s in sector_returns.columns
        )
        cost = turnover * capital_per_leg * rt_cost_pct
        positions_after = new_positions
        new_last_rebalance = as_of_date
        action: Literal["rebalance", "init"] = "init" if state.last_rebalance is None else "rebalance"
    else:
        positions_after = positions_before
        new_last_rebalance = state.last_rebalance
        action = "hold"

    # Day PnL using positions_after (the ones we hold during `as_of_date`)
    returns_today = sector_returns.loc[as_of_date]
    day_pnl = float(sum(
        positions_after.get(s, 0.0) * float(returns_today.get(s, 0.0))
        for s in sector_returns.columns
    ) * capital_per_leg)
    net_pnl = day_pnl - cost

    new_state = SectorLSPositions(positions=positions_after, last_rebalance=new_last_rebalance)
    result = SectorLSTickResult(
        as_of_date=as_of_date,
        action=action,
        positions_before=positions_before,
        positions_after=positions_after,
        long_sector=long_sector,
        short_sector=short_sector,
        day_pnl_usd=day_pnl,
        turnover_cost_usd=cost,
        net_pnl_usd=net_pnl,
    )
    return new_state, result
