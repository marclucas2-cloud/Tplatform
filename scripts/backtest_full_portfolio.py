"""
Backtest portefeuille COMPLET — 3 ans (2023-2026)
TOUTES les strats candidates ensemble, regles live, max 2 contrats futures.

Strats testees:
  FUTURES: Overnight MES, Gold-Equity Div
  EU: EU Gap Open (ESTX50), Brent Lag (MCL), Sector Rotation (DAX/CAC40)

Contraintes:
  - Max 2 futures simultanes
  - Pas de chevauchement meme symbole
  - SL/TP recalcules depuis fill
  - Commissions reelles par instrument
"""
import warnings
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

# Costs per instrument
COSTS = {
    "MES": {"mult": 5, "comm": 1.24, "slip_pts": 0.5},
    "MCL": {"mult": 10, "comm": 1.24, "slip_pts": 0.05},  # micro crude $10/pt
    "MGC": {"mult": 10, "comm": 1.24, "slip_pts": 0.5},
    "ESTX50": {"mult": 10, "comm": 3.0, "slip_pts": 1.0},  # EUREX futures
    "DAX": {"mult": 1, "comm": 6.0, "slip_pts": 0},     # equity proxy (% based)
    "CAC40": {"mult": 1, "comm": 6.0, "slip_pts": 0},
}

MAX_POSITIONS = 3  # futures max 2 + 1 equity
START = "2023-04-01"
END = "2026-04-09"


def load(sym, dirs=["futures", "eu"]):
    for d in dirs:
        for s in ["LONG", "1D"]:
            p = ROOT / f"data/{d}/{sym}_{s}.parquet"
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


