#!/usr/bin/env python3
"""INT-C - Validation batch for T3-B US strategies.

Validates the most promising US sector long/short candidate from T3-B1.
PEAD market-neutral variants are not promoted to WF because they already fail
the hard portfolio gates at the scorecard stage.

Outputs:
  - docs/research/wf_reports/INT-C_us_batch.md
  - output/research/wf_reports/INT-C_us_batch.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.backtest_t3b_us_sector_ls import (  # noqa: E402
    load_sector_return_matrix,
    variant_sector_ls,
)

MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "INT-C_us_batch.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "INT-C_us_batch.json"
WF_WINDOWS = 5
IS_RATIO = 0.60
N_MC = 1000


def sharpe(pnl: pd.Series) -> float:
    if len(pnl) == 0 or pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_dd(pnl: pd.Series, initial: float = 10_000.0) -> float:
    if len(pnl) == 0:
        return 0.0
    eq = initial + pnl.cumsum()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())


def walk_forward(pnl: pd.Series, n_windows: int = WF_WINDOWS, is_ratio: float = IS_RATIO) -> list[dict]:
    results = []
    n = len(pnl)
    window_size = n // n_windows
    for i in range(n_windows):
        start = i * (window_size // 2)
        end = min(start + window_size, n)
        if end - start < 60:
            break
        window = pnl.iloc[start:end]
        is_end = int(len(window) * is_ratio)
        is_data = window.iloc[:is_end]
        oos_data = window.iloc[is_end:]
        results.append(
            {
                "win": i + 1,
                "is_days": len(is_data),
                "oos_days": len(oos_data),
                "is_sharpe": sharpe(is_data),
                "oos_sharpe": sharpe(oos_data),
                "oos_total_pnl": float(oos_data.sum()),
                "oos_dd_pct": max_dd(oos_data) * 100,
                "pass": sharpe(oos_data) > 0.2,
            }
        )
    return results


def monte_carlo(pnl: pd.Series, n_sims: int = N_MC, initial: float = 10_000.0) -> dict:
    arr = pnl.values
    dds = []
    finals = []
    for _ in range(n_sims):
        sample = np.random.choice(arr, size=len(arr), replace=True)
        eq = initial + np.cumsum(sample)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        dds.append(dd.min())
        finals.append(eq[-1] - initial)
    dds = np.array(dds)
    return {
        "p10_dd_pct": float(np.percentile(dds, 10) * 100),
        "p50_dd_pct": float(np.percentile(dds, 50) * 100),
        "p90_dd_pct": float(np.percentile(dds, 90) * 100),
        "prob_dd_gt_20pct": float(np.mean(dds < -0.20)),
        "prob_dd_gt_30pct": float(np.mean(dds < -0.30)),
        "p10_final_pnl": float(np.percentile(finals, 10)),
        "p50_final_pnl": float(np.percentile(finals, 50)),
    }


def main() -> int:
    print("=== INT-C : US sector long/short validation ===")
    sector_returns = load_sector_return_matrix()
    pnl = variant_sector_ls(sector_returns, 40, 5, "us_sector_ls_40_5")

    wf = walk_forward(pnl)
    mc = monte_carlo(pnl)
    wf_pass = sum(1 for w in wf if w.get("pass"))
    overall = wf_pass >= 3 and mc["prob_dd_gt_30pct"] < 0.30

    result = {
        "cand_id": "us_sector_ls_40_5",
        "standalone": {
            "days": len(pnl),
            "total_pnl": float(pnl.sum()),
            "sharpe": sharpe(pnl),
            "max_dd_pct": max_dd(pnl) * 100,
        },
        "walk_forward": wf,
        "monte_carlo": mc,
        "wf_gate_pass": wf_pass >= 3,
        "mc_gate_pass": mc["prob_dd_gt_30pct"] < 0.30,
        "overall_pass": overall,
    }

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps([result], indent=2, default=str), encoding="utf-8")

    st = result["standalone"]
    md = [
        "# INT-C - US sector long/short validation",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Summary",
        "",
        f"- Candidate : `us_sector_ls_40_5`",
        f"- Standalone Sharpe : {st['sharpe']:+.2f}",
        f"- Standalone MaxDD : {st['max_dd_pct']:.1f}%",
        f"- WF OOS pass : {wf_pass}/{len(wf)}",
        f"- MC P(DD>30%) : {mc['prob_dd_gt_30pct']:.1%}",
        f"- Overall : **{'VALIDATED' if overall else 'NEEDS_WORK'}**",
        "",
        "## Walk-forward",
        "",
        "| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for win in wf:
        md.append(
            f"| {win['win']} | {win['is_days']} | {win['oos_days']} | {win['is_sharpe']:+.2f} | "
            f"{win['oos_sharpe']:+.2f} | {win['oos_dd_pct']:.1f}% | {win['oos_total_pnl']:+,.0f} | "
            f"{'yes' if win['pass'] else 'no'} |"
        )
    md += [
        "",
        "## Monte Carlo",
        "",
        f"- Median DD : {mc['p50_dd_pct']:.1f}%",
        f"- P(DD > 20%) : {mc['prob_dd_gt_20pct']:.1%}",
        f"- P(DD > 30%) : {mc['prob_dd_gt_30pct']:.1%}",
        f"- Median final PnL : ${mc['p50_final_pnl']:+,.0f}",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
