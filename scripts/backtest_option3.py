#!/usr/bin/env python3
"""Option 3 quick test — 2 follow-ups from the main research session.

1. SHORT Fed-TOD bonds (19h UTC) — the LONG version lost 84% on ZT, meaning the
   SHORT is winning 84% of the time. Is this a real pattern or data mining?

2. ORB 5-min native — the hourly resample was too coarse for opening range.
   Re-test with real 30-min opening range (6 × 5-min bars) on native data.

Data: IBKR 5-min for equity (MES/MNQ/M2K), 1h for bonds (ZN/ZT/ZB).
"""
from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("option3")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Cost model
COSTS = {
    "MES": {"mult": 5.0, "cost_rt": 2.49, "tick": 0.25},
    "MNQ": {"mult": 2.0, "cost_rt": 1.74, "tick": 0.25},
    "M2K": {"mult": 5.0, "cost_rt": 1.74, "tick": 0.10},
    "ZN": {"mult": 100, "cost_rt": 2.49, "typical": 112},
    "ZT": {"mult": 200, "cost_rt": 2.49, "typical": 104},
    "ZB": {"mult": 100, "cost_rt": 2.49, "typical": 115},
}


def load_5m(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_5M_IBKR6M.parquet")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def load_1h(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1H_IBKR6M.parquet")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def to_usd(ret_pct: float, sym: str) -> float:
    s = COSTS[sym]
    if "typical" in s:
        notional = s["typical"] * s["mult"]
    else:
        notional = 7000 * s["mult"] if sym == "MES" else 25000 * s["mult"] if sym == "MNQ" else 2300 * s["mult"]
    return ret_pct * notional - s["cost_rt"]


def stats(trades, label):
    if not trades:
        return {"label": label, "n": 0}
    df = pd.DataFrame(trades)
    net = df["pnl_usd"].values
    n = len(df)
    wr = float((net > 0).mean())
    total = float(net.sum())
    mu = float(net.mean())
    sd = float(net.std())
    if sd == 0 or n < 2:
        sharpe = 0
    else:
        df["exit_dt"] = pd.to_datetime(df["exit_ts"])
        span = max(1, (df["exit_dt"].max() - df["exit_dt"].min()).days)
        tpy = n / span * 365
        sharpe = mu / sd * np.sqrt(tpy)
    pos = float(df[df["pnl_usd"] > 0]["pnl_usd"].sum())
    neg = float(-df[df["pnl_usd"] < 0]["pnl_usd"].sum())
    pf = pos / neg if neg > 0 else float("inf")
    cum = net.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {
        "label": label, "n": n, "wr": round(wr, 3),
        "avg": round(mu, 2), "total": round(total, 0),
        "sharpe": round(sharpe, 2),
        "pf": round(pf, 2) if pf != float("inf") else 999,
        "mdd": round(mdd, 0),
    }


# ==================================================================
# TEST 1 — SHORT bonds Fed-TOD (19h UTC = 14h ET)
# ==================================================================
def short_fed_tod(df: pd.DataFrame, sym: str) -> list[dict]:
    """SHORT bond at 14:00 ET (19:00 UTC), hold 1h, exit 15:00 ET.
    Inverse of LONG Fed-TOD which was catastrophic (WR 16% on ZT).
    """
    trades = []
    for d, day_df in df.groupby(df.index.date):
        target = day_df[day_df.index.hour == 19]
        if len(target) == 0:
            continue
        entry_bar = target.iloc[0]
        entry_idx = day_df.index.get_loc(entry_bar.name)
        if entry_idx + 1 >= len(day_df):
            continue
        exit_bar = day_df.iloc[entry_idx + 1]
        entry_px = float(entry_bar["open"])
        exit_px = float(exit_bar["close"])
        # SHORT: profit if price goes down
        ret = (entry_px - exit_px) / entry_px
        pnl = to_usd(ret, sym)
        trades.append({
            "sym": sym, "entry_ts": str(entry_bar.name), "exit_ts": str(exit_bar.name),
            "entry_px": entry_px, "exit_px": exit_px,
            "ret_pct": round(ret * 100, 4), "pnl_usd": round(pnl, 2),
        })
    return trades


