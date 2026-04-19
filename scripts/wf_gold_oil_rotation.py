#!/usr/bin/env python3
"""Walk-forward validation of Gold-Oil Rotation strategy.

5 windows, IS 60% / OOS 40%. Check OOS robustness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    "MGC": {"mult": 10.0, "cost": 2.49},
    "MCL": {"mult": 100.0, "cost": 2.49},
}


def load(sym):
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def sim(df, eidx, side, epx, sl_pct, tp_pct, mh):
    sl = epx * (1 - sl_pct) if side == "BUY" else epx * (1 + sl_pct)
    tp = epx * (1 + tp_pct) if side == "BUY" else epx * (1 - tp_pct)
    for j in range(eidx, min(eidx + mh, len(df))):
        h = float(df["high"].iloc[j]); l = float(df["low"].iloc[j]); o = float(df["open"].iloc[j])
        if j > eidx:
            if o <= sl if side == "BUY" else o >= sl: return j, o
            if o >= tp if side == "BUY" else o <= tp: return j, o
        if side == "BUY":
            if l <= sl: return j, sl
            if h >= tp: return j, tp
        else:
            if h >= sl: return j, sl
            if l <= tp: return j, tp
    end = min(eidx + mh - 1, len(df) - 1)
    return end, float(df["close"].iloc[end])


def run_strategy(mgc, mcl, lookback=20, min_edge=0.02, hold=10):
    common = mgc.index.intersection(mcl.index)
    mgc_c = mgc["close"].reindex(common)
    mcl_c = mcl["close"].reindex(common)
    mgc_ret = mgc_c.pct_change(lookback)
    mcl_ret = mcl_c.pct_change(lookback)
    trades = []
    last = -100
    for i in range(lookback, len(common) - hold):
        if i - last < hold: continue
        gr = float(mgc_ret.iloc[i]); cr = float(mcl_ret.iloc[i])
        if not (np.isfinite(gr) and np.isfinite(cr)): continue
        spread = gr - cr
        if abs(spread) < min_edge: continue
        if spread > 0:
            df = mgc; sym = "MGC"
        else:
            df = mcl; sym = "MCL"
        d = common[i]
        di = df.index.get_loc(d)
        if di + hold >= len(df): break
        ep = float(df["open"].iloc[di + 1])
        exit_idx, exit_px = sim(df, di + 1, "BUY", ep, 0.02, 0.04, hold)
        spec = SPECS[sym]
        pnl = (exit_px - ep) * spec["mult"] - spec["cost"]
        trades.append({
            "exit_date": df.index[exit_idx],
            "pnl": pnl, "sym": sym,
        })
        last = i
    return trades


def stats(trades):
    if not trades: return {"n": 0, "sharpe": 0, "total": 0, "wr": 0}
    df = pd.DataFrame(trades)
    arr = df["pnl"].values
    sharpe = arr.mean() / arr.std() * np.sqrt(252) if arr.std() > 0 else 0
    return {
        "n": len(df),
        "sharpe": round(sharpe, 2),
        "total": round(df["pnl"].sum(), 0),
        "wr": round((arr > 0).mean(), 2),
    }


def slice_trades(trades, date_start, date_end):
    out = []
    for t in trades:
        d = pd.Timestamp(t["exit_date"])
        if date_start <= d <= date_end:
            out.append(t)
    return out


def walk_forward(mgc, mcl, n_windows=5, is_frac=0.6):
    all_trades = run_strategy(mgc, mcl)
    if not all_trades:
        print("No trades"); return

    common = mgc.index.intersection(mcl.index)
    total_days = len(common)
    window_days = total_days // n_windows
    is_days = int(window_days * is_frac)
    oos_days = window_days - is_days

    wf_results = []
    for w in range(n_windows):
        is_start = common[w * window_days]
        is_end = common[w * window_days + is_days - 1]
        oos_start = common[w * window_days + is_days]
        oos_end_idx = min(w * window_days + window_days - 1, total_days - 1)
        oos_end = common[oos_end_idx]

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

    df = pd.DataFrame(wf_results)
    print("\n=== WALK-FORWARD GOLD-OIL ROTATION (5 windows, IS=60% / OOS=40%) ===")
    print(df.to_string(index=False))

    oos_positive = sum(r["oos_profitable"] for r in wf_results)
    total_oos_sharpe = np.mean([r["oos_sharpe"] for r in wf_results if r["oos_n"] > 5])
    total_oos_pnl = sum(r["oos_total"] for r in wf_results)
    print(f"\nOOS profitable windows: {oos_positive}/{n_windows}")
    print(f"OOS mean Sharpe: {total_oos_sharpe:.2f}")
    print(f"OOS total PnL: ${total_oos_pnl:,.0f}")

    # Validation gates V15.3
    gate_oos_wins = oos_positive >= 3
    gate_oos_sharpe = total_oos_sharpe > 0.3
    gate_overall = gate_oos_wins and gate_oos_sharpe
    print(f"\nGate 3/5 OOS profitable: {'PASS' if gate_oos_wins else 'FAIL'}")
    print(f"Gate OOS Sharpe > 0.3: {'PASS' if gate_oos_sharpe else 'FAIL'}")
    print(f"OVERALL: {'✅ PASS' if gate_overall else '❌ FAIL'}")

    return df


def main():
    mgc = load("MGC"); mcl = load("MCL")
    walk_forward(mgc, mcl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
