#!/usr/bin/env python3
"""Backtest bond futures strategies — ZN / ZT / ZB.

Context: micro bond futures (M2F/M10Y/M30Y) are NEVER tested in this repo.
Bonds are macro-driven (Fed policy, inflation, flight-to-quality) — completely
decorrelated from equity index futures. Potentially the best diversifier.

Data: IBKR paper gateway 1h bars, 6 months (2025-10 → 2026-04).

Strategies tested:
  B1 - EMA trend follow (classic for bonds — macro regime dependent)
  B2 - Mean reversion on 1h bars (bonds are mean-reverting short-term)
  B3 - Gap fade (bonds have smaller gaps but cleaner patterns)
  B4 - Time-of-day (14:30 ET Fed announcement reactions)

Note: tested on full contracts ZN/ZT/ZB (we'll use micro margin/cost in analysis
even though data is from full contracts — the ratio 1:10 so returns in % are
equivalent).

Cost model:
  M2F (micro 2Y)  : $400 margin, $4 per pt ($2.49 round-trip)
  M10Y (micro 10Y): $500 margin, $10 per pt ($2.49 round-trip)
  M30Y (micro 30Y): $700 margin, $10 per pt ($2.49 round-trip)
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
logger = logging.getLogger("bonds")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Micro bond futures spec (for conversion)
BOND_SPECS = {
    "ZT": {"tick": 0.0078125, "mult_micro": 200, "cost_rt": 2.49, "typical_px": 104},     # M2F (micro 2Y)
    "ZN": {"tick": 0.015625, "mult_micro": 100, "cost_rt": 2.49, "typical_px": 112},      # M10Y (micro 10Y)
    "ZB": {"tick": 0.03125, "mult_micro": 100, "cost_rt": 2.49, "typical_px": 115},       # M30Y (micro 30Y)
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
    pnl_usd: float


def load_bond(sym: str) -> pd.DataFrame:
    f = ROOT / "data" / "futures" / f"{sym}_1H_IBKR6M.parquet"
    df = pd.read_parquet(f)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def ret_to_usd(ret_pct: float, sym: str) -> float:
    """Convert % return to micro futures USD PnL."""
    spec = BOND_SPECS[sym]
    notional = spec["typical_px"] * spec["mult_micro"]
    gross = ret_pct * notional
    return gross - spec["cost_rt"]


# ==================================================================
# B1 — EMA trend follow (classic for bonds)
# ==================================================================
def strat_trend(df: pd.DataFrame, sym: str, fast=20, slow=50) -> list[Trade]:
    close = df["close"].astype(float)
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()

    trades = []
    in_pos = False
    entry_idx = None
    direction = None

    for i in range(slow, len(df) - 1):
        bull = ema_f.iloc[i] > ema_s.iloc[i]
        bear = ema_f.iloc[i] < ema_s.iloc[i]
        if not in_pos:
            if bull:
                entry_idx = i + 1
                direction = "LONG"
                in_pos = True
            elif bear:
                entry_idx = i + 1
                direction = "SHORT"
                in_pos = True
        else:
            # Exit on crossover
            exit_cond = (direction == "LONG" and bear) or (direction == "SHORT" and bull)
            if exit_cond:
                entry_px = float(df["open"].iloc[entry_idx])
                exit_px = float(df["open"].iloc[i + 1])
                ret = (exit_px - entry_px) / entry_px if direction == "LONG" else (entry_px - exit_px) / entry_px
                trades.append(Trade(
                    strat="trend", sym=sym,
                    entry_ts=str(df.index[entry_idx]),
                    exit_ts=str(df.index[i + 1]),
                    entry_px=entry_px, exit_px=exit_px,
                    ret_pct=round(ret * 100, 4),
                    pnl_usd=round(ret_to_usd(ret, sym), 2),
                ))
                in_pos = False

    return trades


# ==================================================================
# B2 — Mean reversion (Bollinger style on 1h)
# ==================================================================
def strat_mr(df: pd.DataFrame, sym: str, period=20, z_entry=2.0, z_exit=0.5, max_hold=10) -> list[Trade]:
    close = df["close"].astype(float)
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    z = (close - ma) / std

    trades = []
    i = period
    while i < len(df) - 1:
        zi = z.iloc[i]
        if pd.isna(zi):
            i += 1
            continue
        direction = None
        if zi > z_entry:
            direction = "SHORT"
        elif zi < -z_entry:
            direction = "LONG"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(df["open"].iloc[entry_idx])

        exit_idx = None
        for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
            zj = z.iloc[j]
            if abs(zj) < z_exit:
                exit_idx = j
                break
        if exit_idx is None:
            exit_idx = min(entry_idx + max_hold - 1, len(df) - 1)
        exit_px = float(df["close"].iloc[exit_idx])
        ret = (exit_px - entry_px) / entry_px if direction == "LONG" else (entry_px - exit_px) / entry_px
        trades.append(Trade(
            strat="mr", sym=sym,
            entry_ts=str(df.index[entry_idx]),
            exit_ts=str(df.index[exit_idx]),
            entry_px=entry_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 4),
            pnl_usd=round(ret_to_usd(ret, sym), 2),
        ))
        i = exit_idx + 1

    return trades


# ==================================================================
# B3 — Gap fade
# ==================================================================
def strat_gap(df: pd.DataFrame, sym: str, threshold_pct=0.001) -> list[Trade]:
    trades = []
    prev_close = None
    for d, day_df in df.groupby(df.index.date):
        if len(day_df) < 3:
            prev_close = None
            continue
        open_bar = day_df.iloc[0]
        open_px = float(open_bar["open"])

        if prev_close is None:
            prev_close = float(day_df.iloc[-1]["close"])
            continue

        gap = (open_px - prev_close) / prev_close
        if abs(gap) < threshold_pct:
            prev_close = float(day_df.iloc[-1]["close"])
            continue

        target = prev_close + gap * 0.5 * prev_close
        direction = "SHORT" if gap > 0 else "LONG"

        exit_px = None
        exit_idx = None
        for i in range(1, len(day_df)):
            bar = day_df.iloc[i]
            if direction == "SHORT" and bar["low"] <= target:
                exit_px = target
                exit_idx = i
                break
            if direction == "LONG" and bar["high"] >= target:
                exit_px = target
                exit_idx = i
                break
        if exit_px is None:
            exit_idx = len(day_df) - 1
            exit_px = float(day_df.iloc[exit_idx]["close"])

        ret = (exit_px - open_px) / open_px if direction == "LONG" else (open_px - exit_px) / open_px
        trades.append(Trade(
            strat="gap", sym=sym,
            entry_ts=str(open_bar.name),
            exit_ts=str(day_df.iloc[exit_idx].name),
            entry_px=open_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 4),
            pnl_usd=round(ret_to_usd(ret, sym), 2),
        ))
        prev_close = float(day_df.iloc[-1]["close"])
    return trades


# ==================================================================
# B4 — Fed time-of-day (14:00 ET = 19:00 UTC, Fed minutes/speeches)
# ==================================================================
def strat_fed_tod(df: pd.DataFrame, sym: str) -> list[Trade]:
    """LONG bond at 14:00 ET (19:00 UTC), hold 1h. Tests the 'post-Fed-event' effect."""
    trades = []
    for d, day_df in df.groupby(df.index.date):
        # Find 19:00 UTC bar (14:00 ET)
        target_hour = 19
        entry_bars = day_df[day_df.index.hour == target_hour]
        if len(entry_bars) == 0:
            continue
        entry_bar = entry_bars.iloc[0]
        entry_idx = day_df.index.get_loc(entry_bar.name)
        if entry_idx + 1 >= len(day_df):
            continue
        exit_bar = day_df.iloc[entry_idx + 1]
        entry_px = float(entry_bar["open"])
        exit_px = float(exit_bar["close"])
        ret = (exit_px - entry_px) / entry_px
        trades.append(Trade(
            strat="fed_tod", sym=sym,
            entry_ts=str(entry_bar.name),
            exit_ts=str(exit_bar.name),
            entry_px=entry_px, exit_px=exit_px,
            ret_pct=round(ret * 100, 4),
            pnl_usd=round(ret_to_usd(ret, sym), 2),
        ))
    return trades


def stats(trades: list[Trade], label: str) -> dict:
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
        span = (df["exit_dt"].max() - df["exit_dt"].min()).days
        tpy = len(df) / span * 365 if span > 0 else 252
        sharpe = mu / sd * np.sqrt(tpy)
    pos = float(df[df["pnl_usd"] > 0]["pnl_usd"].sum())
    neg = float(-df[df["pnl_usd"] < 0]["pnl_usd"].sum())
    pf = pos / neg if neg > 0 else float("inf")
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


def main() -> int:
    logger.info("Loading bond data…")
    data = {}
    for sym in ["ZN", "ZT", "ZB"]:
        try:
            data[sym] = load_bond(sym)
            logger.info(f"  {sym}: {len(data[sym])} bars, {data[sym].index[0]} -> {data[sym].index[-1]}")
        except FileNotFoundError:
            logger.warning(f"  {sym}: missing")

    results = []
    for sym in ["ZN", "ZT", "ZB"]:
        if sym not in data:
            continue
        df = data[sym]
        for strat_fn, name in [
            (strat_trend, "trend"),
            (strat_mr, "mr"),
            (strat_gap, "gap"),
            (strat_fed_tod, "fed_tod"),
        ]:
            try:
                trades = strat_fn(df, sym)
                s = stats(trades, f"{name}_{sym}")
                results.append(s)
                logger.info(f"{name} {sym}: n={s['n']} Sharpe={s['sharpe']} total=${s['total']} WR={s.get('wr','')}")
            except Exception as e:
                logger.error(f"{name} {sym}: {e}")

    results.sort(key=lambda r: r.get("sharpe", 0), reverse=True)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        f"# Bond Futures Intraday Backtest — {today}",
        "",
        "Data: IBKR paper gateway 1h bars, 6 months (Oct 2025 → Apr 2026).",
        "Symbols: ZN (10Y Treasury), ZT (2Y Treasury), ZB (30Y Treasury).",
        "Cost model: micro bond round-trip $2.49 (M2F/M10Y/M30Y).",
        "",
        "## Summary (ranked by Sharpe)",
        "",
        "| Strat × Sym | N | WR | Avg $ | Total $ | **Sharpe** | PF | MaxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['label']} | {r['n']} | {r.get('wr','')} | "
            f"${r.get('avg','')} | ${r.get('total','')} | **{r.get('sharpe','')}** | "
            f"{r.get('pf','')} | ${r.get('mdd','')} |"
        )
    lines.append("")

    # Count wins
    sharpe_positive = sum(1 for r in results if r.get("sharpe", 0) > 0)
    sharpe_good = sum(1 for r in results if r.get("sharpe", 0) > 0.5)
    lines.append(f"**Strats positives (Sharpe > 0) : {sharpe_positive}/{len(results)}**")
    lines.append(f"**Strats bonnes (Sharpe > 0.5) : {sharpe_good}/{len(results)}**")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append("- 6 months seulement (Oct 2025 → Apr 2026) = period limited, biais régime possible")
    lines.append("- Période coincide avec Fed rate-cutting → bond trend probablement biaisé bull")
    lines.append("- Cost model micro ($2.49) appliqué sur returns du full contract — approximation raisonnable")
    lines.append("")

    out_file = OUT_DIR / f"bonds_backtest_{today}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Report: {out_file}")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
