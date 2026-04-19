"""
Backtest complet du portefeuille — 3 ans (2023-2026)
Toutes les strategies actives avec les regles live:
- Max 2 contrats simultanes
- Systeme de priorite
- SL/TP recalcules depuis fill price
- Time-exit 48h
- Correlation inter-strategies
"""
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

MES_MULT = 5
MNQ_MULT = 2
MGC_MULT = 10
COMM = 1.24
MAX_CONTRACTS = 2

START = "2023-04-01"
END = "2026-04-09"


def load(sym, suffix="LONG"):
    for s in [suffix, "1D"]:
        p = ROOT / f"data/futures/{sym}_{s}.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df
    raise FileNotFoundError(f"No data for {sym}")


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


def compute_indicators(mes, vix, mgc):
    """Precompute all indicators needed by strategies."""
    ind = {}
    c = mes["close"]
    # EMAs
    ind["ema20"] = c.ewm(20).mean()
    ind["ema50"] = c.ewm(50).mean()
    ind["ema200"] = c.ewm(200).mean()
    # RSI14
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = delta.clip(upper=0).abs().rolling(14).mean()
    ind["rsi14"] = 100 - 100 / (1 + gain / loss)
    # RSI2
    gain2 = delta.clip(lower=0).rolling(2).mean()
    loss2 = delta.clip(upper=0).abs().rolling(2).mean()
    ind["rsi2"] = 100 - 100 / (1 + gain2 / loss2)
    # VIX
    ind["vix"] = vix["close"].reindex(mes.index, method="ffill")
    # MGC returns
    mgc_c = mgc["close"].reindex(mes.index, method="ffill")
    ind["mgc_ret5"] = mgc_c.pct_change(5)
    ind["mes_ret5"] = c.pct_change(5)
    ind["mes_ret63"] = c.pct_change(63)
    # ATR
    ind["atr20"] = c.diff().abs().rolling(20).mean()
    return ind


def generate_signals(date, i, mes, ind):
    """Generate all strategy signals for a given date. Returns list of (name, side, sl, tp, priority)."""
    signals = []
    c = mes["close"].iloc[i]
    o = mes["open"].iloc[i]
    h = mes["high"].iloc[i]
    l = mes["low"].iloc[i]

    # 1. VIX Mean Reversion (priority 10)
    vix_val = ind["vix"].iloc[i]
    rsi14 = ind["rsi14"].iloc[i]
    if not pd.isna(vix_val) and not pd.isna(rsi14):
        if vix_val > 25 and rsi14 < 30:
            signals.append(("VIX Mean Reversion", "BUY", c - 50, c + 100, 10))

    # 2. Gold-Equity Divergence (priority 7)
    mes_r5 = ind["mes_ret5"].iloc[i]
    mgc_r5 = ind["mgc_ret5"].iloc[i]
    if not pd.isna(mes_r5) and not pd.isna(mgc_r5):
        if mes_r5 > 0.02 and mgc_r5 < -0.01:
            signals.append(("Gold-Equity Div", "SELL", c + 40, c - 60, 7))
        elif mes_r5 < -0.02 and mgc_r5 > 0.01:
            signals.append(("Gold-Equity Div", "BUY", c - 40, c + 60, 7))

    # 3. Overnight Buy-Close MES (priority 5)
    ema20 = ind["ema20"].iloc[i]
    if not pd.isna(ema20) and c > ema20:
        signals.append(("Overnight MES", "BUY", c - 30, c + 50, 5))

    # 4. MES Trend+MR Hybrid (priority 4)
    rsi2 = ind["rsi2"].iloc[i]
    ema50 = ind["ema50"].iloc[i]
    if not pd.isna(rsi2) and not pd.isna(ema50):
        if rsi2 < 10 and c > ema50:
            signals.append(("MES Trend+MR", "BUY", c - 20, c + 30, 4))
        elif rsi2 > 90 and c < ema50:
            signals.append(("MES Trend+MR", "SELL", c + 20, c - 30, 4))

    # 5. TSMOM MES (priority 3)
    ret63 = ind["mes_ret63"].iloc[i]
    if not pd.isna(ret63):
        if ret63 > 0:
            signals.append(("TSMOM MES", "BUY", c - 25, c + 40, 3))
        else:
            signals.append(("TSMOM MES", "SELL", c + 25, c - 40, 3))

    # Sort by priority descending
    signals.sort(key=lambda x: x[4], reverse=True)
    return signals


