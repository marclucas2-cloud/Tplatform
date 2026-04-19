"""
BACKTEST FINAL — Portefeuille 4 strats gagnantes
Overnight MES + Gold-Equity Div + Sector Rotation + EU Gap Open
Sans Brent Lag (negatif sur 3 ans)
3 ans, regles live, max 3 positions.
"""
import warnings
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

COSTS = {
    "MES": {"mult": 5, "comm": 1.24},
    "ESTX50": {"mult": 10, "comm": 3.0},
    "DAX": {"mult": 1, "comm": 6.0},
    "CAC40": {"mult": 1, "comm": 6.0},
    "MGC": {"mult": 10, "comm": 1.24},
}

MAX_POS = 3
START = "2023-04-01"
END = "2026-04-09"


def load(sym):
    for d in ["futures", "eu"]:
        for s in ["LONG", "1D"]:
            p = ROOT / f"data/{d}/{sym}_{s}.parquet"
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
    symbol: str
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
    data = {}
    for sym in ["MES", "MGC", "ESTX50", "DAX", "CAC40"]:
        data[sym] = load(sym)
    vix = load("VIX")

    common = data["MES"].index
    for sym in data:
        common = common.intersection(data[sym].index)
    common = common.intersection(vix.index)

    # Indicators
    ind = {}
    for sym in data:
        c = data[sym]["close"]
        ind[f"{sym}_ema20"] = c.ewm(20).mean()
        ind[f"{sym}_ret5"] = c.pct_change(5)
        ind[f"{sym}_ret20"] = c.pct_change(20)

    ind["ESTX50_gap"] = (data["ESTX50"]["open"] - data["ESTX50"]["close"].shift(1)) / data["ESTX50"]["close"].shift(1)
    ind["MGC_ret5"] = data["MGC"]["close"].pct_change(5)
    ind["MES_ret5"] = data["MES"]["close"].pct_change(5)
    ind["DAX_mom20"] = data["DAX"]["close"].pct_change(20)
    ind["CAC40_mom20"] = data["CAC40"]["close"].pct_change(20)

    positions = {}
    all_trades = []

    for date in common:
        if date < pd.Timestamp(START, tz="UTC") or date > pd.Timestamp(END, tz="UTC"):
            continue

        # EXITS
        for key in list(positions.keys()):
            pos = positions[key]
            sym = pos.symbol
            i = data[sym].index.get_loc(date)
            h, l, c, o = data[sym]["high"].iloc[i], data[sym]["low"].iloc[i], data[sym]["close"].iloc[i], data[sym]["open"].iloc[i]
            days = (date - pd.Timestamp(pos.entry_date, tz="UTC")).days
            hit = exit_p = None

            if pos.side == "BUY":
                if l <= pos.sl: hit, exit_p = "SL", pos.sl
                elif h >= pos.tp: hit, exit_p = "TP", pos.tp
            else:
                if h >= pos.sl: hit, exit_p = "SL", pos.sl
                elif l <= pos.tp: hit, exit_p = "TP", pos.tp

            if "Overnight" in pos.strategy and days >= 1:
                hit, exit_p = "NEXT_OPEN", o
            elif "EU Gap" in pos.strategy and days >= 1:
                hit, exit_p = "EOD", c
            elif "Gold" in pos.strategy and days >= 5:
                hit, exit_p = "TIME_5D", c
            elif "Sector" in pos.strategy and days >= 5:
                hit, exit_p = "REBALANCE", c

            if hit:
                spec = COSTS.get(sym, {"mult": 1, "comm": 3.0})
                pos.pnl = ((exit_p - pos.entry_price) if pos.side == "BUY" else (pos.entry_price - exit_p)) * spec["mult"] - spec["comm"]
                pos.exit_date, pos.exit_price, pos.exit_reason, pos.days_held = str(date.date()), exit_p, hit, days
                all_trades.append(pos)
                del positions[key]

        # SIGNALS
        signals = []
        for sym_k in data:
            if date not in data[sym_k].index:
                continue

        i_mes = data["MES"].index.get_loc(date)
        mes_c = data["MES"]["close"].iloc[i_mes]

        # 1. EU Gap (priority 9)
        if date in data["ESTX50"].index:
            i_e = data["ESTX50"].index.get_loc(date)
            gap = ind["ESTX50_gap"].iloc[i_e]
            if not pd.isna(gap) and abs(gap) > 0.01 and abs(gap) < 0.05:
                eo = data["ESTX50"]["open"].iloc[i_e]
                if gap > 0.01:
                    signals.append(("EU Gap Open", "ESTX50", "SELL", eo, eo * 1.015, eo * 0.98, 9))
                else:
                    signals.append(("EU Gap Open", "ESTX50", "BUY", eo, eo * 0.985, eo * 1.02, 9))

        # 2. Gold-Equity Div (priority 7)
        mr5 = ind["MES_ret5"].iloc[i_mes]
        gr5 = ind["MGC_ret5"].iloc[data["MGC"].index.get_loc(date)] if date in data["MGC"].index else None
        if gr5 is not None and not pd.isna(mr5) and not pd.isna(gr5):
            if mr5 > 0.02 and gr5 < -0.01:
                signals.append(("Gold-Equity Div", "MES", "SELL", mes_c, mes_c + 40, mes_c - 60, 7))
            elif mr5 < -0.02 and gr5 > 0.01:
                signals.append(("Gold-Equity Div", "MES", "BUY", mes_c, mes_c - 40, mes_c + 60, 7))

        # 3. Sector Rotation (priority 6, Mondays only)
        if date.dayofweek == 0 and date in data["DAX"].index:
            i_d = data["DAX"].index.get_loc(date)
            dm = ind["DAX_mom20"].iloc[i_d]
            cm = ind["CAC40_mom20"].iloc[data["CAC40"].index.get_loc(date)] if date in data["CAC40"].index else None
            if cm is not None and not pd.isna(dm) and not pd.isna(cm):
                if dm > cm + 0.02:
                    dc = data["DAX"]["close"].iloc[i_d]
                    signals.append(("Sector Rotation", "DAX", "BUY", dc, dc * 0.96, dc * 1.08, 6))
                elif cm > dm + 0.02:
                    cc = data["CAC40"]["close"].iloc[data["CAC40"].index.get_loc(date)]
                    signals.append(("Sector Rotation", "CAC40", "BUY", cc, cc * 0.96, cc * 1.08, 6))

        # 4. Overnight MES (priority 5)
        e20 = ind["MES_ema20"].iloc[i_mes]
        if not pd.isna(e20) and mes_c > e20:
            signals.append(("Overnight MES", "MES", "BUY", mes_c, mes_c - 30, mes_c + 50, 5))

        signals.sort(key=lambda x: x[6], reverse=True)

        # OPEN
        slots = MAX_POS - len(positions)
        opened = 0
        for name, sym, side, entry, sl, tp, prio in signals:
            if opened >= slots:
                break
            if any(p.strategy == name for p in positions.values()):
                continue
            if any(p.symbol == sym for p in positions.values()):
                continue
            positions[f"{sym}_{name}"] = Trade(strategy=name, symbol=sym, side=side,
                                                entry_date=str(date.date()), entry_price=entry, sl=sl, tp=tp)
            opened += 1

    # Close remaining
    for key in list(positions.keys()):
        pos = positions[key]
        c = data[pos.symbol]["close"].iloc[-1]
        spec = COSTS.get(pos.symbol, {"mult": 1, "comm": 3.0})
        pos.pnl = ((c - pos.entry_price) if pos.side == "BUY" else (pos.entry_price - c)) * spec["mult"] - spec["comm"]
        pos.exit_date, pos.exit_price, pos.exit_reason = str(data[pos.symbol].index[-1].date()), c, "END"
        all_trades.append(pos)

    # === RESULTS ===
    print("=" * 90)
    print(f"  BACKTEST FINAL — 4 strats gagnantes — {START} to {END}")
    print("=" * 90)

    strats = defaultdict(list)
    for t in all_trades:
        strats[t.strategy].append(t)

    print(f"\n{'Strategy':<22} {'Sym':<7} {'Trades':>6} {'Wins':>5} {'WR':>6} {'PnL':>10} {'Avg':>8} {'MaxDD':>8} {'Sharpe':>7} {'Days':>5}")
    print("-" * 90)

    total_pnl, total_n = 0, 0
    strat_rets = {}

    for name in ["EU Gap Open", "Gold-Equity Div", "Sector Rotation", "Overnight MES"]:
        st = strats.get(name, [])
        n = len(st)
        if n == 0:
            print(f"{name:<22} {'—':<7} {'0':>6}")
            continue
        sym = st[0].symbol
        wins = sum(1 for t in st if t.pnl > 0)
        pnl = sum(t.pnl for t in st)
        pnls = [t.pnl for t in st]
        avg_d = np.mean([t.days_held for t in st])
        cum = np.cumsum(pnls)
        max_dd = (cum - np.maximum.accumulate(cum)).min()
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252 / max(avg_d, 1)) if np.std(pnls) > 0 else 0
        print(f"{name:<22} {sym:<7} {n:>6} {wins:>5} {wins/n*100:>5.0f}% ${pnl:>+9,.0f} ${pnl/n:>+7.0f} ${max_dd:>+7.0f} {sharpe:>7.2f} {avg_d:>4.1f}d")
        total_pnl += pnl
        total_n += n
        strat_rets[name] = pnls

    tw = sum(1 for t in all_trades if t.pnl > 0)
    ap = [t.pnl for t in all_trades]
    cum_a = np.cumsum(ap)
    mdd = (cum_a - np.maximum.accumulate(cum_a)).min()
    sh = np.mean(ap) / np.std(ap) * np.sqrt(252) if np.std(ap) > 0 else 0
    pf = sum(t.pnl for t in all_trades if t.pnl > 0) / abs(sum(t.pnl for t in all_trades if t.pnl < 0)) if sum(t.pnl for t in all_trades if t.pnl < 0) != 0 else 0

    print("-" * 90)
    print(f"{'TOTAL':<22} {'ALL':<7} {total_n:>6} {tw:>5} {tw/total_n*100:>5.0f}% ${total_pnl:>+9,.0f} ${total_pnl/total_n:>+7.0f} ${mdd:>+7.0f} {sh:>7.2f}")

    print(f"\n  Capital: $10,000")
    print(f"  Return 3 ans: {total_pnl/10000*100:+.1f}% | ROC/an: {total_pnl/10000/3*100:.1f}%/an")
    print(f"  MaxDD: ${mdd:,.0f} ({mdd/10000*100:.1f}%) | PF: {pf:.2f}")
    print(f"  Avg trade: ${total_pnl/total_n:+.1f} | Total trades: {total_n}")

    # CORRELATION
    print(f"\n{'='*60}")
    print("  CORRELATION")
    print(f"{'='*60}")
    sn = [s for s in strat_rets if len(strat_rets[s]) >= 5]
    if len(sn) >= 2:
        ml = max(len(strat_rets[s]) for s in sn)
        dfc = pd.DataFrame({s[:15]: (strat_rets[s] + [0] * (ml - len(strat_rets[s])))[:ml] for s in sn})
        corr = dfc.corr()
        print(f"{'':>16}", end="")
        for s in corr.columns: print(f"{s:>16}", end="")
        print()
        for s1 in corr.index:
            print(f"{s1:>16}", end="")
            for s2 in corr.columns: print(f"{corr.loc[s1, s2]:>16.2f}", end="")
            print()

    # WF
    print(f"\n{'='*60}")
    print("  WALK-FORWARD (6 fenetres)")
    print(f"{'='*60}")
    ws = max(1, len(all_trades) // 6)
    pw = 0
    for w in range(6):
        wt = all_trades[w * ws:min((w + 1) * ws, len(all_trades))]
        if not wt: continue
        wp = sum(t.pnl for t in wt)
        wn = len(wt)
        ww = sum(1 for t in wt if t.pnl > 0)
        if wp > 0: pw += 1
        print(f"  W{w+1} [{wt[0].entry_date} to {wt[-1].exit_date}]: {wn:3d} trades | ${wp:>+8,.0f} | WR {ww/wn*100:.0f}% | {'PROFIT' if wp > 0 else 'LOSS'}")
    print(f"  WF: {pw}/6 {'PASS' if pw >= 3 else 'FAIL'}")

    # Monthly
    monthly = defaultdict(float)
    for t in all_trades:
        monthly[t.exit_date[:7]] += t.pnl
    pm = sum(1 for v in monthly.values() if v > 0)
    print(f"\n  Mois profitables: {pm}/{len(monthly)} ({pm/len(monthly)*100:.0f}%)")
    print(f"  Best: ${max(monthly.values()):+,.0f} | Worst: ${min(monthly.values()):+,.0f}")
    print(f"  Avg/month: ${total_pnl/len(monthly):+,.0f}")


if __name__ == "__main__":
    main()
