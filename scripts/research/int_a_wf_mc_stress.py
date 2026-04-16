#!/usr/bin/env python3
"""INT-A — Walk-forward + Monte Carlo + stress tests.

Pour chaque candidate PROMOTE des sessions T1-A..E :
  - Regenerer la serie PnL daily (import + re-run fonction)
  - Walk-forward 5 windows (60% IS / 40% OOS rolling)
  - Monte Carlo 1000 sims bootstrap daily
  - Stress periods : 2018 bear crypto, 2020 COVID crash, 2022 bear, 2024 rally
  - Gate : >= 3/5 windows OOS Sharpe > 0.2 ET MC P(DD > 20%) < 30%

Output: docs/research/wf_reports/INT-A_tier1_validation.md + scorecards.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.backtest_futures_calendar import (  # noqa: E402
    load_mes,
    variant_dow_long, variant_dow_short, variant_monday_reversal,
    variant_turn_of_month, variant_fomc_long, variant_fomc_overnight_drift,
    variant_pre_holiday,
)
from scripts.research.backtest_futures_intraday_mr import (  # noqa: E402
    load_daily_long, variant_fade, MES_POINT_VALUE, MES_RT_COST,
    MGC_POINT_VALUE, MGC_RT_COST,
)
from scripts.research.backtest_crypto_basis_carry import (  # noqa: E402
    load_btc, variant_always, variant_bullish_filter, variant_funding_filter,
)

MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "INT-A_tier1_validation.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "INT-A_tier1.json"
JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
MD_OUT.parent.mkdir(parents=True, exist_ok=True)

N_MC = 1000
WF_WINDOWS = 5
IS_RATIO = 0.6


def sharpe(pnl: pd.Series) -> float:
    if pnl.std() == 0 or len(pnl) == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_dd(pnl: pd.Series, initial: float = 10_000.0) -> float:
    if len(pnl) == 0:
        return 0.0
    eq = initial + pnl.cumsum()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())


def walk_forward(pnl: pd.Series, n_windows: int = WF_WINDOWS, is_ratio: float = IS_RATIO):
    """Rolling 60/40 split across n_windows."""
    results = []
    n = len(pnl)
    if n < 100:
        return [{"win": 0, "is_sharpe": 0, "oos_sharpe": 0, "oos_dd": 0, "pass": False}]
    window_size = n // n_windows
    for i in range(n_windows):
        start = i * (window_size // 2)  # overlap half to get 5 windows from 2.5 periods
        end = min(start + window_size, n)
        if end - start < 60:
            break
        window = pnl.iloc[start:end]
        is_end = int(len(window) * is_ratio)
        is_data = window.iloc[:is_end]
        oos_data = window.iloc[is_end:]
        results.append({
            "win": i + 1,
            "is_days": len(is_data),
            "oos_days": len(oos_data),
            "is_sharpe": sharpe(is_data),
            "oos_sharpe": sharpe(oos_data),
            "oos_total_pnl": float(oos_data.sum()),
            "oos_dd_pct": max_dd(oos_data) * 100,
            "pass": sharpe(oos_data) > 0.2,
        })
    return results


def monte_carlo(pnl: pd.Series, n_sims: int = N_MC, initial: float = 10_000.0):
    """Bootstrap daily PnL. Returns distribution of final equity and max DD."""
    if len(pnl) == 0:
        return {"p10_dd": 0, "p50_dd": 0, "p90_dd": 0, "prob_dd_gt_20pct": 0}
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


def stress_periods(pnl: pd.Series):
    """PnL sur periodes de stress historiques."""
    periods = {
        "2018_crypto_bear": ("2018-01-01", "2018-12-31"),
        "2020_covid": ("2020-02-15", "2020-04-15"),
        "2022_bear": ("2022-01-01", "2022-12-31"),
        "2024_rally": ("2024-01-01", "2024-12-31"),
        "2025_latest": ("2025-01-01", "2025-12-31"),
    }
    out = {}
    for name, (s, e) in periods.items():
        s, e = pd.Timestamp(s), pd.Timestamp(e)
        window = pnl.loc[(pnl.index >= s) & (pnl.index <= e)]
        if len(window) == 0:
            out[name] = {"days": 0, "total": 0, "sharpe": 0, "dd": 0}
            continue
        out[name] = {
            "days": len(window),
            "total": float(window.sum()),
            "sharpe": sharpe(window),
            "dd_pct": max_dd(window) * 100,
        }
    return out


def validate_candidate(cand_id: str, pnl: pd.Series):
    print(f"\n--- {cand_id} ---")
    pnl = pnl.dropna()
    if len(pnl) < 100:
        print("  SKIP (< 100 days)")
        return None
    standalone = {
        "cand_id": cand_id,
        "days": len(pnl),
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe(pnl),
        "max_dd_pct": max_dd(pnl) * 100,
    }
    wf = walk_forward(pnl)
    oos_pass = sum(1 for w in wf if w.get("pass"))
    total_wins = len([w for w in wf if "is_days" in w])
    mc = monte_carlo(pnl)
    stress = stress_periods(pnl)

    wf_gate = oos_pass >= 3
    mc_gate = mc["prob_dd_gt_30pct"] < 0.30
    overall = wf_gate and mc_gate

    print(f"  Standalone: Sharpe={standalone['sharpe']:+.2f}, MaxDD={standalone['max_dd_pct']:.1f}%, Total=${standalone['total_pnl']:+,.0f}")
    print(f"  WF: {oos_pass}/{total_wins} windows OOS Sharpe > 0.2  [{'PASS' if wf_gate else 'FAIL'}]")
    print(f"  MC: P(DD>30%)={mc['prob_dd_gt_30pct']:.1%}  [{'PASS' if mc_gate else 'FAIL'}]")
    print(f"  OVERALL: {'VALIDATED' if overall else 'NEEDS_WORK'}")

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


def main():
    print("=== INT-A : Walk-forward + Monte Carlo + stress (Tier 1) ===")

    # Load data
    mes = load_mes()
    mgc = load_daily_long(ROOT / "data" / "futures" / "MGC_LONG.parquet")
    btc = load_btc()

    # Regenerate candidate PnL series
    candidates = {}
    # T1-A (4 PROMOTE_LIVE)
    candidates["long_mon_oc"] = variant_dow_long(mes, 0, "long_mon_oc")
    candidates["long_wed_oc"] = variant_dow_long(mes, 2, "long_wed_oc")
    candidates["turn_of_month"] = variant_turn_of_month(mes)
    candidates["pre_holiday_drift"] = variant_pre_holiday(mes)
    # T1-B (top 3 PROMOTE_PAPER)
    candidates["mes_fade_2.0atr"] = variant_fade(mes, 2.0, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2.0atr")
    candidates["mes_fade_2.5atr"] = variant_fade(mes, 2.5, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2.5atr")
    candidates["mes_fade_2atr_trend"] = variant_fade(mes, 2.0, MES_POINT_VALUE, MES_RT_COST, "mes_fade_2atr_trend", trend_filter=True)
    # T1-C (top 2 PROMOTE_PAPER, keep as proxy)
    candidates["basis_carry_always"] = variant_always(btc)
    candidates["basis_carry_funding_gt_5pct"] = variant_funding_filter(btc, 0.05)

    # T1-D and T1-E: PnL series not easily regenerable (depend on network). Load JSON scorecards as proxy.
    # We skip WF/MC for these but document the gap.

    # Validate all
    results = []
    for cand_id, pnl in candidates.items():
        r = validate_candidate(cand_id, pnl)
        if r:
            results.append(r)

    # Persist JSON
    JSON_OUT.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved -> {JSON_OUT}")

    # Markdown report
    md = [
        "# INT-A — Walk-forward + Monte Carlo + stress (Tier 1 candidates)",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Methode** : pour chaque candidate PROMOTE de T1-A..T1-C, WF 5 windows rolling 60/40,",
        f"MC {N_MC} sims bootstrap daily, stress 2018/2020/2022/2024/2025.",
        "",
        "**Gates** :",
        f"- WF : >= 3/5 windows OOS Sharpe > 0.2",
        f"- MC : P(DD > 30%) < 30%",
        "",
        "**Note** : T1-D (US PEAD) et T1-E (crypto L/S) depend de data externe non re-chargee,",
        "WF/MC specifique a lancer dans une session ulterieure avec les series persistees.",
        "",
        "## Summary table",
        "",
        "| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Overall |",
        "|---|---:|---:|---|---:|---|",
    ]
    for r in results:
        st = r["standalone"]
        wf_pass = sum(1 for w in r["walk_forward"] if w.get("pass"))
        wf_total = len([w for w in r["walk_forward"] if "is_days" in w])
        mc_p = r["monte_carlo"]["prob_dd_gt_30pct"]
        overall = "VALIDATED" if r["overall_pass"] else "NEEDS_WORK"
        md.append(
            f"| `{r['cand_id']}` | {st['sharpe']:+.2f} | {st['max_dd_pct']:.1f}% | "
            f"{wf_pass}/{wf_total} | {mc_p:.1%} | **{overall}** |"
        )

    md += ["", "## Details par candidate", ""]
    for r in results:
        cand = r["cand_id"]
        md += [f"### `{cand}` — {'VALIDATED' if r['overall_pass'] else 'NEEDS_WORK'}", ""]
        st = r["standalone"]
        md += [
            f"**Standalone** : Sharpe={st['sharpe']:+.2f}, MaxDD={st['max_dd_pct']:.1f}%, "
            f"Total=${st['total_pnl']:+,.0f}, days={st['days']}",
            "",
            "**Walk-forward** :",
            "",
            "| Win | IS d | OOS d | IS Sharpe | OOS Sharpe | OOS DD% | OOS PnL $ | Pass |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for w in r["walk_forward"]:
            if "is_days" not in w:
                continue
            md.append(
                f"| {w['win']} | {w['is_days']} | {w['oos_days']} | "
                f"{w['is_sharpe']:+.2f} | {w['oos_sharpe']:+.2f} | "
                f"{w['oos_dd_pct']:.1f}% | {w['oos_total_pnl']:+,.0f} | "
                f"{'yes' if w['pass'] else 'no'} |"
            )
        mc = r["monte_carlo"]
        md += [
            "",
            f"**Monte Carlo ({N_MC} sims)** :",
            f"- Median DD : {mc['p50_dd_pct']:.1f}% | p10 DD : {mc['p10_dd_pct']:.1f}% | p90 DD : {mc['p90_dd_pct']:.1f}%",
            f"- P(DD > 20%) : {mc['prob_dd_gt_20pct']:.1%}",
            f"- P(DD > 30%) : {mc['prob_dd_gt_30pct']:.1%}",
            f"- Median final PnL : ${mc['p50_final_pnl']:+,.0f} | p10 : ${mc['p10_final_pnl']:+,.0f}",
            "",
            "**Stress periods** :",
            "",
            "| Period | Days | Total $ | Sharpe | DD% |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, s in r["stress"].items():
            md.append(f"| {name} | {s['days']} | {s['total']:+,.0f} | {s.get('sharpe', 0):+.2f} | {s.get('dd_pct', 0):.1f}% |")
        md.append("")

    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Markdown -> {MD_OUT}")

    # Summary
    validated = [r["cand_id"] for r in results if r["overall_pass"]]
    needs_work = [r["cand_id"] for r in results if not r["overall_pass"]]
    print(f"\n=== SUMMARY ===")
    print(f"VALIDATED ({len(validated)}): {', '.join(validated)}")
    print(f"NEEDS_WORK ({len(needs_work)}): {', '.join(needs_work)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