# ==================================================================
# TEST 2 — ORB 30-min native (5-min bars)
# ==================================================================
def orb_30min_native(df: pd.DataFrame, sym: str) -> list[dict]:
    """True 30-min opening range breakout using 5-min bars.

    First 30min = 13:30-14:00 UTC (US ST, 9:30-10:00 ET) or 14:30-15:00 UTC (DST).
    Use first 6 bars after 13:30 UTC hour change.
    Entry: break of opening range high/low on subsequent bar.
    Exit: opposing side of range (SL) or 15:55 ET (20:55 UTC) close (time-based).
    """
    trades = []
    for d, day_df in df.groupby(df.index.date):
        # Find RTH start (first bar >= 13:30 UTC, which is US ST open ~9:30 ET)
        rth = day_df[day_df.index.hour >= 13]
        if len(rth) < 15:  # need enough bars
            continue
        # First 30min = first 6 bars (5-min × 6 = 30min)
        opening_range = rth.iloc[:6]
        or_high = float(opening_range["high"].max())
        or_low = float(opening_range["low"].min())
        or_range = or_high - or_low
        if or_range <= 0:
            continue

        # Scan subsequent bars for breakout (until end of day or ~21:00 UTC = 16:00 ET)
        remaining = rth.iloc[6:]
        # Cap at 20:55 UTC
        end_of_day = remaining[remaining.index.hour <= 20]
        if len(end_of_day) == 0:
            continue

        entry_px = None
        entry_ts = None
        direction = None
        entry_idx = None
        for i in range(len(end_of_day)):
            bar = end_of_day.iloc[i]
            if bar["close"] > or_high and entry_px is None:
                entry_px = float(bar["close"])
                direction = "LONG"
                entry_ts = bar.name
                entry_idx = i
                break
            if bar["close"] < or_low and entry_px is None:
                entry_px = float(bar["close"])
                direction = "SHORT"
                entry_ts = bar.name
                entry_idx = i
                break
        if entry_px is None:
            continue

        # Exit: opposing side of OR (SL) or time close
        sl_px = or_low if direction == "LONG" else or_high
        exit_px = None
        exit_ts = None
        for j in range(entry_idx + 1, len(end_of_day)):
            bar = end_of_day.iloc[j]
            if direction == "LONG" and bar["low"] <= sl_px:
                exit_px = sl_px
                exit_ts = bar.name
                break
            if direction == "SHORT" and bar["high"] >= sl_px:
                exit_px = sl_px
                exit_ts = bar.name
                break
        if exit_px is None:
            last = end_of_day.iloc[-1]
            exit_px = float(last["close"])
            exit_ts = last.name

        ret = (exit_px - entry_px) / entry_px if direction == "LONG" else (entry_px - exit_px) / entry_px
        pnl = to_usd(ret, sym)
        trades.append({
            "sym": sym, "entry_ts": str(entry_ts), "exit_ts": str(exit_ts),
            "entry_px": entry_px, "exit_px": exit_px, "direction": direction,
            "or_range": or_range,
            "ret_pct": round(ret * 100, 4), "pnl_usd": round(pnl, 2),
        })
    return trades


def main() -> int:
    logger.info("=== OPTION 3 QUICK TEST ===")

    # TEST 1 — SHORT Fed-TOD bonds
    print("\n## TEST 1 — SHORT bonds Fed-TOD (14:00 ET)")
    results_1 = []
    for sym in ["ZN", "ZT", "ZB"]:
        try:
            df = load_1h(sym)
            trades = short_fed_tod(df, sym)
            s = stats(trades, f"short_fed_tod_{sym}")
            results_1.append(s)
            logger.info(f"SHORT Fed-TOD {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s.get('wr','')}")
        except Exception as e:
            logger.error(f"{sym}: {e}")

    # TEST 2 — ORB 30-min native on 5-min data
    print("\n## TEST 2 — ORB 30-min native (5-min bars)")
    results_2 = []
    for sym in ["MES", "MNQ", "M2K"]:
        try:
            df = load_5m(sym)
            trades = orb_30min_native(df, sym)
            s = stats(trades, f"orb30_native_{sym}")
            results_2.append(s)
            logger.info(f"ORB30 native {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s.get('wr','')}")
        except Exception as e:
            logger.error(f"{sym}: {e}")

    # Build report
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        f"# Option 3 Quick Test — {today}",
        "",
        "Follow-ups from main research session:",
        "1. SHORT Fed-TOD bonds (inverse of LONG which lost 84%)",
        "2. ORB 30-min native (5-min bars, not hourly resample)",
        "",
        "## Test 1 — SHORT Fed-TOD bonds",
        "",
        "| Symbol | N | WR | Avg $ | Total $ | Sharpe | PF | MaxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in results_1:
        lines.append(
            f"| {s.get('label','').replace('short_fed_tod_','')} | {s['n']} | {s.get('wr','')} | "
            f"${s.get('avg','')} | ${s.get('total','')} | **{s.get('sharpe','')}** | "
            f"{s.get('pf','')} | ${s.get('mdd','')} |"
        )
    lines.append("")

    lines.append("## Test 2 — ORB 30-min native (5-min)")
    lines.append("")
    lines.append("| Symbol | N | WR | Avg $ | Total $ | Sharpe | PF | MaxDD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for s in results_2:
        lines.append(
            f"| {s.get('label','').replace('orb30_native_','')} | {s['n']} | {s.get('wr','')} | "
            f"${s.get('avg','')} | ${s.get('total','')} | **{s.get('sharpe','')}** | "
            f"{s.get('pf','')} | ${s.get('mdd','')} |"
        )
    lines.append("")

    out_file = OUT_DIR / f"option3_{today}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"\nReport: {out_file}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
