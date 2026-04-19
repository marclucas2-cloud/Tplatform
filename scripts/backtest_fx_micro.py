"""
Backtest FX micro futures — adapter les strats FX validees au format CME.
M6E (EUR/USD): $12,500 * price, tick 0.0001 = $1.25. Margin ~$250
M6B (GBP/USD): $6,250 * price, tick 0.0001 = $0.625. Margin ~$300
M6J (JPY/USD): $12,500,000 / USDJPY, tick 0.000001 = $1.25. Margin ~$250

Strats testees (adaptees du spot FX):
1. EUR/USD Trend (EMA50/200 cross)
2. EUR/GBP Mean Reversion (via M6E/M6B ratio)
3. Multi-FX Momentum (top 2 momentum, bottom 1 short)

3 ans, backtest portefeuille combine avec les 4 strats existantes.
"""
import warnings
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent

# M6E: 1 tick = 0.0001 = $1.25. 1 contract = $12,500 * price
# Simplified: PnL = (exit - entry) * 12500 per contract
FX_SPECS = {
    "M6E": {"mult": 12500, "comm": 0.62, "margin": 250, "name": "EUR/USD"},
    "M6B": {"mult": 6250, "comm": 0.62, "margin": 300, "name": "GBP/USD"},
    "M6J": {"mult": 12500000, "comm": 0.62, "margin": 250, "name": "USD/JPY"},
}

MES_SPECS = {"MES": {"mult": 5, "comm": 1.24}, "ESTX50": {"mult": 10, "comm": 3.0},
             "DAX": {"mult": 1, "comm": 6.0}, "CAC40": {"mult": 1, "comm": 6.0},
             "MGC": {"mult": 10, "comm": 1.24}}

START = "2023-04-01"
END = "2026-04-09"
MAX_POS = 4  # existing 3 + 1 FX


