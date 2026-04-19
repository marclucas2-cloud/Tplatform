#!/usr/bin/env python3
"""Walk-forward backtest — Overnight MES PRODUCTION logic (matches strategies_v2/futures/overnight_buy_close.py).

Critical : this replicates the EXACT logic running live in worker.py.
Previous WF backtests (wf_futures_all.py signal_overnight_momentum) tested a
DIFFERENT strategy (daily momentum long/short, close-to-close, no EMA filter).
The -0.70 Sharpe from paper_review_20260331 came from that wrong test.

Production logic:
  - Cycle fires at 16h Paris = 10h ET daily
  - bar.close = yesterday's daily close (last completed bar)
  - If close[T-1] > ema20[T-1]: BUY MES at market (fill ~= open[T])
  - SL = fill - 30 points ($150)
  - TP = fill + 50 points ($250)
  - Exit on first subsequent daily bar where SL or TP is hit
  - No time exit (but we add a 10-day safety cap)

Costs: $1.24 commission + 0.25 tick * $5 = $1.25 slippage = $2.49 round-trip.

Data: data/futures/MES_1D.parquet (5 years daily).
Output: reports/research/wf_overnight_mes_real_{date}.md
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
logger = logging.getLogger("wf_overnight_mes")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# === Production constants ===
EMA_PERIOD = 20
SL_POINTS = 30.0
TP_POINTS = 50.0
MULT = 5.0                  # MES $5 per point
COMMISSION = 1.24           # IBKR micro futures round trip
SLIPPAGE_TICKS = 1          # 1 tick round trip (0.5 per side)
TICK_SIZE = 0.25
MAX_HOLD_DAYS = 10          # safety cap (production has none)


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_px: float
    exit_px: float
    exit_reason: str   # SL | TP | TIME
    bars_held: int
    pnl_pts: float
    pnl_gross_usd: float
    pnl_net_usd: float


def load_mes() -> pd.DataFrame:
    f = ROOT / "data" / "futures" / "MES_1D.parquet"
    df = pd.read_parquet(f)
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def backtest_production_logic(df: pd.DataFrame) -> list[Trade]:
    """Replicates the exact live production logic."""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    ema20 = close.ewm(span=EMA_PERIOD, adjust=False).mean()

    slip_usd_rt = SLIPPAGE_TICKS * TICK_SIZE * MULT  # 1 tick × $1.25
    cost_rt = COMMISSION + slip_usd_rt

    trades: list[Trade] = []
    i = EMA_PERIOD
    while i < len(df) - 1:
        # Signal check on day T-1 (the "yesterday close" perspective)
        # In live: at 16h Paris on day T, we check close[T-1] > ema20[T-1]
        # which is the previous daily close. We then buy at open[T].
        c_prev = float(close.iloc[i])
        ema_prev = float(ema20.iloc[i])
        if c_prev <= ema_prev or not np.isfinite(ema_prev):
            i += 1
            continue

        # Entry on next bar open (~10 ET on day T+1)
        entry_idx = i + 1
        if entry_idx >= len(df):
            break
        entry_px = float(open_.iloc[entry_idx])
        sl_px = entry_px - SL_POINTS
        tp_px = entry_px + TP_POINTS

        exit_idx = None
        exit_px = None
        exit_reason = None
        for j in range(entry_idx, min(entry_idx + MAX_HOLD_DAYS, len(df))):
            h = float(high.iloc[j])
            l = float(low.iloc[j])
            o = float(open_.iloc[j])

            # Handle gap on open (worst case assumption)
            if j > entry_idx:  # only check open gap after the entry bar
                if o <= sl_px:
                    exit_idx = j
                    exit_px = o
                    exit_reason = "SL_GAP"
                    break
                if o >= tp_px:
                    exit_idx = j
                    exit_px = o
                    exit_reason = "TP_GAP"
                    break

            # Intraday hit: we don't know which came first in the day
            # Conservative: if both hit, assume SL (pessimistic)
            sl_hit = l <= sl_px
            tp_hit = h >= tp_px
            if sl_hit and tp_hit:
                exit_idx = j
                exit_px = sl_px
                exit_reason = "SL_PESSIMISTIC"
                break
            if sl_hit:
                exit_idx = j
                exit_px = sl_px
                exit_reason = "SL"
                break
            if tp_hit:
                exit_idx = j
                exit_px = tp_px
                exit_reason = "TP"
                break

        if exit_idx is None:
            # Time exit: close of last bar in window
            exit_idx = min(entry_idx + MAX_HOLD_DAYS - 1, len(df) - 1)
            exit_px = float(close.iloc[exit_idx])
            exit_reason = "TIME"

        pnl_pts = exit_px - entry_px
        pnl_gross = pnl_pts * MULT
        pnl_net = pnl_gross - cost_rt

        trades.append(Trade(
            entry_date=str(df.index[entry_idx].date()),
            exit_date=str(df.index[exit_idx].date()),
            entry_px=entry_px,
            exit_px=exit_px,
            exit_reason=exit_reason,
            bars_held=exit_idx - entry_idx + 1,
            pnl_pts=round(pnl_pts, 2),
            pnl_gross_usd=round(pnl_gross, 2),
            pnl_net_usd=round(pnl_net, 2),
        ))
        # Next signal check only AFTER this trade exits (no overlapping)
        i = exit_idx + 1

    return trades


def compute_stats(trades: list[Trade], label: str = "") -> dict:
    if not trades:
        return {"label": label, "n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date")
    net = df["pnl_net_usd"].values
    wr = float((net > 0).mean())
    total = float(net.sum())
    avg = float(net.mean())
    std = float(net.std())
    # Annualized Sharpe (trades are roughly 1 per day when active)
    # Use mean / std * sqrt(trades_per_year)
    if len(df) >= 2 and std > 0:
        span_days = (df["exit_date"].max() - df["exit_date"].min()).days
        trades_per_year = len(df) / span_days * 365 if span_days > 0 else 252
        sharpe = avg / std * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0
    wins = df[df["pnl_net_usd"] > 0]["pnl_net_usd"].sum()
    losses = -df[df["pnl_net_usd"] < 0]["pnl_net_usd"].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    cum = net.cumsum()
    peak = np.maximum.accumulate(cum)
    mdd = float((cum - peak).min())
    avg_hold = float(df["bars_held"].mean())
    return {
        "label": label,
        "n_trades": len(df),
        "win_rate": round(wr, 3),
        "avg_net_usd": round(avg, 2),
        "total_net_usd": round(total, 0),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999,
        "max_dd_usd": round(mdd, 0),
        "avg_bars_held": round(avg_hold, 1),
    }


def walk_forward(trades: list[Trade], n_windows: int = 5) -> list[dict]:
    if len(trades) < 50:
        return []
    df = pd.DataFrame([asdict(t) for t in trades])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values("exit_date").reset_index(drop=True)
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

        def _sharpe(arr):
            if len(arr) < 2 or arr.std() == 0:
                return 0.0
            return float(arr.mean() / arr.std() * np.sqrt(252))

        results.append({
            "window": i + 1,
            "is_n": len(is_slice),
            "oos_n": len(oos_slice),
            "is_sharpe": round(_sharpe(is_slice["pnl_net_usd"].values), 2),
            "oos_sharpe": round(_sharpe(oos_slice["pnl_net_usd"].values), 2),
            "is_pnl": round(float(is_slice["pnl_net_usd"].sum()), 0),
            "oos_pnl": round(float(oos_slice["pnl_net_usd"].sum()), 0),
            "oos_profitable": bool(oos_slice["pnl_net_usd"].sum() > 0),
        })
    return results


def main() -> int:
    logger.info("Loading MES daily data…")
    df = load_mes()
    logger.info(f"  {len(df)} bars, {df.index[0].date()} → {df.index[-1].date()}")

    logger.info("Running backtest (production logic)…")
    trades = backtest_production_logic(df)
    logger.info(f"  {len(trades)} trades")

    stats = compute_stats(trades, "overnight_mes_production")
    logger.info(f"Stats: {stats}")

    wf = walk_forward(trades)
    if wf:
        n_prof = sum(1 for w in wf if w["oos_profitable"])
        avg_oos_sh = float(np.mean([w["oos_sharpe"] for w in wf]))
        avg_is_sh = float(np.mean([w["is_sharpe"] for w in wf]))
        logger.info(f"WF: {n_prof}/{len(wf)} profitable, IS {avg_is_sh:.2f} → OOS {avg_oos_sh:.2f}")
    else:
        n_prof = 0
        avg_oos_sh = 0.0
        avg_is_sh = 0.0

    # Exit reason breakdown
    exit_counts = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        f"# Walk-Forward Backtest — Overnight MES (REAL production logic) — {today}",
        "",
        "**Strategy** : reproduit EXACTEMENT la logique de strategies_v2/futures/overnight_buy_close.py",
        "- Signal : close[T-1] > EMA20[T-1]",
        "- Entry : open[T] (approximation du fill ~10 ET)",
        "- SL : entry - 30 points ($150)",
        "- TP : entry + 50 points ($250)",
        "- Exit : premier bar avec SL ou TP touche, cap 10 jours (safety)",
        "",
        f"**Data** : MES_1D.parquet ({len(df)} bars, {df.index[0].date()} → {df.index[-1].date()})",
        f"**Costs** : ${COMMISSION} commission + 1 tick slippage (${SLIPPAGE_TICKS*TICK_SIZE*MULT:.2f}) = ${COMMISSION+SLIPPAGE_TICKS*TICK_SIZE*MULT:.2f} round-trip",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total trades | **{stats['n_trades']}** |",
        f"| Win rate | {stats['win_rate']:.0%} |",
        f"| Avg PnL/trade | ${stats['avg_net_usd']} |",
        f"| Total net PnL | **${stats['total_net_usd']}** |",
        f"| **Sharpe (annualized)** | **{stats['sharpe']}** |",
        f"| Profit factor | {stats['profit_factor']} |",
        f"| Max DD | ${stats['max_dd_usd']} |",
        f"| Avg bars held | {stats['avg_bars_held']} |",
        "",
        "## Exit breakdown",
        "",
        "| Exit reason | Count | % |",
        "|---|---:|---:|",
    ]
    total_ex = sum(exit_counts.values())
    for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
        pct = count / total_ex * 100
        lines.append(f"| {reason} | {count} | {pct:.1f}% |")
    lines.append("")

    lines.append("## Walk-Forward")
    lines.append("")
    if wf:
        lines.append(f"- Profitable OOS windows: **{n_prof}/{len(wf)}**")
        lines.append(f"- IS avg Sharpe: {avg_is_sh:.2f}")
        lines.append(f"- OOS avg Sharpe: **{avg_oos_sh:.2f}**")
        ratio = avg_oos_sh / avg_is_sh if avg_is_sh > 0 else 0
        lines.append(f"- OOS/IS ratio: {ratio:.2f}")
        lines.append("")
        lines.append("| W | IS n | OOS n | IS Sh | OOS Sh | OOS PnL |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for w in wf:
            lines.append(
                f"| {w['window']} | {w['is_n']} | {w['oos_n']} | "
                f"{w['is_sharpe']} | {w['oos_sharpe']} | ${w['oos_pnl']} |"
            )
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    if stats["sharpe"] > 0.5 and n_prof >= len(wf) / 2 and wf:
        v = "✅ GO — edge reel, strategie peut rester LIVE"
    elif stats["sharpe"] > 0.2:
        v = "⚠️ BORDERLINE — edge marginal, garder en paper ou reduire sizing"
    else:
        v = "❌ KILL — pas d'edge demontre, arreter la strat"
    lines.append(f"**{v}**")
    lines.append("")
    lines.append("Comparison aux claims historiques :")
    lines.append(f"- overnight_buy_close.py docstring : **Sharpe 3.85** (208 trades, +$13,546)")
    lines.append(f"- paper_review_20260331 : **-0.70** (WF, mais test d'une AUTRE strat = signal_overnight_momentum)")
    lines.append(f"- backtest_overnight_indices.py filtered : **0.27** (close→open, pas la vraie logique)")
    lines.append(f"- **CE backtest (real prod logic)** : **{stats['sharpe']}**")
    lines.append("")

    out_file = OUT_DIR / f"wf_overnight_mes_real_{today}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Report: {out_file}")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