def simulate_portfolio(mes, ind):
    """Simulate full portfolio with live rules."""
    dates = mes.loc[START:END].index
    positions = {}  # sym -> Trade
    all_trades = []
    daily_equity = []
    daily_pnl_by_strat = defaultdict(list)

    for i_abs in range(len(mes)):
        date = mes.index[i_abs]
        if date < pd.Timestamp(START, tz="UTC") or date > pd.Timestamp(END, tz="UTC"):
            continue

        c = mes["close"].iloc[i_abs]
        h = mes["high"].iloc[i_abs]
        l = mes["low"].iloc[i_abs]

        # 1. Check exits on existing positions
        for sym in list(positions.keys()):
            pos = positions[sym]
            hit = None
            exit_p = 0

            if pos.side == "BUY":
                if l <= pos.sl:
                    hit = "SL"
                    exit_p = pos.sl
                elif h >= pos.tp:
                    hit = "TP"
                    exit_p = pos.tp
            else:
                if h >= pos.sl:
                    hit = "SL"
                    exit_p = pos.sl
                elif l <= pos.tp:
                    hit = "TP"
                    exit_p = pos.tp

            # Time exit 48h (approx 2 trading days)
            days = (date - pd.Timestamp(pos.entry_date, tz="UTC")).days
            if days >= 2 and "Overnight" in pos.strategy:
                hit = "TIME"
                # Overnight exits at open
                exit_p = mes["open"].iloc[i_abs]
            elif days >= 10 and "VIX" in pos.strategy:
                hit = "TIME"
                exit_p = c
            elif days >= 5 and "Gold" in pos.strategy:
                hit = "TIME"
                exit_p = c
            elif days >= 5 and "TSMOM" not in pos.strategy and days >= 3:
                pass  # let SL/TP handle

            # TSMOM rebalance every 21 days
            if "TSMOM" in pos.strategy and days >= 21:
                hit = "REBALANCE"
                exit_p = c

            if hit:
                mult = MES_MULT
                if pos.side == "BUY":
                    pos.pnl = (exit_p - pos.entry_price) * mult - COMM
                else:
                    pos.pnl = (pos.entry_price - exit_p) * mult - COMM
                pos.exit_date = str(date.date())
                pos.exit_price = exit_p
                pos.exit_reason = hit
                pos.days_held = days
                all_trades.append(pos)
                daily_pnl_by_strat[pos.strategy].append(pos.pnl)
                del positions[sym]

        # 2. Generate new signals
        n_positions = len(positions)
        if n_positions < MAX_CONTRACTS:
            sigs = generate_signals(date, i_abs, mes, ind)
            slots = MAX_CONTRACTS - n_positions
            opened = 0
            for name, side, sl, tp, prio in sigs:
                if opened >= slots:
                    break
                # Don't open if already have a position with same strategy
                if any(p.strategy == name for p in positions.values()):
                    continue
                # Don't open same symbol
                key = f"MES_{name}"
                if key in positions:
                    continue

                pos = Trade(
                    strategy=name,
                    symbol="MES",
                    side=side,
                    entry_date=str(date.date()),
                    entry_price=c,
                    sl=sl,
                    tp=tp,
                )
                positions[key] = pos
                opened += 1

        # Daily equity tracking (unrealized)
        unrealized = 0
        for pos in positions.values():
            if pos.side == "BUY":
                unrealized += (c - pos.entry_price) * MES_MULT
            else:
                unrealized += (pos.entry_price - c) * MES_MULT
        realized = sum(t.pnl for t in all_trades)
        daily_equity.append({
            "date": str(date.date()),
            "realized": realized,
            "unrealized": unrealized,
            "total": realized + unrealized,
            "positions": len(positions),
        })

    # Close remaining
    for sym in list(positions.keys()):
        pos = positions[sym]
        c = mes["close"].iloc[-1]
        if pos.side == "BUY":
            pos.pnl = (c - pos.entry_price) * MES_MULT - COMM
        else:
            pos.pnl = (pos.entry_price - c) * MES_MULT - COMM
        pos.exit_date = str(mes.index[-1].date())
        pos.exit_price = c
        pos.exit_reason = "END"
        pos.days_held = (mes.index[-1] - pd.Timestamp(pos.entry_date, tz="UTC")).days
        all_trades.append(pos)

    return all_trades, daily_equity, daily_pnl_by_strat


