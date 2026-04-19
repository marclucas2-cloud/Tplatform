#!/usr/bin/env python3
"""Backtest 4 intraday strats on IBKR-tradable futures (via ETF proxies).

Strategies:
  S1 - Opening Range Breakout (ORB)       : break of first hour range (MES/MNQ/M2K)
  S2 - Time-of-Day rally 15:30 ET         : LONG last 30min of US session
  S3 - Gap fade                            : fade overnight gaps > 0.3%
  S4 - VIX spike mean reversion            : LONG when VIX > 90p, exit retrace 50%

Data: 2 years hourly bars via yfinance ETF proxies
  - MES ← SPY, MNQ ← QQQ, M2K ← IWM, MCL ← USO, MGC ← GLD
  - VIX ← ^VIX

Costs: realistic futures (MES $2.49/round-trip, MNQ $1.74, M2K $1.74)
  (ETF prices used but cost model converted to futures economics)

Output: reports/research/intraday_4strats_{date}.md
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
logger = logging.getLogger("intraday_4strats")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ETF-to-futures price conversion and cost model
# ETF return % ≈ futures return % (high correlation for MES/SPY)
# We model futures PnL in USD using ETF % return × notional / futures mult
# BUT for simplicity, we directly model the return and apply futures round-trip cost
FUTURES_SPECS = {
    "MES": {"mult": 5.0, "tick": 0.25, "cost_rt": 2.49, "typical_px": 7000},
    "MNQ": {"mult": 2.0, "tick": 0.25, "cost_rt": 1.74, "typical_px": 25000},
    "M2K": {"mult": 5.0, "tick": 0.10, "cost_rt": 1.74, "typical_px": 2300},
    "MCL": {"mult": 100.0, "tick": 0.01, "cost_rt": 2.49, "typical_px": 75},
    "MGC": {"mult": 10.0, "tick": 0.10, "cost_rt": 2.49, "typical_px": 2400},
}


@dataclass
class Trade:
    strat: str
    sym: str
    entry_ts: str
    exit_ts: str
    entry_px: float
    exit_px: float
    ret_pct: float
    pnl_usd: float  # approx futures PnL


def load_ibkr_5m(sym: str) -> pd.DataFrame:
    """Load IBKR 5-min bars (real futures data)."""
    f = ROOT / "data" / "futures" / f"{sym}_5M_IBKR6M.parquet"
    df = pd.read_parquet(f)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def load_hourly(sym: str) -> pd.DataFrame:
    """Load IBKR 5-min and resample to hourly for strats that need less granularity."""
    df = load_ibkr_5m(sym)
    agg = df.resample("1H").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    return agg


def etf_to_futures_pnl(ret_pct: float, sym: str) -> float:
    """Convert an ETF % return to approximate futures USD PnL."""
    spec = FUTURES_SPECS[sym]
    notional = spec["typical_px"] * spec["mult"]
    gross = ret_pct * notional
    return gross - spec["cost_rt"]


# ==================================================================
# S1 — Opening Range Breakout (ORB)
# ==================================================================
def strat_orb(df: pd.DataFrame, sym: str) -> list[Trade]:
    """Buy break of first hour's high (9:30-10:30 ET), exit at 15:55 ET.

    Hourly bars: first hour = 14:30-15:30 UTC (US DST) or 13:30-14:30 UTC (US ST).
    We use index time_of_day and pick bars ~14:30-15:30 UTC for simplicity.

    Setup:
      - At 15:30 UTC (10:30 ET): compute range of first hour (high - low of 14:30 bar)
      - If close > first_high: entry LONG at close of 15:30 bar
      - If close < first_low: entry SHORT
      - Exit at 20:30 UTC (16:30 ET) close (end of RTH)
      - SL = other side of range
    """
    trades = []
    # Group by calendar date of the index
    for d, day_df in df.groupby(df.index.date):
        if len(day_df) < 4:
            continue
        # Get first bar of RTH (US open ~13:30-14:30 UTC depending on DST)
        rth_bars = day_df[(day_df.index.hour >= 13) & (day_df.index.hour <= 21)]
        if len(rth_bars) < 4:
            continue
        first_bar = rth_bars.iloc[0]
        first_high = float(first_bar["high"])
        first_low = float(first_bar["low"])
        first_range = first_high - first_low
        if first_range <= 0:
            continue

        # Check breakout on subsequent bars (next 3 bars = 3 hours)
        for i in range(1, min(4, len(rth_bars))):
            bar = rth_bars.iloc[i]
            if bar["close"] > first_high:
                entry_px = float(bar["close"])
                direction = "LONG"
                break
            elif bar["close"] < first_low:
                entry_px = float(bar["close"])
                direction = "SHORT"
                break
        else:
            continue  # no breakout

        # Exit at last RTH bar close
        exit_bar = rth_bars.iloc[-1]
        exit_px = float(exit_bar["close"])
        if direction == "LONG":
            ret = (exit_px - entry_px) / entry_px
        else:
            ret = (entry_px - exit_px) / entry_px

        pnl_usd = etf_to_futures_pnl(ret, sym)
        trades.append(Trade(
            strat="orb", sym=sym,
            entry_ts=str(bar.name),
            exit_ts=str(exit_bar.name),
            entry_px=entry_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 3),
            pnl_usd=round(pnl_usd, 2),
        ))
    return trades


# ==================================================================
# S2 — Time-of-Day rally 15:30 ET (last 30min of session)
# ==================================================================
def strat_tod_rally(df: pd.DataFrame, sym: str) -> list[Trade]:
    """LONG at ~19:30 UTC (15:30 ET), exit at ~20:30 UTC (16:30 ET close).

    Hourly data: not exact 30min, so use last 2 bars of RTH (15:30-20:30 UTC window).
    Heston-Korajczyk-Sadka (2010): last 30min tends to drift positive.
    """
    trades = []
    for d, day_df in df.groupby(df.index.date):
        rth = day_df[(day_df.index.hour >= 13) & (day_df.index.hour <= 21)]
        if len(rth) < 5:
            continue
        # Entry = open of 2nd-to-last RTH bar, exit = close of last RTH bar
        entry_bar = rth.iloc[-2]
        exit_bar = rth.iloc[-1]
        entry_px = float(entry_bar["open"])
        exit_px = float(exit_bar["close"])
        ret = (exit_px - entry_px) / entry_px
        pnl_usd = etf_to_futures_pnl(ret, sym)
        trades.append(Trade(
            strat="tod_rally", sym=sym,
            entry_ts=str(entry_bar.name),
            exit_ts=str(exit_bar.name),
            entry_px=entry_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 3),
            pnl_usd=round(pnl_usd, 2),
        ))
    return trades


# ==================================================================
# S3 — Gap fade
# ==================================================================
def strat_gap_fade(df: pd.DataFrame, sym: str, gap_threshold: float = 0.003) -> list[Trade]:
    """Fade opening gap > 0.3%: exit at mid-gap or end of session.

    Find open bar (first bar of US session), compare to previous session close.
    If gap > +0.3%: SHORT, target = half-gap retracement.
    If gap < -0.3%: LONG, target = half-gap retracement.
    Exit: target hit or end of session.
    """
    trades = []
    prev_close = None
    for d, day_df in df.groupby(df.index.date):
        rth = day_df[(day_df.index.hour >= 13) & (day_df.index.hour <= 21)]
        if len(rth) < 3:
            prev_close = None
            continue
        open_bar = rth.iloc[0]
        open_px = float(open_bar["open"])

        if prev_close is None:
            prev_close = float(rth.iloc[-1]["close"])
            continue

        gap_pct = (open_px - prev_close) / prev_close
        if abs(gap_pct) < gap_threshold:
            prev_close = float(rth.iloc[-1]["close"])
            continue

        target = prev_close + gap_pct * 0.5 * prev_close  # 50% retracement
        direction = "SHORT" if gap_pct > 0 else "LONG"

        # Find exit: target hit or last bar
        exit_px = None
        exit_bar_idx = None
        for i in range(1, len(rth)):
            bar = rth.iloc[i]
            if direction == "SHORT" and bar["low"] <= target:
                exit_px = target
                exit_bar_idx = i
                break
            if direction == "LONG" and bar["high"] >= target:
                exit_px = target
                exit_bar_idx = i
                break
        if exit_px is None:
            exit_bar_idx = len(rth) - 1
            exit_px = float(rth.iloc[exit_bar_idx]["close"])

        if direction == "LONG":
            ret = (exit_px - open_px) / open_px
        else:
            ret = (open_px - exit_px) / open_px

        pnl_usd = etf_to_futures_pnl(ret, sym)
        trades.append(Trade(
            strat="gap_fade", sym=sym,
            entry_ts=str(open_bar.name),
            exit_ts=str(rth.iloc[exit_bar_idx].name),
            entry_px=open_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 3),
            pnl_usd=round(pnl_usd, 2),
        ))
        prev_close = float(rth.iloc[-1]["close"])
    return trades


# ==================================================================
# S4 — VIX spike mean reversion (on SPY/MES)
# ==================================================================
def strat_vix_spike_mr(df: pd.DataFrame, vix: pd.DataFrame, sym: str) -> list[Trade]:
    """LONG MES when VIX spikes > 90th percentile 30d rolling, exit when VIX retraces 50%."""
    trades = []
    # Align VIX to MES timeline
    vix_close = vix["close"].reindex(df.index, method="ffill")
    vix_90p = vix_close.rolling(30 * 24, min_periods=48).quantile(0.90)
    vix_baseline = vix_close.rolling(30 * 24, min_periods=48).median()

    entries = df.index[(vix_close > vix_90p) & (vix_close.shift(1) <= vix_90p.shift(1))]
    for ts in entries:
        try:
            idx = df.index.get_loc(ts)
        except KeyError:
            continue
        if idx + 1 >= len(df):
            continue
        entry_px = float(df["close"].iloc[idx])
        vix_at_entry = float(vix_close.iloc[idx])
        vix_target = (vix_at_entry + float(vix_baseline.iloc[idx])) / 2  # 50% retrace

        exit_idx = None
        exit_px = None
        for j in range(idx + 1, min(idx + 48, len(df))):  # max 48h (2 days)
            if vix_close.iloc[j] <= vix_target:
                exit_idx = j
                exit_px = float(df["close"].iloc[j])
                break
        if exit_idx is None:
            exit_idx = min(idx + 47, len(df) - 1)
            exit_px = float(df["close"].iloc[exit_idx])

        ret = (exit_px - entry_px) / entry_px
        pnl_usd = etf_to_futures_pnl(ret, sym)
        trades.append(Trade(
            strat="vix_spike_mr", sym=sym,
            entry_ts=str(ts),
            exit_ts=str(df.index[exit_idx]),
            entry_px=entry_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 3),
            pnl_usd=round(pnl_usd, 2),
        ))
    return trades


# ==================================================================
# Stats + WF
# ==================================================================
def compute_stats(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0, "sharpe": 0, "total": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    net = df["pnl_usd"].values
    wr = float((net > 0).mean())
    total = float(net.sum())
    mu = float(net.mean())
    sd = float(net.std())
    if sd == 0 or len(df) < 2:
        sharpe = 0.0
    else:
        df["exit_dt"] = pd.to_datetime(df["exit_ts"])
        span_days = (df["exit_dt"].max() - df["exit_dt"].min()).days
        trades_per_year = len(df) / span_days * 365 if span_days > 0 else 252
        sharpe = mu / sd * np.sqrt(trades_per_year)
    wins = float(df[df["pnl_usd"] > 0]["pnl_usd"].sum())
    losses = float(-df[df["pnl_usd"] < 0]["pnl_usd"].sum())
    pf = wins / losses if losses > 0 else float("inf")
    cum = net.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    return {
        "label": label,
        "n": len(df),
        "wr": round(wr, 3),
        "avg": round(mu, 2),
        "total": round(total, 0),
        "sharpe": round(sharpe, 2),
        "pf": round(pf, 2) if pf != float("inf") else 999,
        "mdd": round(mdd, 0),
    }


def walk_forward(trades: list[Trade], n_windows: int = 5) -> dict:
    if len(trades) < 40:
        return {"n": len(trades), "note": "insufficient"}
    df = pd.DataFrame([asdict(t) for t in trades])
    df["exit_dt"] = pd.to_datetime(df["exit_ts"])
    df = df.sort_values("exit_dt").reset_index(drop=True)
    n = len(df)
    slice_size = n // (n_windows + 1)
    is_size = int(slice_size * 1.5)
    results = []
    for i in range(n_windows):
        oos_start = (i + 1) * slice_size
        is_start = max(0, oos_start - is_size)
        oos_end = oos_start + slice_size
        if oos_end > n:
            break
        is_slice = df.iloc[is_start:oos_start]
        oos_slice = df.iloc[oos_start:oos_end]

        def _sh(arr):
            if len(arr) < 2 or arr.std() == 0:
                return 0.0
            return float(arr.mean() / arr.std() * np.sqrt(252))

        results.append({
            "w": i + 1,
            "is_n": len(is_slice),
            "oos_n": len(oos_slice),
            "is_sh": round(_sh(is_slice["pnl_usd"].values), 2),
            "oos_sh": round(_sh(oos_slice["pnl_usd"].values), 2),
            "oos_pnl": round(float(oos_slice["pnl_usd"].sum()), 0),
            "oos_prof": bool(oos_slice["pnl_usd"].sum() > 0),
        })
    n_prof = sum(1 for r in results if r["oos_prof"])
    avg_oos = float(np.mean([r["oos_sh"] for r in results])) if results else 0
    avg_is = float(np.mean([r["is_sh"] for r in results])) if results else 0
    return {
        "n_windows": len(results),
        "n_profitable": n_prof,
        "avg_is_sharpe": round(avg_is, 2),
        "avg_oos_sharpe": round(avg_oos, 2),
        "ratio": round(avg_oos / avg_is if avg_is > 0 else 0, 2),
        "windows": results,
    }


def main() -> int:
    logger.info("Loading IBKR 5-min data (resampled to 1H)…")
    data = {}
    for sym in ["MES", "MNQ", "M2K", "MCL", "MGC"]:
        try:
            data[sym] = load_hourly(sym)
            logger.info(f"  {sym}: {len(data[sym])} bars")
        except FileNotFoundError:
            logger.warning(f"  {sym}: missing")
    # VIX data still from yfinance (no VIX futures in our IBKR downloads)
    try:
        vix_f = ROOT / "data" / "futures" / "VIX_1H_YF2Y.parquet"
        vix = pd.read_parquet(vix_f)
        vix.index = pd.to_datetime(vix.index)
        if vix.index.tz is not None:
            vix.index = vix.index.tz_localize(None)
        logger.info(f"  VIX: {len(vix)} bars (yfinance)")
    except Exception:
        vix = pd.DataFrame()
        logger.warning("  VIX: unavailable")

    results = {}

    # S1 — ORB on MES, MNQ, M2K
    for sym in ["MES", "MNQ", "M2K"]:
        if sym not in data:
            continue
        t = strat_orb(data[sym], sym)
        key = f"orb_{sym.lower()}"
        results[key] = {"stats": compute_stats(t, key), "wf": walk_forward(t)}
        s = results[key]["stats"]
        logger.info(f"ORB {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s['wr']}")

    # S2 — TOD rally on MES, MNQ, M2K
    for sym in ["MES", "MNQ", "M2K"]:
        if sym not in data:
            continue
        t = strat_tod_rally(data[sym], sym)
        key = f"tod_{sym.lower()}"
        results[key] = {"stats": compute_stats(t, key), "wf": walk_forward(t)}
        s = results[key]["stats"]
        logger.info(f"TOD {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s['wr']}")

    # S3 — Gap fade
    for sym in ["MES", "MNQ", "M2K", "MCL", "MGC"]:
        if sym not in data:
            continue
        t = strat_gap_fade(data[sym], sym)
        key = f"gap_{sym.lower()}"
        results[key] = {"stats": compute_stats(t, key), "wf": walk_forward(t)}
        s = results[key]["stats"]
        logger.info(f"GAP {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s['wr']}")

    # S4 — VIX spike MR on MES
    for sym in ["MES", "MNQ"]:
        if sym not in data:
            continue
        t = strat_vix_spike_mr(data[sym], vix, sym)
        key = f"vix_{sym.lower()}"
        results[key] = {"stats": compute_stats(t, key), "wf": walk_forward(t)}
        s = results[key]["stats"]
        logger.info(f"VIX MR {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s['wr']}")

    # Build report
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        f"# Intraday 4-Strategies Backtest — IBKR Futures — {today}",
        "",
        "Data: 2 years of 1h bars via yfinance ETF proxies (SPY/QQQ/IWM/USO/GLD + ^VIX).",
        "Costs: realistic IBKR micro futures round-trip (MES $2.49, MNQ $1.74, etc).",
        "",
        "## Summary",
        "",
        "| Strat | Symbol | N | WR | Avg $ | Total $ | **Sharpe** | PF | MaxDD | WF prof | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key in sorted(results.keys()):
        r = results[key]
        s = r["stats"]
        wf = r["wf"]
        n_prof = wf.get("n_profitable", 0)
        nw = wf.get("n_windows", 0)
        verdict = "GO" if s.get("sharpe", 0) > 0.5 and n_prof >= nw / 2 and nw > 0 else \
                  "MARGINAL" if s.get("sharpe", 0) > 0.2 else "KILL"
        strat_name, sym = key.split("_", 1)
        lines.append(
            f"| {strat_name} | {sym.upper()} | {s['n']} | {s.get('wr','')} | "
            f"${s.get('avg','')} | ${s.get('total','')} | **{s.get('sharpe','')}** | "
            f"{s.get('pf','')} | ${s.get('mdd','')} | {n_prof}/{nw} | {verdict} |"
        )
    lines.append("")

    lines.append("## Walk-Forward Detail")
    lines.append("")
    for key in sorted(results.keys()):
        wf = results[key]["wf"]
        if not wf.get("windows"):
            continue
        lines.append(f"### {key}")
        lines.append(f"- IS avg Sharpe: {wf['avg_is_sharpe']} | OOS avg: **{wf['avg_oos_sharpe']}** | Ratio: {wf['ratio']}")
        lines.append(f"- Profitable OOS: **{wf['n_profitable']}/{wf['n_windows']}**")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- ETF proxies used (SPY for MES, QQQ for MNQ, IWM for M2K, USO for MCL, GLD for MGC). Corrélation >0.95 avec futures, mais timing open/close peut différer de quelques min vs vrais futures CME.")
    lines.append("- Hourly bars → ORB tests a 1h opening range, pas le classique 30min. Plus grossier mais directionnel.")
    lines.append("- Costs model: US$ futures round-trip applied directement sur notional ETF × multiplier futures. Approximation raisonnable pour un first screen.")
    lines.append("- Pour toute strat PASS ou MARGINAL → refaire avec vraie data futures CME intraday avant deploy.")
    lines.append("")

    out_file = OUT_DIR / f"intraday_4strats_{today}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Report: {out_file}")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
