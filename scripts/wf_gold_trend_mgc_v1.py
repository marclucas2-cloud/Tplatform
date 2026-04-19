#!/usr/bin/env python3
"""Walk-forward validation of gold_trend_mgc V1 (SL 0.4% / TP 0.8%).

B2 iter3 (2026-04-19). Produces machine-readable manifest to unblock
quant_registry.yaml:gold_trend_mgc (remove wf_exempt_reason).

V1 params (recalibration 2026-04-16, docs/research/gold_trend_sl_recalibration.md):
  - Long MGC si close > EMA20
  - SL 0.4% / TP 0.8%
  - Max hold 10 jours
  - Cost 2.49 USD RT (MGC)
  - Multiplier 10 USD/point

Source data:
  data/futures/MGC_1D.parquet

Gates de validation (5 windows, IS 60% / OOS 40%) :
  - >=3/5 OOS windows profitable
  - Mean OOS Sharpe > 0.3
  - Monte Carlo P(DD>30%) < 10%

Output:
  - Console summary + gate verdict
  - data/research/wf_manifests/gold_trend_mgc_v1_YYYY-MM-DD.json

Usage:
    python scripts/wf_gold_trend_mgc_v1.py
    python scripts/wf_gold_trend_mgc_v1.py --mc-sims 5000
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

MGC_PATH = ROOT / "data" / "futures" / "MGC_1D.parquet"
MANIFEST_DIR = ROOT / "data" / "research" / "wf_manifests"

SPEC = {"mult": 10.0, "cost_rt": 2.49}
PARAMS = {
    "ema_period": 20,
    "sl_pct": 0.004,
    "tp_pct": 0.008,
    "max_hold_days": 10,
}
MC_SIMS_DEFAULT = 2000


def load_mgc() -> pd.DataFrame:
    df = pd.read_parquet(MGC_PATH)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def simulate_trade_exit(
    df: pd.DataFrame, entry_idx: int, entry_price: float,
    sl_pct: float, tp_pct: float, max_hold: int,
) -> tuple[int, float]:
    """Return (exit_idx, exit_price). Long only (V1). Intrabar gap check."""
    sl = entry_price * (1 - sl_pct)
    tp = entry_price * (1 + tp_pct)
    for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        o = float(df["open"].iloc[j])
        if j > entry_idx:
            if o <= sl:
                return j, o
            if o >= tp:
                return j, o
        if l <= sl:
            return j, sl
        if h >= tp:
            return j, tp
    end = min(entry_idx + max_hold - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


def run_gold_trend_v1(df: pd.DataFrame) -> list[dict]:
    """Long MGC when close > EMA20. SL 0.4%, TP 0.8%. Max hold 10d."""
    ema = df["close"].ewm(span=PARAMS["ema_period"], adjust=False).mean()
    trades: list[dict] = []
    last_exit_idx = -1
    for i in range(PARAMS["ema_period"] + 1, len(df) - 1):
        if i <= last_exit_idx:
            continue
        close_prev = float(df["close"].iloc[i - 1])
        ema_prev = float(ema.iloc[i - 1])
        if close_prev > ema_prev:
            entry_idx = i
            entry_px = float(df["open"].iloc[i])
            exit_idx, exit_px = simulate_trade_exit(
                df, entry_idx, entry_px,
                PARAMS["sl_pct"], PARAMS["tp_pct"], PARAMS["max_hold_days"],
            )
            pnl = (exit_px - entry_px) * SPEC["mult"] - SPEC["cost_rt"]
            trades.append({
                "entry_date": df.index[entry_idx],
                "exit_date": df.index[exit_idx],
                "entry_px": entry_px,
                "exit_px": exit_px,
                "pnl": pnl,
                "bars_held": exit_idx - entry_idx + 1,
            })
            last_exit_idx = exit_idx
    return trades


def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "sharpe": 0.0, "total": 0.0, "wr": 0.0, "max_dd": 0.0}
    arr = np.array([t["pnl"] for t in trades])
    sharpe = arr.mean() / arr.std() * math.sqrt(252) if arr.std() > 0 else 0.0
    equity = arr.cumsum()
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd_usd = float(dd.min())
    return {
        "n": len(arr),
        "sharpe": round(float(sharpe), 3),
        "total": round(float(arr.sum()), 2),
        "wr": round(float((arr > 0).mean()), 3),
        "max_dd_usd": round(max_dd_usd, 2),
    }


def slice_trades(trades: list[dict], start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    return [t for t in trades if start <= pd.Timestamp(t["exit_date"]) <= end]


def walk_forward(df: pd.DataFrame, n_windows: int = 5, is_frac: float = 0.6) -> tuple[list[dict], list[dict]]:
    all_trades = run_gold_trend_v1(df)
    total_days = len(df)
    window_days = total_days // n_windows
    is_days = int(window_days * is_frac)

    wf_results = []
    for w in range(n_windows):
        is_start = df.index[w * window_days]
        is_end = df.index[w * window_days + is_days - 1]
        oos_start = df.index[w * window_days + is_days]
        oos_end_idx = min(w * window_days + window_days - 1, total_days - 1)
        oos_end = df.index[oos_end_idx]
        is_trades = slice_trades(all_trades, is_start, is_end)
        oos_trades = slice_trades(all_trades, oos_start, oos_end)
        is_s = stats(is_trades)
        oos_s = stats(oos_trades)
        wf_results.append({
            "window": w + 1,
            "is_period": f"{is_start.date()}..{is_end.date()}",
            "oos_period": f"{oos_start.date()}..{oos_end.date()}",
            "is_n": is_s["n"], "is_sharpe": is_s["sharpe"], "is_total": is_s["total"],
            "oos_n": oos_s["n"], "oos_sharpe": oos_s["sharpe"], "oos_total": oos_s["total"],
            "oos_profitable": oos_s["total"] > 0,
        })
    return all_trades, wf_results


def monte_carlo_dd(trades: list[dict], n_sims: int, dd_threshold: float = 0.30) -> dict:
    """MC P(max_dd > threshold * starting equity)."""
    if not trades or n_sims <= 0:
        return {"n_sims": 0, "p_dd_gt_30": 0.0, "p_dd_gt_40": 0.0}
    pnls = np.array([t["pnl"] for t in trades])
    starting_equity = 10000.0
    rng = np.random.default_rng(42)
    dd_ratios = np.empty(n_sims)
    for s in range(n_sims):
        shuffled = rng.permutation(pnls)
        equity = starting_equity + shuffled.cumsum()
        peak = np.maximum.accumulate(equity)
        dd_ratio = ((peak - equity) / peak).max()
        dd_ratios[s] = dd_ratio
    return {
        "n_sims": n_sims,
        "p_dd_gt_30": round(float((dd_ratios > 0.30).mean()), 4),
        "p_dd_gt_40": round(float((dd_ratios > 0.40).mean()), 4),
        "p_dd_gt_50": round(float((dd_ratios > 0.50).mean()), 4),
        "median_dd": round(float(np.median(dd_ratios)), 4),
    }


def deflated_sharpe_pvalue(all_trades: list[dict], n_trials: int = 1) -> float:
    """Very simple DSR approximation (Bailey+LdP 2014).

    For V1 only 1 config tested -> proche du Sharpe nominal. Renvoie p-value
    pour H0: Sharpe < 0.
    """
    if len(all_trades) < 10:
        return 1.0
    pnls = np.array([t["pnl"] for t in all_trades])
    mean_ret = pnls.mean()
    std_ret = pnls.std(ddof=1)
    if std_ret == 0:
        return 1.0
    sharpe = mean_ret / std_ret * math.sqrt(252)
    n = len(pnls)
    # Approx p-value from t-stat: t = sharpe * sqrt(n/252)
    t_stat = sharpe * math.sqrt(n / 252)
    # One-sided p-value
    from math import erf
    p_value = 0.5 * (1 - erf(t_stat / math.sqrt(2)))
    return round(float(p_value), 6)


def compute_grade(oos_wins: int, mean_oos_sharpe: float, mc_p_dd_30: float) -> str:
    """S / A / B / REJECTED per promotion doctrine."""
    if oos_wins < 3 or mean_oos_sharpe < 0.3:
        return "REJECTED"
    if mc_p_dd_30 > 0.10:
        return "REJECTED"
    if oos_wins == 5 and mean_oos_sharpe > 1.0 and mc_p_dd_30 < 0.02:
        return "S"
    if oos_wins >= 4 and mean_oos_sharpe > 0.7 and mc_p_dd_30 < 0.05:
        return "A"
    return "B"


def write_manifest(
    all_trades: list[dict],
    wf_results: list[dict],
    mc: dict,
    summary: dict,
    run_id: str,
) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest_path = MANIFEST_DIR / f"gold_trend_mgc_v1_{date_str}.json"

    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "strategy_id": "gold_trend_mgc",
        "variant": "v1_sl04_tp08",
        "source": "scripts/wf_gold_trend_mgc_v1.py",
        "source_data": str(MGC_PATH.relative_to(ROOT)),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "params": PARAMS,
        "spec": SPEC,
        "windows": wf_results,
        "monte_carlo": mc,
        "summary": summary,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return manifest_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mc-sims", type=int, default=MC_SIMS_DEFAULT)
    ap.add_argument("--n-windows", type=int, default=5)
    ap.add_argument("--is-frac", type=float, default=0.6)
    args = ap.parse_args()

    if not MGC_PATH.exists():
        print(f"[err] MGC data missing: {MGC_PATH}")
        return 2

    df = load_mgc()
    print(f"MGC rows: {len(df)}, first={df.index.min().date()}, last={df.index.max().date()}")

    all_trades, wf_results = walk_forward(df, n_windows=args.n_windows, is_frac=args.is_frac)
    all_stats = stats(all_trades)

    oos_positive = sum(r["oos_profitable"] for r in wf_results)
    oos_sharpes = [r["oos_sharpe"] for r in wf_results if r["oos_n"] >= 3]
    mean_oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
    total_oos_pnl = sum(r["oos_total"] for r in wf_results)

    mc = monte_carlo_dd(all_trades, n_sims=args.mc_sims)
    dsr_p = deflated_sharpe_pvalue(all_trades, n_trials=1)
    grade = compute_grade(oos_positive, mean_oos_sharpe, mc["p_dd_gt_30"])
    verdict = "VALIDATED" if grade != "REJECTED" else "REJECTED"

    summary = {
        "all_trades": all_stats,
        "windows_pass": oos_positive,
        "windows_total": len(wf_results),
        "pass_rate": round(oos_positive / len(wf_results), 2) if wf_results else 0.0,
        "mean_oos_sharpe": round(mean_oos_sharpe, 3),
        "total_oos_pnl_usd": round(total_oos_pnl, 2),
        "dsr_pvalue": dsr_p,
        "mc_p_dd_gt_30": mc["p_dd_gt_30"],
        "grade": grade,
        "verdict": verdict,
    }

    print("\n=== WF gold_trend_mgc V1 (SL 0.4% / TP 0.8%, EMA 20) ===")
    print(pd.DataFrame(wf_results).to_string(index=False))
    print(f"\nAll trades: {all_stats}")
    print(f"\nOOS profitable: {oos_positive}/{len(wf_results)}")
    print(f"Mean OOS Sharpe: {mean_oos_sharpe:.3f}")
    print(f"Total OOS PnL: ${total_oos_pnl:,.0f}")
    print(f"MC P(DD>30%): {mc['p_dd_gt_30']:.2%}")
    print(f"MC median DD: {mc['median_dd']:.2%}")
    print(f"DSR p-value : {dsr_p:.4f}")
    print(f"Grade       : {grade}")
    print(f"Verdict     : {verdict}")

    run_id = f"wf_gold_trend_mgc_v1_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    manifest = write_manifest(all_trades, wf_results, mc, summary, run_id)
    print(f"\nManifest: {manifest.relative_to(ROOT)}")

    return 0 if verdict == "VALIDATED" else 1


if __name__ == "__main__":
    sys.exit(main())