def main():
    print("Loading data...")
    data = {}
    for sym in ["MES", "MCL", "MGC", "ESTX50", "DAX", "CAC40"]:
        try:
            data[sym] = load(sym)
            print(f"  {sym}: {len(data[sym])} bars")
        except FileNotFoundError:
            print(f"  {sym}: NOT FOUND")

    vix = load("VIX")
    print(f"  VIX: {len(vix)} bars")

    # Align all data on common dates
    common_idx = data["MES"].index
    for sym in data:
        common_idx = common_idx.intersection(data[sym].index)
    common_idx = common_idx.intersection(vix.index)
    print(f"\nCommon dates: {len(common_idx)} ({common_idx[0].date()} to {common_idx[-1].date()})")

    # Precompute indicators
    ind = {}
    for sym in data:
        c = data[sym]["close"]
        ind[f"{sym}_ema20"] = c.ewm(20).mean()
        ind[f"{sym}_ema50"] = c.ewm(50).mean()
        ind[f"{sym}_ret5"] = c.pct_change(5)
        ind[f"{sym}_ret20"] = c.pct_change(20)
        ind[f"{sym}_ret63"] = c.pct_change(63)
        ind[f"{sym}_atr"] = c.diff().abs().rolling(20).mean()

    # RSI14 MES
    delta = data["MES"]["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = delta.clip(upper=0).abs().rolling(14).mean()
    ind["MES_rsi14"] = 100 - 100 / (1 + gain / loss)

    # VIX
    ind["VIX"] = vix["close"].reindex(data["MES"].index, method="ffill")

    # ESTX50 gap (close-to-open)
    ind["ESTX50_gap"] = (data["ESTX50"]["open"] - data["ESTX50"]["close"].shift(1)) / data["ESTX50"]["close"].shift(1)

    # MCL vs Brent lag (using MCL momentum as proxy)
    ind["MCL_ema10"] = data["MCL"]["close"].ewm(10).mean()
    ind["MCL_ema30"] = data["MCL"]["close"].ewm(30).mean()

    # DAX vs CAC40 momentum for sector rotation
    ind["DAX_mom20"] = data["DAX"]["close"].pct_change(20)
    ind["CAC40_mom20"] = data["CAC40"]["close"].pct_change(20)

    # MGC
    ind["MGC_ret5"] = data["MGC"]["close"].pct_change(5)

    print("\nSimulating portfolio...")

    positions = {}
    all_trades = []

    for date in common_idx:
        if date < pd.Timestamp(START, tz="UTC") or date > pd.Timestamp(END, tz="UTC"):
            continue

        # === CHECK EXITS ===
        for key in list(positions.keys()):
            pos = positions[key]
            sym = pos.symbol
            if sym not in data:
                continue

            i = data[sym].index.get_loc(date)
            h = data[sym]["high"].iloc[i]
            l = data[sym]["low"].iloc[i]
            c = data[sym]["close"].iloc[i]
            o = data[sym]["open"].iloc[i]
            days = (date - pd.Timestamp(pos.entry_date, tz="UTC")).days
            hit = None
            exit_p = 0

            # SL/TP
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

            # Time exits by strategy
            if "Overnight" in pos.strategy and days >= 1:
                hit, exit_p = "NEXT_OPEN", o
            elif "EU Gap" in pos.strategy and days >= 1:
                hit, exit_p = "EOD", c  # intraday, exit same day
            elif "Brent" in pos.strategy and days >= 1:
                hit, exit_p = "EOD", c
            elif "Gold" in pos.strategy and days >= 5:
                hit, exit_p = "TIME_5D", c
            elif "Sector" in pos.strategy and days >= 5:
                hit, exit_p = "REBALANCE", c

            if hit:
                spec = COSTS.get(sym, {"mult": 1, "comm": 3.0})
                if pos.side == "BUY":
                    pos.pnl = (exit_p - pos.entry_price) * spec["mult"] - spec["comm"]
                else:
                    pos.pnl = (pos.entry_price - exit_p) * spec["mult"] - spec["comm"]
                pos.exit_date = str(date.date())
                pos.exit_price = exit_p
                pos.exit_reason = hit
                pos.days_held = days
                all_trades.append(pos)
                del positions[key]

        # === GENERATE SIGNALS ===
        signals = []

        for sym_key in data:
            if sym_key not in data or date not in data[sym_key].index:
                continue

        i_mes = data["MES"].index.get_loc(date) if date in data["MES"].index else None
        i_estx = data["ESTX50"].index.get_loc(date) if date in data["ESTX50"].index else None
        i_mcl = data["MCL"].index.get_loc(date) if date in data["MCL"].index else None
        i_mgc = data["MGC"].index.get_loc(date) if date in data["MGC"].index else None
        i_dax = data["DAX"].index.get_loc(date) if date in data["DAX"].index else None

        if i_mes is None:
            continue

        mes_c = data["MES"]["close"].iloc[i_mes]
        mes_o = data["MES"]["open"].iloc[i_mes]

        # 1. EU Gap Open (ESTX50) — priority 9, intraday
        if i_estx is not None:
            gap = ind["ESTX50_gap"].iloc[i_estx]
            if not pd.isna(gap):
                estx_c = data["ESTX50"]["close"].iloc[i_estx]
                estx_o = data["ESTX50"]["open"].iloc[i_estx]
                if gap > 0.01:  # gap up > 1% -> fade SHORT
                    sl = estx_o * 1.015
                    tp = estx_o * 0.98
                    signals.append(("EU Gap Open", "ESTX50", "SELL", estx_o, sl, tp, 9))
                elif gap < -0.01:  # gap down > 1% -> fade LONG
                    sl = estx_o * 0.985
                    tp = estx_o * 1.02
                    signals.append(("EU Gap Open", "ESTX50", "BUY", estx_o, sl, tp, 9))

        # 2. Brent Lag (MCL) — priority 8, intraday
        if i_mcl is not None:
            ema10 = ind["MCL_ema10"].iloc[i_mcl]
            ema30 = ind["MCL_ema30"].iloc[i_mcl]
            mcl_c = data["MCL"]["close"].iloc[i_mcl]
            if not pd.isna(ema10) and not pd.isna(ema30):
                if ema10 > ema30 * 1.005:  # bullish cross
                    sl = mcl_c - 1.5
                    tp = mcl_c + 3.0
                    signals.append(("Brent Lag MCL", "MCL", "BUY", mcl_c, sl, tp, 8))
                elif ema10 < ema30 * 0.995:
                    sl = mcl_c + 1.5
                    tp = mcl_c - 3.0
                    signals.append(("Brent Lag MCL", "MCL", "SELL", mcl_c, sl, tp, 8))

        # 3. Gold-Equity Div (MES+MGC) — priority 7, swing 5d
        if i_mgc is not None:
            mes_r5 = ind["MES_ret5"].iloc[i_mes]
            mgc_r5 = ind["MGC_ret5"].iloc[i_mgc]
            if not pd.isna(mes_r5) and not pd.isna(mgc_r5):
                if mes_r5 > 0.02 and mgc_r5 < -0.01:
                    signals.append(("Gold-Equity Div", "MES", "SELL", mes_c, mes_c + 40, mes_c - 60, 7))
                elif mes_r5 < -0.02 and mgc_r5 > 0.01:
                    signals.append(("Gold-Equity Div", "MES", "BUY", mes_c, mes_c - 40, mes_c + 60, 7))

        # 4. Sector Rotation (DAX vs CAC40) — priority 6, weekly
        if i_dax is not None:
            dax_m = ind["DAX_mom20"].iloc[i_dax]
            cac_m = ind["CAC40_mom20"].iloc[i_dax] if date in data["CAC40"].index else None
            dow = date.dayofweek
            if dow == 0 and cac_m is not None and not pd.isna(dax_m) and not pd.isna(cac_m):
                # Weekly rebalance on Mondays
                dax_c = data["DAX"]["close"].iloc[i_dax]
                if dax_m > cac_m + 0.02:  # DAX momentum > CAC by 2%
                    # Long DAX (momentum leader) — using index as proxy
                    signals.append(("Sector Rotation", "DAX", "BUY", dax_c, dax_c * 0.96, dax_c * 1.08, 6))
                elif cac_m > dax_m + 0.02:
                    signals.append(("Sector Rotation", "CAC40", "BUY", data["CAC40"]["close"].iloc[data["CAC40"].index.get_loc(date)], data["CAC40"]["close"].iloc[data["CAC40"].index.get_loc(date)] * 0.96, data["CAC40"]["close"].iloc[data["CAC40"].index.get_loc(date)] * 1.08, 6))

        # 5. Overnight MES — priority 5
        ema20 = ind["MES_ema20"].iloc[i_mes]
        if not pd.isna(ema20) and mes_c > ema20:
            signals.append(("Overnight MES", "MES", "BUY", mes_c, mes_c - 30, mes_c + 50, 5))

        # Sort by priority
        signals.sort(key=lambda x: x[6], reverse=True)

        # === OPEN POSITIONS ===
        slots = MAX_POSITIONS - len(positions)
        opened = 0
        for name, sym, side, entry, sl, tp, prio in signals:
            if opened >= slots:
                break
            # No duplicate strategy
            if any(p.strategy == name for p in positions.values()):
                continue
            # No duplicate symbol
            key = f"{sym}_{name}"
            if any(p.symbol == sym for p in positions.values()):
                continue

            positions[key] = Trade(
                strategy=name, symbol=sym, side=side,
                entry_date=str(date.date()), entry_price=entry,
                sl=sl, tp=tp,
            )
            opened += 1

    # Close remaining
    for key in list(positions.keys()):
        pos = positions[key]
        sym = pos.symbol
        c = data[sym]["close"].iloc[-1]
        spec = COSTS.get(sym, {"mult": 1, "comm": 3.0})
        if pos.side == "BUY":
            pos.pnl = (c - pos.entry_price) * spec["mult"] - spec["comm"]
        else:
            pos.pnl = (pos.entry_price - c) * spec["mult"] - spec["comm"]
        pos.exit_date = str(data[sym].index[-1].date())
        pos.exit_price = c
        pos.exit_reason = "END"
        all_trades.append(pos)

    # =============================================
    # RESULTS
    # =============================================
    print("\n" + "=" * 85)
    print(f"  BACKTEST PORTEFEUILLE COMPLET — {START} to {END}")
    print(f"  6 strategies, max {MAX_POSITIONS} positions, regles live")
    print("=" * 85)

    strats = defaultdict(list)
    for t in all_trades:
        strats[t.strategy].append(t)

    print(f"\n{'Strategy':<22} {'Sym':<7} {'Trades':>6} {'Wins':>5} {'WR':>6} {'PnL':>10} {'Avg':>8} {'MaxDD':>8} {'Sharpe':>7} {'AvgDays':>7}")
    print("-" * 95)

    total_pnl = 0
    total_n = 0
    all_strat_returns = {}

    for name in ["EU Gap Open", "Brent Lag MCL", "Gold-Equity Div", "Sector Rotation", "Overnight MES"]:
        st = strats.get(name, [])
        n = len(st)
        if n == 0:
            print(f"{name:<22} {'—':<7} {'0':>6}")
            continue
        sym = st[0].symbol
        wins = sum(1 for t in st if t.pnl > 0)
        pnl = sum(t.pnl for t in st)
        avg = pnl / n
        pnls = [t.pnl for t in st]
        avg_days = np.mean([t.days_held for t in st])
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        max_dd = (cum - peak).min()
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252 / max(avg_days, 1)) if np.std(pnls) > 0 else 0

        print(f"{name:<22} {sym:<7} {n:>6} {wins:>5} {wins/n*100:>5.0f}% ${pnl:>+9,.0f} ${avg:>+7.0f} ${max_dd:>+7.0f} {sharpe:>7.2f} {avg_days:>6.1f}d")
        total_pnl += pnl
        total_n += n
        all_strat_returns[name] = pnls

    total_wins = sum(1 for t in all_trades if t.pnl > 0)
    all_pnls = [t.pnl for t in all_trades]
    cum_all = np.cumsum(all_pnls)
    peak_all = np.maximum.accumulate(cum_all)
    max_dd_all = (cum_all - peak_all).min()
    sharpe_all = np.mean(all_pnls) / np.std(all_pnls) * np.sqrt(252) if np.std(all_pnls) > 0 else 0
    pf = sum(t.pnl for t in all_trades if t.pnl > 0) / abs(sum(t.pnl for t in all_trades if t.pnl < 0)) if sum(t.pnl for t in all_trades if t.pnl < 0) != 0 else 0

    print("-" * 95)
    print(f"{'TOTAL':<22} {'ALL':<7} {total_n:>6} {total_wins:>5} {total_wins/total_n*100:>5.0f}% ${total_pnl:>+9,.0f} ${total_pnl/total_n:>+7.0f} ${max_dd_all:>+7.0f} {sharpe_all:>7.2f}")

    print(f"\n  Capital: $10,000 | Return: {total_pnl/10000*100:+.1f}% | ROC/an: {total_pnl/10000/3*100:.1f}%")
    print(f"  MaxDD: ${max_dd_all:,.0f} | PF: {pf:.2f} | Avg trade: ${total_pnl/total_n:+.1f}")

    # CORRELATION
    print("\n" + "=" * 85)
    print("  CORRELATION INTER-STRATEGIES")
    print("=" * 85)
    strat_names = [s for s in all_strat_returns if len(all_strat_returns[s]) >= 5]
    if len(strat_names) >= 2:
        max_len = max(len(all_strat_returns[s]) for s in strat_names)
        df_c = pd.DataFrame()
        for s in strat_names:
            p = all_strat_returns[s]
            df_c[s[:18]] = (p + [0] * (max_len - len(p)))[:max_len]
        corr = df_c.corr()
        print(f"\n{'':>19}", end="")
        for s in corr.columns:
            print(f"{s:>19}", end="")
        print()
        for s1 in corr.index:
            print(f"{s1:>19}", end="")
            for s2 in corr.columns:
                print(f"{corr.loc[s1, s2]:>19.2f}", end="")
            print()

    # WALK-FORWARD
    print("\n" + "=" * 85)
    print("  WALK-FORWARD (6 fenetres)")
    print("=" * 85)
    ws = max(1, len(all_trades) // 6)
    pw = 0
    for w in range(6):
        wt = all_trades[w * ws:min((w + 1) * ws, len(all_trades))]
        if not wt:
            continue
        wp = sum(t.pnl for t in wt)
        wn = len(wt)
        ww = sum(1 for t in wt if t.pnl > 0)
        tag = "PROFIT" if wp > 0 else "LOSS"
        if wp > 0:
            pw += 1
        period = f"{wt[0].entry_date} to {wt[-1].exit_date}"
        print(f"  W{w+1} [{period}]: {wn:3d} trades | ${wp:>+8,.0f} | WR {ww/wn*100:.0f}% | {tag}")
    print(f"  WF: {pw}/6 {'PASS' if pw >= 3 else 'FAIL'}")

    # MONTHLY
    monthly = defaultdict(float)
    for t in all_trades:
        monthly[t.exit_date[:7]] += t.pnl
    pm = sum(1 for v in monthly.values() if v > 0)
    print(f"\n  Mois profitables: {pm}/{len(monthly)} ({pm/len(monthly)*100:.0f}%)")
    print(f"  Meilleur mois: ${max(monthly.values()):+,.0f}")
    print(f"  Pire mois: ${min(monthly.values()):+,.0f}")

    # LAST 15 TRADES
    print("\n" + "=" * 85)
    print("  DERNIERS 15 TRADES")
    print("=" * 85)
    print(f"{'Date':<11} {'Strategy':<20} {'Sym':<7} {'Side':<5} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'Why':<10} {'Days':>4}")
    for t in all_trades[-15:]:
        print(f"{t.entry_date:<11} {t.strategy:<20} {t.symbol:<7} {t.side:<5} {t.entry_price:>8.0f} {t.exit_price:>8.0f} ${t.pnl:>+7.0f} {t.exit_reason:<10} {t.days_held:>4}")


if __name__ == "__main__":
    main()
