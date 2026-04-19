#!/usr/bin/env python3
"""INT-D - Walk-forward and bull/bear validation for crypto discovery batch.

Validates the strongest crypto candidates from:
  - T4-A1 BTC range harvest rebuild
  - T4-A2 cross-sectional relative-strength sleeves

Outputs:
  - docs/research/wf_reports/INT-D_crypto_batch.md
  - output/research/wf_reports/INT-D_crypto_batch.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.backtest_t4_crypto_range_harvest import load_btc_4h, run_range_harvest  # noqa: E402
from scripts.research.backtest_t4_crypto_relative_strength import (  # noqa: E402
    build_variants,
    load_close_series,
)

MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "INT-D_crypto_batch.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "INT-D_crypto_batch.json"
WF_WINDOWS = 5
IS_RATIO = 0.60
N_MC = 1000


def sharpe(pnl: pd.Series) -> float:
    pnl = pnl.dropna()
    if len(pnl) == 0 or pnl.std() == 0:
        return 0.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_dd(pnl: pd.Series, initial: float = 10_000.0) -> float:
    pnl = pnl.dropna()
    if len(pnl) == 0:
        return 0.0
    eq = initial + pnl.cumsum()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())


def walk_forward(pnl: pd.Series, n_windows: int = WF_WINDOWS, is_ratio: float = IS_RATIO) -> list[dict]:
    pnl = pnl.dropna()
    results = []
    n = len(pnl)
    if n < 150:
        return [{"win": 0, "is_sharpe": 0.0, "oos_sharpe": 0.0, "pass": False}]
    window_size = n // n_windows
    for i in range(n_windows):
        start = i * (window_size // 2)
        end = min(start + window_size, n)
        if end - start < 90:
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
                "oos_dd_pct": max_dd(oos_data) * 100.0,
                "pass": sharpe(oos_data) > 0.2 and float(oos_data.sum()) > 0.0,
            }
        )
    return results


def monte_carlo(pnl: pd.Series, n_sims: int = N_MC, initial: float = 10_000.0) -> dict:
    pnl = pnl.dropna()
    if len(pnl) == 0:
        return {"prob_dd_gt_30pct": 0.0}
    arr = pnl.values
    n = len(arr)
    dds = []
    finals = []
    for _ in range(n_sims):
        sample = np.random.choice(arr, size=n, replace=True)
        eq = initial + np.cumsum(sample)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        dds.append(dd.min())
        finals.append(eq[-1] - initial)
    dds = np.array(dds)
    return {
        "p10_dd_pct": float(np.percentile(dds, 10) * 100.0),
        "p50_dd_pct": float(np.percentile(dds, 50) * 100.0),
        "p90_dd_pct": float(np.percentile(dds, 90) * 100.0),
        "prob_dd_gt_20pct": float(np.mean(dds < -0.20)),
        "prob_dd_gt_30pct": float(np.mean(dds < -0.30)),
        "p10_final_pnl": float(np.percentile(finals, 10)),
        "p50_final_pnl": float(np.percentile(finals, 50)),
    }


def bull_bear_breakdown(pnl: pd.Series, btc_close: pd.Series, lookback: int = 60) -> dict[str, dict]:
    btc_close = btc_close.copy()
    btc_close.index = pd.to_datetime(btc_close.index).normalize()
    regime = btc_close.pct_change(lookback)
    aligned = pnl.reindex(regime.index).fillna(0.0)

    masks = {
        "bull": regime > 0,
        "bear": regime <= 0,
    }
    out: dict[str, dict] = {}
    for name, mask in masks.items():
        window = aligned.loc[mask.fillna(False)]
        out[name] = {
            "days": int(len(window)),
            "total_pnl": float(window.sum()),
            "sharpe": sharpe(window),
            "max_dd_pct": max_dd(window) * 100.0,
        }
    return out


def validate_candidate(cand_id: str, pnl: pd.Series, btc_close: pd.Series) -> dict:
    pnl = pnl.dropna()
    standalone = {
        "cand_id": cand_id,
        "days": len(pnl),
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe(pnl),
        "max_dd_pct": max_dd(pnl) * 100.0,
    }
    wf = walk_forward(pnl)
    wf_pass = sum(1 for win in wf if win.get("pass"))
    mc = monte_carlo(pnl)
    regime = bull_bear_breakdown(pnl, btc_close)
    bull_bear_gate = regime["bull"]["total_pnl"] > 0 and regime["bear"]["total_pnl"] >= 0
    wf_gate = wf_pass >= 3
    mc_gate = mc["prob_dd_gt_30pct"] < 0.30
    overall = wf_gate and mc_gate and bull_bear_gate
    return {
        "cand_id": cand_id,
        "standalone": standalone,
        "walk_forward": wf,
        "monte_carlo": mc,
        "regime": regime,
        "wf_gate_pass": wf_gate,
        "mc_gate_pass": mc_gate,
        "bull_bear_gate_pass": bull_bear_gate,
        "overall_pass": overall,
    }


def main() -> int:
    print("=== INT-D : Crypto batch WF/MC/bull-bear ===")

    btc_4h = load_btc_4h()
    range_pnl = run_range_harvest(
        btc_4h,
        bb_period=20,
        adx_max=20,
        sl_mult=1.5,
        max_hold_bars=18,
        label="range_bb_harvest_rebuild",
    )
    range_bb30_pnl = run_range_harvest(
        btc_4h,
        bb_period=30,
        adx_max=20,
        sl_mult=1.5,
        max_hold_bars=18,
        label="range_bb_harvest_bb30",
    )

    rel_variants, _ = build_variants()
    btc_daily = load_close_series("BTC")

    candidates = {
        "range_bb_harvest_rebuild": range_pnl,
        "range_bb_harvest_bb30": range_bb30_pnl,
        "crypto_ls_20_7_3": rel_variants["crypto_ls_20_7_3"],
        "crypto_ls_20_7_2": rel_variants["crypto_ls_20_7_2"],
        "alt_rel_strength_14_60_7": rel_variants["alt_rel_strength_14_60_7"],
        "alt_rel_strength_14_90_7": rel_variants["alt_rel_strength_14_90_7"],
    }

    results = []
    for cand_id, pnl in candidates.items():
        result = validate_candidate(cand_id, pnl, btc_daily)
        results.append(result)
        wf_pass = sum(1 for win in result["walk_forward"] if win.get("pass"))
        print(
            f"{cand_id}: sharpe={result['standalone']['sharpe']:+.2f} "
            f"WF={wf_pass}/{len(result['walk_forward'])} "
            f"MC30={result['monte_carlo']['prob_dd_gt_30pct']:.1%} "
            f"bull=${result['regime']['bull']['total_pnl']:+,.0f} "
            f"bear=${result['regime']['bear']['total_pnl']:+,.0f} "
            f"[{'VALIDATED' if result['overall_pass'] else 'NEEDS_WORK'}]"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    md = [
        "# INT-D - Crypto discovery batch validation",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        "**Scope** : validation of crypto sleeves meant to survive both bull and bear tapes",
        "",
        "## Gates",
        "",
        "- walk-forward: at least 3/5 OOS windows with positive OOS PnL and Sharpe > 0.2",
        "- Monte Carlo: P(DD > 30%) < 30%",
        "- bull/bear robustness: positive bull total PnL and non-negative bear total PnL",
        "",
        "## Summary",
        "",
        "| Candidate | Sharpe | MaxDD | WF OOS pass | MC P(DD>30%) | Bull PnL | Bear PnL | Overall |",
        "|---|---:|---:|---|---:|---:|---:|---|",
    ]
    for result in results:
        st = result["standalone"]
        wf_pass = sum(1 for win in result["walk_forward"] if win.get("pass"))
        wf_total = len(result["walk_forward"])
        mc_p = result["monte_carlo"]["prob_dd_gt_30pct"]
        bull = result["regime"]["bull"]["total_pnl"]
        bear = result["regime"]["bear"]["total_pnl"]
        overall = "VALIDATED" if result["overall_pass"] else "NEEDS_WORK"
        md.append(
            f"| `{result['cand_id']}` | {st['sharpe']:+.2f} | {st['max_dd_pct']:.1f}% | "
            f"{wf_pass}/{wf_total} | {mc_p:.1%} | ${bull:+,.0f} | ${bear:+,.0f} | **{overall}** |"
        )

    md += ["", "## Details", ""]
    for result in results:
        cand = result["cand_id"]
        st = result["standalone"]
        reg = result["regime"]
        md += [
            f"### `{cand}` - {'VALIDATED' if result['overall_pass'] else 'NEEDS_WORK'}",
            "",
            f"**Standalone** : Sharpe={st['sharpe']:+.2f}, MaxDD={st['max_dd_pct']:.1f}%, Total=${st['total_pnl']:+,.0f}, days={st['days']}",
            "",
            f"**Bull regime** : days={reg['bull']['days']}, total=${reg['bull']['total_pnl']:+,.0f}, sharpe={reg['bull']['sharpe']:+.2f}, maxDD={reg['bull']['max_dd_pct']:.1f}%",
            f"**Bear regime** : days={reg['bear']['days']}, total=${reg['bear']['total_pnl']:+,.0f}, sharpe={reg['bear']['sharpe']:+.2f}, maxDD={reg['bear']['max_dd_pct']:.1f}%",
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
                f"| {win['win']} | {win['is_days']} | {win['oos_days']} | {win['is_sharpe']:+.2f} | "
                f"{win['oos_sharpe']:+.2f} | {win['oos_dd_pct']:.1f}% | {win['oos_total_pnl']:+,.0f} | "
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
