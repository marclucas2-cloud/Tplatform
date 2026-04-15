#!/usr/bin/env python3
"""Walk-forward validation on best MES V2 combo (SL=60/TP=120/EMA=50).

V2 sweep found this combo Sharpe 0.60 over full 5Y. Question: does it hold
in walk-forward OOS? Need 3/5 profitable + OOS/IS ratio > 0.5 to pass V15.3 gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPEC = {"mult": 5.0, "tick": 0.25, "commission": 1.24, "slip_ticks_rt": 1}
SL = 60
TP = 120
EMA = 50


def load_mes():
    df = pd.read_parquet(ROOT / "data" / "futures" / "MES_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def run_trades(df):
    cost_rt = SPEC["commission"] + SPEC["slip_ticks_rt"] * SPEC["tick"] * SPEC["mult"]
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema = close.ewm(span=EMA).mean()

    trades = []
    i = EMA
    while i < len(df) - 1:
        if close.iloc[i] <= ema.iloc[i] or not np.isfinite(ema.iloc[i]):
            i += 1
            continue
        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl = entry_px - SL
        tp = entry_px + TP

        exit_px = None
        exit_idx = None
        for j in range(entry_idx, min(entry_idx + 10, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])
            if j > entry_idx:
                if o <= sl:
                    exit_idx = j; exit_px = o; break
                if o >= tp:
                    exit_idx = j; exit_px = o; break
            if l <= sl and h >= tp:
                exit_idx = j; exit_px = sl; break
            if l <= sl:
                exit_idx = j; exit_px = sl; break
            if h >= tp:
                exit_idx = j; exit_px = tp; break
        if exit_idx is None:
            exit_idx = min(entry_idx + 9, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])

        pnl = (exit_px - entry_px) * SPEC["mult"] - cost_rt
        trades.append({"exit_date": df.index[exit_idx], "pnl": pnl})
        i = exit_idx + 1

    return pd.DataFrame(trades).sort_values("exit_date").reset_index(drop=True)


def sharpe(arr):
    if len(arr) < 2 or arr.std() == 0:
        return 0.0
    return float(arr.mean() / arr.std() * np.sqrt(252))


def main():
    print("MES V2 Walk-Forward — SL=60 TP=120 EMA=50")
    print()
    df = load_mes()
    print(f"Data: {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")

    trades = run_trades(df)
    n = len(trades)
    total = trades["pnl"].sum()
    wr = (trades["pnl"] > 0).mean()
    sh = sharpe(trades["pnl"].values)
    print(f"\nTotal: n={n} Sharpe={sh:.2f} total=${total:.0f} WR={wr:.0%}")

    # WF 5 windows 60/40
    print(f"\n=== Walk-Forward 5 windows ===")
    slice_size = n // 6
    is_size = int(slice_size * 1.5)

    results = []
    for i in range(5):
        oos_start = (i + 1) * slice_size
        is_start = max(0, oos_start - is_size)
        oos_end = oos_start + slice_size
        if oos_end > n:
            break
        is_slice = trades.iloc[is_start:oos_start]
        oos_slice = trades.iloc[oos_start:oos_end]
        is_sh = sharpe(is_slice["pnl"].values)
        oos_sh = sharpe(oos_slice["pnl"].values)
        oos_pnl = oos_slice["pnl"].sum()
        prof = oos_pnl > 0
        results.append({
            "w": i + 1,
            "is_n": len(is_slice),
            "oos_n": len(oos_slice),
            "is_sh": round(is_sh, 2),
            "oos_sh": round(oos_sh, 2),
            "oos_pnl": round(oos_pnl, 0),
            "prof": prof,
        })
        print(f"W{i+1}: IS n={len(is_slice)} Sh={is_sh:+.2f} | OOS n={len(oos_slice)} Sh={oos_sh:+.2f} PnL=${oos_pnl:+.0f} {'PROFIT' if prof else 'LOSS'}")

    n_prof = sum(1 for r in results if r["prof"])
    avg_is = np.mean([r["is_sh"] for r in results])
    avg_oos = np.mean([r["oos_sh"] for r in results])
    ratio = avg_oos / avg_is if avg_is > 0 else 0

    print(f"\n--- WF Summary ---")
    print(f"Profitable OOS: {n_prof}/5")
    print(f"Avg IS Sharpe: {avg_is:+.2f}")
    print(f"Avg OOS Sharpe: {avg_oos:+.2f}")
    print(f"OOS/IS ratio: {ratio:+.2f}")

    print(f"\n=== V15.3 GATE CHECK ===")
    gate_prof = n_prof >= 3
    gate_oos = avg_oos > 0.3
    gate_ratio = ratio > 0.5
    print(f"  Profitable >= 3/5: {'PASS' if gate_prof else 'FAIL'} ({n_prof}/5)")
    print(f"  OOS Sharpe > 0.3:  {'PASS' if gate_oos else 'FAIL'} ({avg_oos:+.2f})")
    print(f"  OOS/IS ratio > 0.5: {'PASS' if gate_ratio else 'FAIL'} ({ratio:+.2f})")

    passed = sum([gate_prof, gate_oos, gate_ratio])
    print(f"\nGATES PASSED: {passed}/3")
    if passed == 3:
        print("VERDICT: GO PAPER (3/3 gates)")
    elif passed >= 2:
        print("VERDICT: BORDERLINE (2/3 gates) — paper extended observation")
    else:
        print("VERDICT: FAIL ({passed}/3 gates) — reject")

    return 0


if __name__ == "__main__":
    sys.exit(main())
