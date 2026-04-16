#!/usr/bin/env python3
"""T2-C — EU sector rotation paneuropeen.

Logique: long top 3 indices EU sur 20j momentum, short bottom 3. Rebalance
monthly.

Data disponibles (data/futures/*_1D.parquet):
  - CAC40 (France), DAX (Germany), ESTX50 (Eurozone), MIB (Italy)

Note: seulement 4 indices, pas vraiment sectoriel mais paneuropeen.
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
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"

INDICES = ["CAC40", "DAX", "ESTX50", "MIB"]
CAPITAL_PER_LEG = 1_500.0
RT_COST_BPS = 10  # IBKR CFD EU ~ 10 bps RT
MOMENTUM_WINDOW = 20
REBALANCE_DAYS = 30


def load(sym: str) -> pd.Series:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df["close"].sort_index()


def main():
    print("=== T2-C : EU sector rotation paneuropeen ===\n")

    closes = {s: load(s) for s in INDICES}
    df = pd.DataFrame(closes).dropna()
    print(f"Common range: {df.index.min().date()} -> {df.index.max().date()} ({len(df)}d)")

    returns = df.pct_change().fillna(0)
    mom = df.pct_change(MOMENTUM_WINDOW)

    positions = {s: 0.0 for s in INDICES}
    last_rebalance = None
    pnl = []
    for i, dt in enumerate(df.index):
        if i < MOMENTUM_WINDOW:
            pnl.append(0.0)
            continue
        do_reb = (last_rebalance is None) or ((dt - last_rebalance).days >= REBALANCE_DAYS)
        if do_reb:
            ranks = mom.loc[dt].sort_values(ascending=False)
            new_pos = {s: 0.0 for s in INDICES}
            # 4 indices: top 2 long, bottom 2 short
            long_n = 2
            short_n = 2
            for s in ranks.head(long_n).index:
                new_pos[s] = 1.0
            for s in ranks.tail(short_n).index:
                new_pos[s] = -1.0
            rebal_cost = sum(abs(new_pos[s] - positions[s]) for s in INDICES) * CAPITAL_PER_LEG * RT_COST_BPS / 10_000
            positions = new_pos
            last_rebalance = dt
        else:
            rebal_cost = 0
        day_pnl = sum(positions[s] * returns.loc[dt, s] for s in INDICES) * CAPITAL_PER_LEG
        pnl.append(day_pnl - rebal_cost)
    pnl_s = pd.Series(pnl, index=df.index, name="eu_sector_rotation")

    total = pnl_s.sum()
    sharpe = pnl_s.mean() / pnl_s.std() * np.sqrt(252) if pnl_s.std() > 0 else 0
    print(f"Standalone: total=${total:+,.0f}, Sharpe={sharpe:+.2f}")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    try:
        sc = score_candidate("eu_sector_rotation", pnl_s, baseline, 10_000.0, 1.0)
        print(f"[{sc.verdict}] score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
              f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")
    except Exception as e:
        print(f"ERR: {e}")
        sc = None

    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    if sc:
        (JSON_OUT_DIR / "T2-03_scorecards.json").write_text(
            json.dumps([sc.to_dict()], indent=2, default=str))
    md = [
        "# T2-C — EU sector rotation (paneuropeen)",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Univers** : {', '.join(INDICES)}",
        f"**Methodologie** : top 2 long, bottom 2 short sur 20d momentum, rebalance monthly",
        f"**Data range** : {df.index.min().date()} -> {df.index.max().date()} ({len(df)}d)",
        "",
        "## Results",
        "",
        f"- Total PnL : ${total:+,.0f}",
        f"- Sharpe : {sharpe:+.2f}",
    ]
    if sc:
        md += [
            f"- Verdict : **{sc.verdict}**",
            f"- Score : {sc.marginal_score:+.3f}, dSharpe {sc.delta_sharpe:+.3f}, "
            f"dMaxDD {sc.delta_maxdd:+.2f}pp, corr {sc.corr_to_portfolio:+.2f}",
            f"- Penalties : {', '.join(sc.penalties) if sc.penalties else '-'}",
        ]
    (MD_OUT_DIR / "T2-03_eu_sector_rotation.md").write_text("\n".join(md), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
