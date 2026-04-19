#!/usr/bin/env python3
"""INT-B - Walk-forward and Monte Carlo validation for discovery batch T3-A.

Validates the strongest research candidates from:
  - T3-A1 MCL overnight drift
  - T3-A2 MES -> BTC Asia lead-lag
  - T3-A3 EU indices relative momentum

Outputs:
  - docs/research/wf_reports/INT-B_discovery_batch.md
  - output/research/wf_reports/INT-B_discovery_batch.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.backtest_t3a_mcl_overnight import load_mcl, variant_mon_trend  # noqa: E402
from scripts.research.backtest_t3a_mes_btc_leadlag import (  # noqa: E402
    build_daily_dataset,
    variant_threshold,
)
from scripts.research.backtest_t3a_eu_indices_relmom import (  # noqa: E402
    load_eu_closes,
    variant_relative_momentum,
)

MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "INT-B_discovery_batch.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "INT-B_discovery_batch.json"
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
    if n < 100:
        return [{"win": 0, "is_sharpe": 0.0, "oos_sharpe": 0.0, "pass": False}]
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
    if len(pnl) == 0:
        return {"prob_dd_gt_30pct": 0.0}
    arr = pnl.values
    n = len(arr)
    dds = []
    final_pnls = []
    for _ in range(n_sims):
        sample = np.random.choice(arr, size=n, replace=True)
        eq = initial + np.cumsum(sample)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        dds.append(dd.min())
        final_pnls.append(eq[-1] - initial)
    dds = np.array(dds)
    return {
        "p10_dd_pct": float(np.percentile(dds, 10) * 100),
        "p50_dd_pct": float(np.percentile(dds, 50) * 100),
        "p90_dd_pct": float(np.percentile(dds, 90) * 100),
        "prob_dd_gt_20pct": float(np.mean(dds < -0.20)),
        "prob_dd_gt_30pct": float(np.mean(dds < -0.30)),
        "p10_final_pnl": float(np.percentile(final_pnls, 10)),
        "p50_final_pnl": float(np.percentile(final_pnls, 50)),
    }


def stress_periods(pnl: pd.Series) -> dict:
    periods = {
        "2018_bear": ("2018-01-01", "2018-12-31"),
        "2020_covid": ("2020-02-15", "2020-04-15"),
        "2022_bear": ("2022-01-01", "2022-12-31"),
        "2024_rally": ("2024-01-01", "2024-12-31"),
        "2025_latest": ("2025-01-01", "2025-12-31"),
    }
    out = {}
    for name, (start, end) in periods.items():
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        window = pnl.loc[(pnl.index >= s) & (pnl.index <= e)]
        if len(window) == 0:
            out[name] = {"days": 0, "total": 0.0, "sharpe": 0.0, "dd_pct": 0.0}
            continue
        out[name] = {
            "days": len(window),
            "total": float(window.sum()),
            "sharpe": sharpe(window),
            "dd_pct": max_dd(window) * 100,
        }
    return out


def validate_candidate(cand_id: str, pnl: pd.Series) -> dict:
    pnl = pnl.dropna()
    standalone = {
        "cand_id": cand_id,
        "days": len(pnl),
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe(pnl),
        "max_dd_pct": max_dd(pnl) * 100,
    }
    wf = walk_forward(pnl)
    wf_pass = sum(1 for w in wf if w.get("pass"))
    mc = monte_carlo(pnl)
    stress = stress_periods(pnl)
    wf_gate = wf_pass >= 3
    mc_gate = mc["prob_dd_gt_30pct"] < 0.30
    overall = wf_gate and mc_gate
    return {
        "cand_id": cand_id,
        "standalone": standalone,
        "walk_forward": wf,
        "monte_carlo": mc,
        "stress": stress,
        "wf_gate_pass": wf_gate,
        "mc_gate_pass": mc_gate,
        "overall_pass": overall,
    }


def main() -> int:
    print("=== INT-B : Discovery batch WF/MC ===")

    mcl = load_mcl()
    mcl_pnl = variant_mon_trend(mcl, 10, "mcl_overnight_mon_trend10")

    leadlag_daily = build_daily_dataset()
    btc_pnl = variant_threshold(
        leadlag_daily,
        signal_quantile=0.70,
        vol_quantile=0.80,
        mode="both",
        label="btc_asia_mes_leadlag_q70_v80",
    )

    eu_px = load_eu_closes()
    eu_pnl = variant_relative_momentum(eu_px, 40, 3, "1v1", "eu_relmom_40_3")

    candidates = {
        "mcl_overnight_mon_trend10": mcl_pnl,
        "btc_asia_mes_leadlag_q70_v80": btc_pnl,
        "eu_relmom_40_3": eu_pnl,
    }

    results = []
    for cand_id, pnl in candidates.items():
        result = validate_candidate(cand_id, pnl)
        results.append(result)
        print(
            f"{cand_id}: sharpe={result['standalone']['sharpe']:+.2f} "
            f"WF={sum(1 for w in result['walk_forward'] if w.get('pass'))}/{len(result['walk_forward'])} "
            f"MC30={result['monte_carlo']['prob_dd_gt_30pct']:.1%} "
            f"[{'VALIDATED' if result['overall_pass'] else 'NEEDS_WORK'}]"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    md = [
        "# INT-B - Discovery batch validation",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        "**Scope** : validation of the strongest T3-A research candidates only",
        "",
        "## Gates",
        "",
        "- walk-forward: at least 3/5 OOS windows with Sharpe > 0.2",
        "- Monte Carlo: P(DD > 30%) < 30%",
        "",
        "## Summary",
        "",
        "| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Overall |",
        "|---|---:|---:|---|---:|---|",
    ]
    for result in results:
        st = result["standalone"]
        wf_pass = sum(1 for w in result["walk_forward"] if w.get("pass"))
        wf_total = len(result["walk_forward"])
        mc_p = result["monte_carlo"]["prob_dd_gt_30pct"]
        overall = "VALIDATED" if result["overall_pass"] else "NEEDS_WORK"
        md.append(
            f"| `{result['cand_id']}` | {st['sharpe']:+.2f} | {st['max_dd_pct']:.1f}% | "
            f"{wf_pass}/{wf_total} | {mc_p:.1%} | **{overall}** |"
        )

    md += ["", "## Details", ""]
    for result in results:
        cand = result["cand_id"]
        st = result["standalone"]
        md += [
            f"### `{cand}` - {'VALIDATED' if result['overall_pass'] else 'NEEDS_WORK'}",
            "",
            f"**Standalone** : Sharpe={st['sharpe']:+.2f}, MaxDD={st['max_dd_pct']:.1f}%, "
            f"Total=${st['total_pnl']:+,.0f}, days={st['days']}",
            "",
            "**Walk-forward**",
            "",
            "| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for win in result["walk_forward"]:
            if "is_days" not in win:
                continue
            md.append(
                f"| {win['win']} | {win['is_days']} | {win['oos_days']} | "
                f"{win['is_sharpe']:+.2f} | {win['oos_sharpe']:+.2f} | "
                f"{win['oos_dd_pct']:.1f}% | {win['oos_total_pnl']:+,.0f} | "
                f"{'yes' if win['pass'] else 'no'} |"
            )
        mc = result["monte_carlo"]
        md += [
            "",
            "**Monte Carlo**",
            "",
            f"- Median DD : {mc['p50_dd_pct']:.1f}%",
            f"- P(DD > 20%) : {mc['prob_dd_gt_20pct']:.1%}",
            f"- P(DD > 30%) : {mc['prob_dd_gt_30pct']:.1%}",
            f"- Median final PnL : ${mc['p50_final_pnl']:+,.0f}",
            "",
        ]

    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
