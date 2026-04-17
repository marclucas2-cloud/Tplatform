"""Backtest gold_trend_mgc: Fixed SL vs Trailing Stop variants.

Compares:
  A) V1 baseline: fixed SL 0.4%, TP 0.8%
  B) Trailing 0.4%: SL follows price up, never down
  C) Trailing 0.6%: wider trailing
  D) Trailing 0.4% + TP 1.5%: trailing with wider target
  E) Trailing 0.3%: tighter trailing
  F) Trailing 0.4% no TP: pure trailing, no take-profit cap

Uses MGC_1D.parquet (5Y daily). EMA20 trend filter.
Costs: $0.97 commission per contract + 0.5 tick slippage ($5).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    side: str
    exit_reason: str
    pnl_gross: float
    pnl_net: float
    bars_held: int


@dataclass
class Variant:
    name: str
    sl_pct: float
    tp_pct: float | None
    trailing: bool
    max_hold: int = 10


COMMISSION = 0.97
SLIPPAGE_TICKS = 0.5
TICK_SIZE = 0.1
TICK_VALUE = 1.0  # MGC: $1 per 0.1 point (multiplier 10)
SLIPPAGE_USD = SLIPPAGE_TICKS * TICK_VALUE
COST_PER_TRADE = COMMISSION + SLIPPAGE_USD  # entry + exit


def backtest_variant(df: pd.DataFrame, variant: Variant, ema_period: int = 20) -> list[Trade]:
    """Run backtest for a single variant."""
    df = df.copy()
    df["ema"] = df["close"].ewm(span=ema_period, adjust=False).mean()

    trades: list[Trade] = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    sl_price = 0.0
    tp_price = 0.0
    highest_since_entry = 0.0

    for i in range(ema_period, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if in_position:
            bars_held = i - entry_idx

            # Update trailing stop
            if variant.trailing and row.high > highest_since_entry:
                highest_since_entry = row.high
                new_sl = highest_since_entry * (1 - variant.sl_pct)
                if new_sl > sl_price:
                    sl_price = new_sl

            # Check SL
            if row.low <= sl_price:
                exit_price = min(sl_price, row.open)  # gap protection
                pnl_gross = (exit_price - entry_price) * 10  # multiplier
                pnl_net = pnl_gross - COST_PER_TRADE * 2
                trades.append(Trade(
                    entry_date=df.index[entry_idx].strftime("%Y-%m-%d"),
                    exit_date=row.name.strftime("%Y-%m-%d"),
                    entry_price=entry_price, exit_price=exit_price,
                    side="BUY", exit_reason="SL" if not variant.trailing else "TRAIL",
                    pnl_gross=pnl_gross, pnl_net=pnl_net, bars_held=bars_held,
                ))
                in_position = False
                continue

            # Check TP
            if variant.tp_pct and row.high >= tp_price:
                exit_price = max(tp_price, row.open)
                pnl_gross = (exit_price - entry_price) * 10
                pnl_net = pnl_gross - COST_PER_TRADE * 2
                trades.append(Trade(
                    entry_date=df.index[entry_idx].strftime("%Y-%m-%d"),
                    exit_date=row.name.strftime("%Y-%m-%d"),
                    entry_price=entry_price, exit_price=exit_price,
                    side="BUY", exit_reason="TP",
                    pnl_gross=pnl_gross, pnl_net=pnl_net, bars_held=bars_held,
                ))
                in_position = False
                continue

            # Check max hold
            if bars_held >= variant.max_hold:
                exit_price = row.close
                pnl_gross = (exit_price - entry_price) * 10
                pnl_net = pnl_gross - COST_PER_TRADE * 2
                trades.append(Trade(
                    entry_date=df.index[entry_idx].strftime("%Y-%m-%d"),
                    exit_date=row.name.strftime("%Y-%m-%d"),
                    entry_price=entry_price, exit_price=exit_price,
                    side="BUY", exit_reason="MAX_HOLD",
                    pnl_gross=pnl_gross, pnl_net=pnl_net, bars_held=bars_held,
                ))
                in_position = False
                continue

        else:
            # Entry: close > EMA20
            if prev.close > df.iloc[i - 1]["ema"]:
                entry_price = row.open + SLIPPAGE_TICKS * TICK_SIZE
                entry_idx = i
                highest_since_entry = row.high
                sl_price = entry_price * (1 - variant.sl_pct)
                tp_price = entry_price * (1 + variant.tp_pct) if variant.tp_pct else 1e9
                in_position = True

    return trades


def summarize(trades: list[Trade], name: str) -> dict:
    if not trades:
        return {"name": name, "trades": 0}
    pnls = [t.pnl_net for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    cumsum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0
    avg_bars = np.mean([t.bars_held for t in trades])

    exits = {}
    for t in trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

    # Sharpe (annualized from daily-ish trades)
    if len(pnls) > 1:
        trades_per_year = len(pnls) / 5  # ~5 years of data
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(trades_per_year) if np.std(pnls) > 0 else 0
    else:
        sharpe = 0

    return {
        "name": name,
        "trades": len(trades),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "max_dd": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "avg_bars": round(avg_bars, 1),
        "exits": exits,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 999,
    }


def main():
    df = pd.read_parquet(ROOT / "data" / "futures" / "MGC_1D.parquet")
    print(f"MGC_1D: {len(df)} bars, {df.index[0].date()} to {df.index[-1].date()}\n")

    variants = [
        Variant("A: Fixed SL=0.4% TP=0.8%", sl_pct=0.004, tp_pct=0.008, trailing=False),
        Variant("B: Trail 0.4% TP=0.8%", sl_pct=0.004, tp_pct=0.008, trailing=True),
        Variant("C: Trail 0.6% TP=0.8%", sl_pct=0.006, tp_pct=0.008, trailing=True),
        Variant("D: Trail 0.4% TP=1.5%", sl_pct=0.004, tp_pct=0.015, trailing=True),
        Variant("E: Trail 0.3% TP=0.8%", sl_pct=0.003, tp_pct=0.008, trailing=True),
        Variant("F: Trail 0.4% no TP", sl_pct=0.004, tp_pct=None, trailing=True),
        Variant("G: Trail 0.6% no TP", sl_pct=0.006, tp_pct=None, trailing=True),
        Variant("H: Trail 0.8% TP=2%", sl_pct=0.008, tp_pct=0.02, trailing=True),
    ]

    results = []
    for v in variants:
        trades = backtest_variant(df, v)
        s = summarize(trades, v.name)
        results.append(s)

    # Display
    print(f"{'Variant':<30} {'Trades':>6} {'PnL':>10} {'WR%':>6} {'MaxDD':>10} {'Sharpe':>7} {'PF':>6} {'AvgBars':>8} Exits")
    print("-" * 110)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['name']:<30} {'0':>6}")
            continue
        exits_str = " ".join(f"{k}={v}" for k, v in r.get("exits", {}).items())
        print(
            f"{r['name']:<30} {r['trades']:>6} "
            f"${r['total_pnl']:>9,.0f} {r['win_rate']:>5.1f}% "
            f"${r['max_dd']:>9,.0f} {r['sharpe']:>6.2f} {r['profit_factor']:>5.2f} "
            f"{r['avg_bars']:>7.1f}  {exits_str}"
        )

    # Best by Sharpe
    best = max(results, key=lambda r: r.get("sharpe", 0))
    print(f"\nBest by Sharpe: {best['name']} (Sharpe={best['sharpe']}, PnL=${best['total_pnl']:,.0f})")

    # Best by PnL
    best_pnl = max(results, key=lambda r: r.get("total_pnl", 0))
    print(f"Best by PnL:    {best_pnl['name']} (PnL=${best_pnl['total_pnl']:,.0f}, Sharpe={best_pnl['sharpe']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
