#!/usr/bin/env python3
"""T3-B1 - US sector long/short rotation research batch.

Builds equal-weight sector baskets from the local US stock universe and trades
relative strength long/short between the strongest and weakest sectors.

Outputs:
  - docs/research/wf_reports/T3B-01_us_sector_ls.md
  - output/research/wf_reports/T3B-01_scorecards.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.portfolio_marginal_score import score_candidate  # noqa: E402

META_PATH = ROOT / "data" / "us_stocks" / "_metadata.csv"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T3B-01_us_sector_ls.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T3B-01_scorecards.json"

CAPITAL_PER_LEG = 1_000.0
RT_COST_PCT = 0.0010


def load_sector_return_matrix() -> pd.DataFrame:
    meta = pd.read_csv(META_PATH)
    meta = meta[(meta["pass_all"] == True) & meta["sector"].notna()].copy()  # noqa: E712

    sector_map = meta.groupby("sector")["ticker"].apply(list).to_dict()
    sector_returns: dict[str, pd.Series] = {}

    for sector, tickers in sector_map.items():
        series = []
        for ticker in tickers:
            path = ROOT / "data" / "us_stocks" / f"{ticker}.parquet"
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
                index=idx,
                name=ticker,
            ).sort_index()
            series.append(close.pct_change().rename(ticker))

        if series:
            mat = pd.concat(series, axis=1)
            sector_returns[sector] = mat.mean(axis=1).rename(sector)

    sector_df = pd.DataFrame(sector_returns).sort_index().dropna(how="all").fillna(0.0)
    return sector_df


def variant_sector_ls(
    sector_returns: pd.DataFrame,
    lookback: int,
    hold_days: int,
    label: str,
) -> pd.Series:
    momentum = (1.0 + sector_returns).rolling(lookback).apply(np.prod, raw=True) - 1.0
    positions = {sector: 0.0 for sector in sector_returns.columns}
    last_rebalance = None
    pnl_rows = []

    for dt in sector_returns.index:
        if pd.isna(momentum.loc[dt]).all():
            pnl_rows.append(0.0)
            continue

        do_rebalance = last_rebalance is None or (dt - last_rebalance).days >= hold_days
        cost = 0.0
        if do_rebalance:
            ranks = momentum.loc[dt].sort_values(ascending=False)
            new_positions = {sector: 0.0 for sector in sector_returns.columns}
            new_positions[ranks.index[0]] = 1.0
            new_positions[ranks.index[-1]] = -1.0
            turnover = sum(abs(new_positions[sector] - positions[sector]) for sector in positions)
            cost = turnover * CAPITAL_PER_LEG * RT_COST_PCT
            positions = new_positions
            last_rebalance = dt

        day_pnl = (
            sum(positions[sector] * sector_returns.loc[dt, sector] for sector in sector_returns.columns)
            * CAPITAL_PER_LEG
        )
        pnl_rows.append(day_pnl - cost)

    pnl = pd.Series(pnl_rows, index=sector_returns.index, name=label).astype(float)
    return pnl


def build_variants(sector_returns: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "us_sector_ls_20_5": variant_sector_ls(sector_returns, 20, 5, "us_sector_ls_20_5"),
        "us_sector_ls_40_5": variant_sector_ls(sector_returns, 40, 5, "us_sector_ls_40_5"),
        "us_sector_ls_40_10": variant_sector_ls(sector_returns, 40, 10, "us_sector_ls_40_10"),
    }


def _standalone_stats(pnl: pd.Series) -> dict:
    active = int((pnl != 0).sum())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() != 0 else 0.0
    eq = 10_000.0 + pnl.cumsum()
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    return {
        "active_days": active,
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe,
        "max_dd_pct": dd * 100,
    }


def main() -> int:
    print("=== T3-B1 : US sector long/short ===")
    sector_returns = load_sector_return_matrix()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(sector_returns)
    scorecards = []
    rows = []
    for name, pnl in variants.items():
        stats = _standalone_stats(pnl)
        sc = score_candidate(name, pnl, baseline, 10_000.0, 1.0)
        scorecards.append(sc.to_dict())
        rows.append((name, stats, sc))
        print(
            f"{name}: total=${stats['total_pnl']:+,.0f} sharpe={stats['sharpe']:+.2f} "
            f"[{sc.verdict}] score={sc.marginal_score:+.3f}"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(scorecards, indent=2, default=str), encoding="utf-8")

    md = [
        "# T3-B1 - US sector long/short rotation",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Universe** : {sector_returns.shape[1]} sectors, {sector_returns.shape[0]} daily observations",
        f"**Sizing** : ${CAPITAL_PER_LEG:,.0f} per leg, {RT_COST_PCT * 100:.2f}% RT cost",
        "",
        "## Thesis",
        "",
        "- sector leadership rotates slower than single-stock noise",
        "- a long/short sector sleeve is a cleaner US market-neutral candidate than raw single-name PEAD",
        "",
        "## Variants",
        "",
        "| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, stats, sc in rows:
        md.append(
            f"| `{name}` | {stats['active_days']} | ${stats['total_pnl']:+,.0f} | "
            f"{stats['sharpe']:+.2f} | {stats['max_dd_pct']:.1f}% | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | {sc.delta_maxdd:+.2f}pp | "
            f"{sc.corr_to_portfolio:+.2f} |"
        )

    best = max(rows, key=lambda row: row[2].marginal_score)
    md += [
        "",
        "## Best candidate",
        "",
        f"- `{best[0]}`",
        f"- Verdict : **{best[2].verdict}**",
        f"- Marginal score : {best[2].marginal_score:+.3f}",
        f"- Delta Sharpe : {best[2].delta_sharpe:+.3f}",
        f"- Delta MaxDD : {best[2].delta_maxdd:+.2f}pp",
        f"- Corr to portfolio : {best[2].corr_to_portfolio:+.3f}",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
