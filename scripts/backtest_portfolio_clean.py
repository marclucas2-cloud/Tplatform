"""Backtest portefeuille CLEAN — seulement les strats positives sur 3 ans.
Overnight MES + VIX MR (SL elargi) + Gold-Equity Div.
"""
import warnings
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

MES_MULT = 5
COMM = 1.24
MAX_CONTRACTS = 2
START = "2023-04-01"
END = "2026-04-09"


def load(sym):
    for s in ["LONG", "1D"]:
        p = ROOT / f"data/futures/{sym}_{s}.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df
    raise FileNotFoundError(sym)


@dataclass
class Trade:
    strategy: str
    side: str
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0
    sl: float = 0
    tp: float = 0
    pnl: float = 0
    exit_reason: str = ""
    days_held: int = 0


def main():
    mes = load("MES")
    vix = load("VIX")
    mgc = load("MGC")

    c = mes["close"]
    ema20 = c.ewm(20).mean()
    vix_c = vix["close"].reindex(mes.index, method="ffill")
    mgc_c = mgc["close"].reindex(mes.index, method="ffill")

    # RSI14
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = delta.clip(upper=0).abs().rolling(14).mean()
    rsi14 = 100 - 100 / (1 + gain / loss)

    mes_ret5 = c.pct_change(5)
    mgc_ret5 = mgc_c.pct_change(5)

    positions = {}
    all_trades = []

    for i in range(200, len(mes)):
        date = mes.index[i]
        if date < pd.Timestamp(START, tz="UTC") or date > pd.Timestamp(END, tz="UTC"):
            continue

        price = c.iloc[i]
        h = mes["high"].iloc[i]
        l = mes["low"].iloc[i]
        o = mes["open"].iloc[i]

        # Check exits
        for key in list(positions.keys()):
            pos = positions[key]
            hit = None
            exit_p = 0
            days = (date - pd.Timestamp(pos.entry_date, tz="UTC")).days

            if pos.side == "BUY":
                if l <= pos.sl:
                    hit, exit_p = "SL", pos.sl
                elif h >= pos.tp:
                    hit, exit_p = "TP", pos.tp
            else:
                if h >= pos.sl:
                    hit, exit_p = "SL", pos.sl
                elif l <= pos.tp:
                    hit, exit_p = "TP", pos.tp

            # Time exits
            if "Overnight" in pos.strategy and days >= 1:
                hit, exit_p = "NEXT_OPEN", o
            elif "VIX" in pos.strategy:
                v = vix_c.iloc[i]
                if (not pd.isna(v) and v < 20) or days >= 10:
                    hit, exit_p = "VIX_EXIT" if not pd.isna(v) and v < 20 else "TIME", price
            elif "Gold" in pos.strategy and days >= 5:
                hit, exit_p = "TIME", price

            if hit:
                if pos.side == "BUY":
                    pos.pnl = (exit_p - pos.entry_price) * MES_MULT - COMM
                else:
                    pos.pnl = (pos.entry_price - exit_p) * MES_MULT - COMM
                pos.exit_date = str(date.date())
                pos.exit_price = exit_p
                pos.exit_reason = hit
                pos.days_held = days
                all_trades.append(pos)
                del positions[key]

        # Signals (sorted by priority)
        signals = []
        v = vix_c.iloc[i]
        r = rsi14.iloc[i]
        mr5 = mes_ret5.iloc[i]
        gr5 = mgc_ret5.iloc[i]
        e20 = ema20.iloc[i]

        # VIX MR (priority 10) — SL elargi 80 pts au lieu de 50
        if not pd.isna(v) and not pd.isna(r) and v > 25 and r < 30:
            signals.append(("VIX Mean Reversion", "BUY", price - 80, price + 120, 10))

        # Gold-Equity (priority 7)
        if not pd.isna(mr5) and not pd.isna(gr5):
            if mr5 > 0.02 and gr5 < -0.01:
                signals.append(("Gold-Equity Div", "SELL", price + 40, price - 60, 7))
            elif mr5 < -0.02 and gr5 > 0.01:
                signals.append(("Gold-Equity Div", "BUY", price - 40, price + 60, 7))

        # Overnight (priority 5)
        if not pd.isna(e20) and price > e20:
            signals.append(("Overnight MES", "BUY", price - 30, price + 50, 5))

        signals.sort(key=lambda x: x[4], reverse=True)

        # Open new positions
        slots = MAX_CONTRACTS - len(positions)
        opened = 0
        for name, side, sl, tp, prio in signals:
            if opened >= slots:
                break
            if any(p.strategy == name for p in positions.values()):
                continue
            key = f"MES_{name}"
            if key in positions:
                continue
            positions[key] = Trade(strategy=name, side=side, entry_date=str(date.date()),
                                   entry_price=price, sl=sl, tp=tp)
            opened += 1

    # Close remaining
    for key in list(positions.keys()):
        pos = positions[key]
        pos.pnl = (c.iloc[-1] - pos.entry_price) * MES_MULT - COMM if pos.side == "BUY" else (pos.entry_price - c.iloc[-1]) * MES_MULT - COMM
        pos.exit_date = str(mes.index[-1].date())
        pos.exit_price = c.iloc[-1]
        pos.exit_reason = "END"
        all_trades.append(pos)

    # RESULTS
    print("=" * 80)
    print(f"  PORTEFEUILLE CLEAN — 3 strats seulement — {START} to {END}")
    print("=" * 80)

    strats = defaultdict(list)
    for t in all_trades:
        strats[t.strategy].append(t)

    print(f"\n{'Strategy':<25} {'Trades':>6} {'Wins':>5} {'WR':>6} {'PnL':>10} {'Avg':>8} {'Sharpe':>7}")
    print("-" * 75)

    total_pnl = 0
    total_n = 0
    for name in ["VIX Mean Reversion", "Gold-Equity Div", "Overnight MES"]:
        st = strats.get(name, [])
        n = len(st)
        if n == 0:
            print(f"{name:<25} {'0':>6}")
            continue
        wins = sum(1 for t in st if t.pnl > 0)
        pnl = sum(t.pnl for t in st)
        pnls = [t.pnl for t in st]
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(52) if np.std(pnls) > 0 else 0
        print(f"{name:<25} {n:>6} {wins:>5} {wins/n*100:>5.0f}% ${pnl:>+9,.0f} ${pnl/n:>+7.0f} {sharpe:>7.2f}")
        total_pnl += pnl
        total_n += n

    total_wins = sum(1 for t in all_trades if t.pnl > 0)
    all_pnls = [t.pnl for t in all_trades]
    sharpe_all = np.mean(all_pnls) / np.std(all_pnls) * np.sqrt(52) if np.std(all_pnls) > 0 else 0
    cum = np.cumsum(all_pnls)
    max_dd = (cum - np.maximum.accumulate(cum)).min()
    pf = sum(t.pnl for t in all_trades if t.pnl > 0) / abs(sum(t.pnl for t in all_trades if t.pnl < 0)) if sum(t.pnl for t in all_trades if t.pnl < 0) != 0 else 0

    print("-" * 75)
    print(f"{'TOTAL':<25} {total_n:>6} {total_wins:>5} {total_wins/total_n*100:>5.0f}% ${total_pnl:>+9,.0f} ${total_pnl/total_n:>+7.0f} {sharpe_all:>7.2f}")
    print(f"\n  Return: {total_pnl/10000*100:+.1f}% | MaxDD: ${max_dd:,.0f} | PF: {pf:.2f}")

    # WF
    print("\n  Walk-Forward (6 fenetres):")
    ws = len(all_trades) // 6
    profit_w = 0
    for w in range(6):
        wt = all_trades[w*ws:min((w+1)*ws, len(all_trades))]
        if not wt:
            continue
        wp = sum(t.pnl for t in wt)
        tag = "PROFIT" if wp > 0 else "LOSS"
        if wp > 0:
            profit_w += 1
        print(f"    W{w+1}: {len(wt)} trades | ${wp:>+8,.0f} | {tag}")
    print(f"    WF: {profit_w}/6 {'PASS' if profit_w >= 3 else 'FAIL'}")

    # Monthly
    monthly = defaultdict(float)
    for t in all_trades:
        monthly[t.exit_date[:7]] += t.pnl
    profit_months = sum(1 for v in monthly.values() if v > 0)
    print(f"\n  Mois profitables: {profit_months}/{len(monthly)} ({profit_months/len(monthly)*100:.0f}%)")


if __name__ == "__main__":
    main()