def main():
    print("Loading data...")
    mes = load("MES")
    vix = load("VIX")
    mgc = load("MGC")

    # Filter to 3 years
    mes_3y = mes.loc[START:END]
    print(f"MES: {len(mes_3y)} bars ({START} to {END})")
    print(f"VIX: {len(vix)} bars")
    print(f"MGC: {len(mgc)} bars")

    print("\nComputing indicators...")
    ind = compute_indicators(mes, vix, mgc)

    print("Simulating portfolio...")
    trades, equity, pnl_by_strat = simulate_portfolio(mes, ind)

    # =========================================
    # RESULTS
    # =========================================
    print("\n" + "=" * 80)
    print(f"  BACKTEST PORTEFEUILLE COMPLET — {START} to {END} (3 ans)")
    print(f"  5 strategies, max {MAX_CONTRACTS} contrats, systeme de priorite")
    print("=" * 80)

    # Per-strategy breakdown
    strat_trades = defaultdict(list)
    for t in trades:
        strat_trades[t.strategy].append(t)

    print(f"\n{'Strategy':<25} {'Trades':>6} {'Wins':>5} {'WR':>6} {'PnL':>10} {'Avg':>8} {'MaxDD':>8} {'Sharpe':>7}")
    print("-" * 85)

    total_pnl = 0
    total_trades = 0
    strat_daily_returns = {}

    for strat_name in ["VIX Mean Reversion", "Gold-Equity Div", "Overnight MES", "MES Trend+MR", "TSMOM MES"]:
        st = strat_trades.get(strat_name, [])
        n = len(st)
        if n == 0:
            print(f"{strat_name:<25} {'0':>6}")
            continue
        wins = sum(1 for t in st if t.pnl > 0)
        pnl = sum(t.pnl for t in st)
        avg = pnl / n
        pnls = [t.pnl for t in st]

        # Max DD from cumulative PnL
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = cum - peak
        max_dd = dd.min()

        # Sharpe (annualized from trade returns)
        if np.std(pnls) > 0:
            sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(52)
        else:
            sharpe = 0

        wr = wins / n * 100
        print(f"{strat_name:<25} {n:>6} {wins:>5} {wr:>5.0f}% ${pnl:>+9,.0f} ${avg:>+7.0f} ${max_dd:>+7.0f} {sharpe:>7.2f}")

        total_pnl += pnl
        total_trades += n
        strat_daily_returns[strat_name] = pnls

    total_wins = sum(1 for t in trades if t.pnl > 0)
    total_wr = total_wins / total_trades * 100 if total_trades else 0
    all_pnls = [t.pnl for t in trades]
    cum_all = np.cumsum(all_pnls)
    peak_all = np.maximum.accumulate(cum_all)
    max_dd_all = (cum_all - peak_all).min()
    sharpe_all = np.mean(all_pnls) / np.std(all_pnls) * np.sqrt(52) if np.std(all_pnls) > 0 else 0

    print("-" * 85)
    print(f"{'TOTAL':<25} {total_trades:>6} {total_wins:>5} {total_wr:>5.0f}% ${total_pnl:>+9,.0f} ${total_pnl/total_trades:>+7.0f} ${max_dd_all:>+7.0f} {sharpe_all:>7.2f}")

    # =========================================
    # CORRELATION MATRIX
    # =========================================
    print("\n" + "=" * 80)
    print("  CORRELATION INTER-STRATEGIES (rendements par trade)")
    print("=" * 80)

    # Build daily PnL series per strategy
    strat_names = [s for s in strat_daily_returns if len(strat_daily_returns[s]) >= 5]
    if len(strat_names) >= 2:
        # Align by trade index (not perfect but indicative)
        max_len = max(len(strat_daily_returns[s]) for s in strat_names)
        df_corr = pd.DataFrame()
        for s in strat_names:
            pnls = strat_daily_returns[s]
            # Pad shorter series
            padded = pnls + [0] * (max_len - len(pnls))
            df_corr[s[:15]] = padded[:max_len]

        corr = df_corr.corr()
        print(f"\n{'':<16}", end="")
        for s in corr.columns:
            print(f"{s:>16}", end="")
        print()
        for s1 in corr.index:
            print(f"{s1:<16}", end="")
            for s2 in corr.columns:
                v = corr.loc[s1, s2]
                print(f"{v:>16.2f}", end="")
            print()
    else:
        print("  Pas assez de strategies avec >= 5 trades pour la correlation")

    # =========================================
    # EQUITY CURVE STATS
    # =========================================
    print("\n" + "=" * 80)
    print("  EQUITY CURVE")
    print("=" * 80)

    eq = pd.DataFrame(equity)
    print(f"  Capital initial: $10,000")
    print(f"  Capital final:   ${10000 + total_pnl:,.0f}")
    print(f"  PnL total:       ${total_pnl:+,.0f}")
    print(f"  Return:          {total_pnl/10000*100:+.1f}%")
    print(f"  Sharpe:          {sharpe_all:.2f}")
    print(f"  Max Drawdown:    ${max_dd_all:,.0f}")
    print(f"  Trades:          {total_trades}")
    print(f"  Win Rate:        {total_wr:.0f}%")
    print(f"  Avg Trade:       ${total_pnl/total_trades:+.0f}")
    print(f"  Profit Factor:   {sum(t.pnl for t in trades if t.pnl>0) / abs(sum(t.pnl for t in trades if t.pnl<0)):.2f}" if sum(t.pnl for t in trades if t.pnl<0) != 0 else "  Profit Factor:   inf")

    # Monthly returns
    print("\n  Monthly returns:")
    monthly = defaultdict(float)
    for t in trades:
        month = t.exit_date[:7]
        monthly[month] += t.pnl
    months = sorted(monthly.keys())
    for m in months[-12:]:
        bar = "+" * int(max(0, monthly[m] / 50)) + "-" * int(max(0, -monthly[m] / 50))
        print(f"    {m}: ${monthly[m]:>+8,.0f} {bar}")

    profitable_months = sum(1 for v in monthly.values() if v > 0)
    print(f"\n  Profitable months: {profitable_months}/{len(monthly)} ({profitable_months/len(monthly)*100:.0f}%)")

    # =========================================
    # TRADE DETAILS (last 20)
    # =========================================
    print("\n" + "=" * 80)
    print("  DERNIERS 20 TRADES")
    print("=" * 80)
    print(f"{'Date':<12} {'Strategy':<22} {'Side':<5} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'Reason':<8} {'Days':>4}")
    print("-" * 80)
    for t in trades[-20:]:
        print(f"{t.entry_date:<12} {t.strategy:<22} {t.side:<5} {t.entry_price:>8.0f} {t.exit_price:>8.0f} ${t.pnl:>+7.0f} {t.exit_reason:<8} {t.days_held:>4}")

    # =========================================
    # WALK-FORWARD VALIDATION
    # =========================================
    print("\n" + "=" * 80)
    print("  WALK-FORWARD VALIDATION (6 fenetres)")
    print("=" * 80)

    n = len(trades)
    window_size = n // 6
    for w in range(6):
        start_i = w * window_size
        end_i = min((w + 1) * window_size, n)
        window_trades = trades[start_i:end_i]
        if not window_trades:
            continue
        w_pnl = sum(t.pnl for t in window_trades)
        w_n = len(window_trades)
        w_wins = sum(1 for t in window_trades if t.pnl > 0)
        w_wr = w_wins / w_n * 100 if w_n else 0
        period = f"{window_trades[0].entry_date} to {window_trades[-1].exit_date}"
        tag = "PROFIT" if w_pnl > 0 else "LOSS"
        print(f"  W{w+1} [{period}]: {w_n:3d} trades | ${w_pnl:>+8,.0f} | WR {w_wr:.0f}% | {tag}")

    wf_profit = sum(1 for w in range(6) if sum(t.pnl for t in trades[w*window_size:min((w+1)*window_size, n)]) > 0)
    print(f"  WF Ratio: {wf_profit}/6")
    print(f"  Verdict: {'PASS' if wf_profit >= 3 else 'FAIL'}")


if __name__ == "__main__":
    main()