def load_fx(ticker, sym):
    import yfinance as yf
    df = yf.download(ticker, start="2020-01-01", end="2026-04-10", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


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
    print("Loading data...")
    m6e = load_fx("6E=F", "M6E")
    m6b = load_fx("6B=F", "M6B")
    mes = load("MES")
    mgc = load("MGC")
    estx = load("ESTX50")
    dax = load("DAX")
    cac = load("CAC40")
    vix = load("VIX")

    print(f"M6E: {len(m6e)} bars, M6B: {len(m6b)} bars")

    # Align all
    common = mes.index
    for df in [m6e, m6b, mgc, estx, dax, cac, vix]:
        common = common.intersection(df.index)

    # FX indicators
    m6e_c = m6e["close"]
    m6b_c = m6b["close"]
    m6e_ema50 = m6e_c.ewm(50).mean()
    m6e_ema200 = m6e_c.ewm(200).mean()
    m6b_ema50 = m6b_c.ewm(50).mean()
    m6b_ema200 = m6b_c.ewm(200).mean()
    m6e_atr = m6e_c.diff().abs().rolling(20).mean()
    m6b_atr = m6b_c.diff().abs().rolling(20).mean()

    # Existing strat indicators
    mes_c = mes["close"]
    mes_ema20 = mes_c.ewm(20).mean()
    mes_ret5 = mes_c.pct_change(5)
    mgc_ret5 = mgc["close"].pct_change(5).reindex(mes.index, method="ffill")
    estx_gap = (estx["open"] - estx["close"].shift(1)) / estx["close"].shift(1)
    dax_mom = dax["close"].pct_change(20)
    cac_mom = cac["close"].pct_change(20)

    positions = {}
    all_trades = []

    for date in common:
        if date < pd.Timestamp(START, tz="UTC") or date > pd.Timestamp(END, tz="UTC"):
            continue

        # EXITS
        for key in list(positions.keys()):
            pos = positions[key]
            sym = pos.symbol
            days = (date - pd.Timestamp(pos.entry_date, tz="UTC")).days
            hit = exit_p = None

            if sym in ["M6E", "M6B"]:
                df_ref = m6e if sym == "M6E" else m6b
            elif sym == "MES":
                df_ref = mes
            elif sym == "ESTX50":
                df_ref = estx
            elif sym in ["DAX", "CAC40"]:
                df_ref = dax if sym == "DAX" else cac
            else:
                continue

            if date not in df_ref.index:
                continue
            i = df_ref.index.get_loc(date)
            h, l, c, o = df_ref["high"].iloc[i], df_ref["low"].iloc[i], df_ref["close"].iloc[i], df_ref["open"].iloc[i]

            if pos.side == "BUY":
                if l <= pos.sl: hit, exit_p = "SL", pos.sl
                elif h >= pos.tp: hit, exit_p = "TP", pos.tp
            else:
                if h >= pos.sl: hit, exit_p = "SL", pos.sl
                elif l <= pos.tp: hit, exit_p = "TP", pos.tp

            # Time exits
            if "FX Trend" in pos.strategy and days >= 21: hit, exit_p = "REBALANCE", c
            elif "FX MR" in pos.strategy and days >= 10: hit, exit_p = "TIME", c
            elif "Overnight" in pos.strategy and days >= 1: hit, exit_p = "NEXT_OPEN", o
            elif "EU Gap" in pos.strategy and days >= 1: hit, exit_p = "EOD", c
            elif "Gold" in pos.strategy and days >= 5: hit, exit_p = "TIME", c
            elif "Sector" in pos.strategy and days >= 5: hit, exit_p = "REBALANCE", c

            if hit:
                spec = FX_SPECS.get(sym, MES_SPECS.get(sym, {"mult": 1, "comm": 3.0}))
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

        # SIGNALS
        signals = []

        # === FX STRATEGIES ===
        if date in m6e.index:
            i_e = m6e.index.get_loc(date)
            e50 = m6e_ema50.iloc[i_e]
            e200 = m6e_ema200.iloc[i_e]
            atr = m6e_atr.iloc[i_e]
            price = m6e_c.iloc[i_e]
            if not pd.isna(e50) and not pd.isna(e200) and not pd.isna(atr) and atr > 0:
                if e50 > e200:
                    signals.append(("FX Trend M6E", "M6E", "BUY", price, price - 2.5 * atr, price + 4 * atr, 8))
                elif e50 < e200:
                    signals.append(("FX Trend M6E", "M6E", "SELL", price, price + 2.5 * atr, price - 4 * atr, 8))

        if date in m6b.index:
            i_b = m6b.index.get_loc(date)
            e50b = m6b_ema50.iloc[i_b]
            e200b = m6b_ema200.iloc[i_b]
            atrb = m6b_atr.iloc[i_b]
            priceb = m6b_c.iloc[i_b]
            if not pd.isna(e50b) and not pd.isna(e200b) and not pd.isna(atrb) and atrb > 0:
                if e50b > e200b:
                    signals.append(("FX Trend M6B", "M6B", "BUY", priceb, priceb - 2.5 * atrb, priceb + 4 * atrb, 7))
                elif e50b < e200b:
                    signals.append(("FX Trend M6B", "M6B", "SELL", priceb, priceb + 2.5 * atrb, priceb - 4 * atrb, 7))

        # === EXISTING STRATEGIES ===
        if date in mes.index:
            i_m = mes.index.get_loc(date)
            mc = mes_c.iloc[i_m]

            # EU Gap (prio 9)
            if date in estx.index:
                gap = estx_gap.iloc[estx.index.get_loc(date)]
                if not pd.isna(gap) and abs(gap) > 0.01 and abs(gap) < 0.05:
                    eo = estx["open"].iloc[estx.index.get_loc(date)]
                    if gap > 0.01:
                        signals.append(("EU Gap Open", "ESTX50", "SELL", eo, eo * 1.015, eo * 0.98, 9))
                    else:
                        signals.append(("EU Gap Open", "ESTX50", "BUY", eo, eo * 0.985, eo * 1.02, 9))

            # Gold-Equity (prio 7)
            mr5 = mes_ret5.iloc[i_m]
            gr5 = mgc_ret5.iloc[i_m] if date in mgc_ret5.index else None
            if gr5 is not None and not pd.isna(mr5) and not pd.isna(gr5):
                if mr5 > 0.02 and gr5 < -0.01:
                    signals.append(("Gold-Equity Div", "MES", "SELL", mc, mc + 40, mc - 60, 7))
                elif mr5 < -0.02 and gr5 > 0.01:
                    signals.append(("Gold-Equity Div", "MES", "BUY", mc, mc - 40, mc + 60, 7))

            # Sector Rotation (prio 6, Monday)
            if date.dayofweek == 0 and date in dax.index:
                dm = dax_mom.iloc[dax.index.get_loc(date)]
                cm = cac_mom.iloc[cac.index.get_loc(date)] if date in cac.index else None
                if cm is not None and not pd.isna(dm) and not pd.isna(cm):
                    if dm > cm + 0.02:
                        dc = dax["close"].iloc[dax.index.get_loc(date)]
                        signals.append(("Sector Rotation", "DAX", "BUY", dc, dc * 0.96, dc * 1.08, 6))
                    elif cm > dm + 0.02:
                        cc = cac["close"].iloc[cac.index.get_loc(date)]
                        signals.append(("Sector Rotation", "CAC40", "BUY", cc, cc * 0.96, cc * 1.08, 6))

            # Overnight (prio 5)
            e20 = mes_ema20.iloc[i_m]
            if not pd.isna(e20) and mc > e20:
                signals.append(("Overnight MES", "MES", "BUY", mc, mc - 30, mc + 50, 5))

        signals.sort(key=lambda x: x[6], reverse=True)

        # OPEN
        slots = MAX_POS - len(positions)
        for name, sym, side, entry, sl, tp, prio in signals:
            if slots <= 0: break
            if any(p.strategy == name for p in positions.values()): continue
            if any(p.symbol == sym for p in positions.values()): continue
            positions[f"{sym}_{name}"] = Trade(strategy=name, symbol=sym, side=side,
                entry_date=str(date.date()), entry_price=entry, sl=sl, tp=tp)
            slots -= 1

    # Close remaining
    for key in list(positions.keys()):
        pos = positions[key]
        sym = pos.symbol
        ref = {"M6E": m6e, "M6B": m6b, "MES": mes, "ESTX50": estx, "DAX": dax, "CAC40": cac}.get(sym, mes)
        c = ref["close"].iloc[-1]
        spec = FX_SPECS.get(sym, MES_SPECS.get(sym, {"mult": 1, "comm": 3.0}))
        pos.pnl = ((c - pos.entry_price) if pos.side == "BUY" else (pos.entry_price - c)) * spec["mult"] - spec["comm"]
        pos.exit_date = str(ref.index[-1].date())
        pos.exit_price = c
        pos.exit_reason = "END"
        all_trades.append(pos)

    # RESULTS
    print("\n" + "=" * 90)
    print(f"  BACKTEST PORTEFEUILLE + FX MICRO — {START} to {END}")
    print(f"  6 strategies, max {MAX_POS} positions")
    print("=" * 90)

    strats = defaultdict(list)
    for t in all_trades: strats[t.strategy].append(t)

    print(f"\n{'Strategy':<22} {'Sym':<7} {'Trades':>6} {'Wins':>5} {'WR':>6} {'PnL':>10} {'Avg':>8} {'Sharpe':>7} {'Days':>5}")
    print("-" * 80)

    total_pnl = total_n = 0
    for name in ["EU Gap Open", "FX Trend M6E", "FX Trend M6B", "Gold-Equity Div", "Sector Rotation", "Overnight MES"]:
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
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252 / max(avg_d, 1)) if np.std(pnls) > 0 else 0
        print(f"{name:<22} {sym:<7} {n:>6} {wins:>5} {wins/n*100:>5.0f}% ${pnl:>+9,.0f} ${pnl/n:>+7.0f} {sharpe:>7.2f} {avg_d:>4.1f}d")
        total_pnl += pnl
        total_n += n

    tw = sum(1 for t in all_trades if t.pnl > 0)
    ap = [t.pnl for t in all_trades]
    sh = np.mean(ap) / np.std(ap) * np.sqrt(252) if np.std(ap) > 0 else 0
    cum = np.cumsum(ap)
    mdd = (cum - np.maximum.accumulate(cum)).min()
    pf = sum(t.pnl for t in all_trades if t.pnl > 0) / abs(sum(t.pnl for t in all_trades if t.pnl < 0)) if sum(t.pnl for t in all_trades if t.pnl < 0) != 0 else 0

    print("-" * 80)
    print(f"{'TOTAL':<22} {'ALL':<7} {total_n:>6} {tw:>5} {tw/total_n*100:>5.0f}% ${total_pnl:>+9,.0f} ${total_pnl/total_n:>+7.0f} {sh:>7.2f}")
    print(f"\n  Return: {total_pnl/10000*100:+.1f}% | ROC/an: {total_pnl/10000/3*100:.1f}%/an | MaxDD: ${mdd:,.0f} | PF: {pf:.2f}")

    # WF
    ws = max(1, len(all_trades) // 6)
    pw = 0
    print("\n  Walk-Forward:")
    for w in range(6):
        wt = all_trades[w * ws:min((w + 1) * ws, len(all_trades))]
        if not wt: continue
        wp = sum(t.pnl for t in wt)
        if wp > 0: pw += 1
        print(f"    W{w+1}: {len(wt)} trades ${wp:>+8,.0f} {'PROFIT' if wp > 0 else 'LOSS'}")
    print(f"    WF: {pw}/6 {'PASS' if pw >= 3 else 'FAIL'}")

    # Correlation
    print("\n  Correlations FX vs existing:")
    sr = {}
    for name in strats:
        if len(strats[name]) >= 5:
            sr[name[:15]] = [t.pnl for t in strats[name]]
    if len(sr) >= 2:
        ml = max(len(v) for v in sr.values())
        dfc = pd.DataFrame({k: (v + [0]*(ml-len(v)))[:ml] for k, v in sr.items()})
        corr = dfc.corr()
        for i in range(len(corr)):
            for j in range(i+1, len(corr)):
                print(f"    {corr.index[i]}/{corr.columns[j]}: {corr.iloc[i,j]:.2f}")


if __name__ == "__main__":
    main()
