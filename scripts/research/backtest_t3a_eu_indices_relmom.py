#!/usr/bin/env python3
"""T3-A3 - EU country-index relative momentum research batch.

Research-only paper proxy for a long/short relative-strength sleeve across:
  - DAX
  - CAC40
  - ESTX50
  - MIB

Outputs:
  - docs/research/wf_reports/T3A-03_eu_indices_relmom.md
  - output/research/wf_reports/T3A-03_scorecards.json
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

BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T3A-03_eu_indices_relmom.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T3A-03_scorecards.json"

UNIVERSE = ["DAX", "CAC40", "ESTX50", "MIB"]
CAPITAL_PER_LEG = 1_000.0
RT_COST_PCT = 0.0010


def load_eu_closes() -> pd.DataFrame:
    closes = {}
    for symbol in UNIVERSE:
        path = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
        df = pd.read_parquet(path)
        idx = pd.to_datetime(df.index)
        try:
            idx = idx.tz_localize(None)
        except TypeError:
            pass
        closes[symbol] = pd.Series(df["close"].values, index=idx.normalize(), name=symbol)
    px = pd.DataFrame(closes).sort_index().ffill().dropna()
    return px


def variant_relative_momentum(
    px: pd.DataFrame,
    lookback: int,
    hold_days: int,
    mode: str,
    label: str,
) -> pd.Series:
    returns = px.pct_change().fillna(0.0)
    momentum = px.pct_change(lookback)
    positions = {sym: 0.0 for sym in UNIVERSE}
    last_rebalance = None
    pnl_rows = []

    for dt in px.index:
        if pd.isna(momentum.loc[dt]).all():
            pnl_rows.append(0.0)
            continue

        do_rebalance = last_rebalance is None or (dt - last_rebalance).days >= hold_days
        cost = 0.0
        if do_rebalance:
            ranks = momentum.loc[dt].sort_values(ascending=False)
            new_positions = {sym: 0.0 for sym in UNIVERSE}
            if mode == "1v1":
                new_positions[ranks.index[0]] = 1.0
                new_positions[ranks.index[-1]] = -1.0
            else:
                for sym in ranks.index[:2]:
                    new_positions[sym] = 1.0
                for sym in ranks.index[-2:]:
                    new_positions[sym] = -1.0
            turnover = sum(abs(new_positions[sym] - positions[sym]) for sym in UNIVERSE)
            cost = turnover * CAPITAL_PER_LEG * RT_COST_PCT
            positions = new_positions
            last_rebalance = dt

        day_pnl = sum(positions[sym] * returns.loc[dt, sym] for sym in UNIVERSE) * CAPITAL_PER_LEG
        pnl_rows.append(day_pnl - cost)

    pnl = pd.Series(pnl_rows, index=px.index, name=label).astype(float)
    return pnl


def build_variants(px: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "eu_relmom_40_3": variant_relative_momentum(px, 40, 3, "1v1", "eu_relmom_40_3"),
        "eu_relmom_80_10_2v2": variant_relative_momentum(
            px, 80, 10, "2v2", "eu_relmom_80_10_2v2"
        ),
        "eu_relmom_20_3": variant_relative_momentum(px, 20, 3, "1v1", "eu_relmom_20_3"),
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
    print("=== T3-A3 : EU indices relative momentum ===")
    px = load_eu_closes()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(px)
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
        "# T3-A3 - EU indices relative momentum",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Data** : {px.index.min().date()} -> {px.index.max().date()} ({len(px)} days)",
        f"**Universe** : {', '.join(UNIVERSE)}",
        f"**Sizing** : ${CAPITAL_PER_LEG:,.0f} per leg, {RT_COST_PCT * 100:.2f}% RT cost proxy",
        "**Execution note** : paper-only research proxy, not a live implementation spec",
        "",
        "## Thesis",
        "",
        "- country-index spreads in Europe can be traded as relative strength rather than outright direction",
        "- the sleeve targets a missing regional relative-value slot without using production routing",
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
