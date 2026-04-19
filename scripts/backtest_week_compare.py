"""
Backtest precis semaine 4-9 avril 2026.
Compare le PnL theorique (backtest) avec le PnL reel IBKR.
"""
import yfinance as yf
import pandas as pd
import numpy as np

# Download futures data
print("Downloading MES/MNQ futures data...")
mes_raw = yf.download('ES=F', start='2025-01-01', end='2026-04-10', interval='1d', progress=False)
mnq_raw = yf.download('NQ=F', start='2025-01-01', end='2026-04-10', interval='1d', progress=False)

for df in [mes_raw, mnq_raw]:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]

mes = mes_raw.copy()
mnq = mnq_raw.copy()

MES_MULT = 5   # $5/point
MNQ_MULT = 2   # $2/point
COMM_RT = 1.24  # round-trip per contract

week_start = '2026-04-04'
week_end = '2026-04-09'

print(f"MES: {len(mes)} bars, last={mes.index[-1].date()}")
print(f"MNQ: {len(mnq)} bars, last={mnq.index[-1].date()}")

# Precompute indicators
ema50 = mes['close'].ewm(span=50).mean()
ema200 = mes['close'].ewm(span=200).mean()
ema20 = mes['close'].ewm(span=20).mean()
high100 = mes['close'].rolling(100).max()
low100 = mes['close'].rolling(100).min()
atr20 = mes['close'].diff().abs().rolling(20).mean()

ema20_mnq = mnq['close'].ewm(span=20).mean()

print()
print("=" * 70)
print("  OHLC SEMAINE 4-9 AVRIL 2026")
print("=" * 70)

for date in mes.loc['2026-04-01':'2026-04-09'].index:
    i = mes.index.get_loc(date)
    d = date.strftime('%a %Y-%m-%d')
    o = mes['open'].iloc[i]
    h = mes['high'].iloc[i]
    l = mes['low'].iloc[i]
    c = mes['close'].iloc[i]
    chg = c - mes['close'].iloc[i-1] if i > 0 else 0
    candle = "UP" if c >= o else "DOWN"

    mnq_c = ""
    if date in mnq.index:
        j = mnq.index.get_loc(date)
        mnq_c = f"MNQ C={mnq['close'].iloc[j]:.0f}"

    print(f"  {d}: O={o:.0f} H={h:.0f} L={l:.0f} C={c:.0f} ({candle}, {chg:+.0f}pts) | {mnq_c}")

print()
print("=" * 70)
print("  SIGNAUX PAR STRATEGIE - CHAQUE JOUR")
print("=" * 70)

all_signals = {}  # date -> list of (strat_name, side, symbol, entry_price, sl, tp, strength)

for date in mes.loc[week_start:week_end].index:
    i = mes.index.get_loc(date)
    if i < 200:
        continue

    d = date.strftime('%Y-%m-%d')
    c_mes = mes['close'].iloc[i]
    o_mes = mes['open'].iloc[i]
    a = atr20.iloc[i]
    signals = []

    # 1. MES Trend (EMA50/200 + Breakout100)
    e50 = ema50.iloc[i]
    e200 = ema200.iloc[i]
    h100 = high100.iloc[i]
    l100 = low100.iloc[i]
    if e50 < e200 and c_mes <= l100:
        sl = c_mes + 20
        tp = c_mes - 40
        signals.append(('MES Trend', 'SELL', 'MES', c_mes, sl, tp, 0.54))
    elif e50 > e200 and c_mes >= h100:
        sl = c_mes - 20
        tp = c_mes + 40
        signals.append(('MES Trend', 'BUY', 'MES', c_mes, sl, tp, 0.54))

    # 2. MES 3-Day Stretch
    prev_candles = []
    for j in range(i-3, i):
        prev_candles.append("DOWN" if mes['close'].iloc[j] < mes['open'].iloc[j] else "UP")
    all_down = all(c == "DOWN" for c in prev_candles)
    all_up = all(c == "UP" for c in prev_candles)
    if all_down:
        signals.append(('3-Day Stretch', 'BUY', 'MES', c_mes, c_mes - 20, c_mes + 30, 1.0))
    elif all_up:
        signals.append(('3-Day Stretch', 'SELL', 'MES', c_mes, c_mes + 20, c_mes - 30, 1.0))

    # 3. Overnight MES
    e20 = ema20.iloc[i]
    if c_mes > e20:
        signals.append(('Overnight MES', 'BUY', 'MES', c_mes, c_mes - 30, c_mes + 20, 0.8))

    # 4. Overnight MNQ
    if date in mnq.index:
        j = mnq.index.get_loc(date)
        c_mnq = mnq['close'].iloc[j]
        e20_mnq_val = ema20_mnq.iloc[j]
        if c_mnq > e20_mnq_val:
            signals.append(('Overnight MNQ', 'BUY', 'MNQ', c_mnq, c_mnq - 100, c_mnq + 70, 0.8))

    # 5. TSMOM MES
    if i >= 63:
        ret63 = (c_mes / mes['close'].iloc[i-63]) - 1
        side = 'BUY' if ret63 > 0 else 'SELL'
        if side == 'SELL':
            signals.append(('TSMOM MES', 'SELL', 'MES', c_mes, c_mes + 25, c_mes - 40, abs(ret63)*10))
        else:
            signals.append(('TSMOM MES', 'BUY', 'MES', c_mes, c_mes - 25, c_mes + 40, abs(ret63)*10))

    # 6. TSMOM MNQ
    if date in mnq.index:
        j = mnq.index.get_loc(date)
        c_mnq = mnq['close'].iloc[j]
        if j >= 63:
            ret63_mnq = (c_mnq / mnq['close'].iloc[j-63]) - 1
            side = 'BUY' if ret63_mnq > 0 else 'SELL'
            if side == 'SELL':
                signals.append(('TSMOM MNQ', 'SELL', 'MNQ', c_mnq, c_mnq + 100, c_mnq - 140, abs(ret63_mnq)*10))
            else:
                signals.append(('TSMOM MNQ', 'BUY', 'MNQ', c_mnq, c_mnq - 100, c_mnq + 140, abs(ret63_mnq)*10))

    all_signals[d] = signals

    print(f"\n  {d} (MES close={c_mes:.0f}, EMA50={e50:.0f}, EMA200={e200:.0f}):")
    if not signals:
        print(f"    -> Aucun signal")
    for s in signals:
        name, side, sym, entry, sl, tp, strength = s
        print(f"    {name}: {side} {sym} @ {entry:.0f} SL={sl:.0f} TP={tp:.0f} str={strength:.2f}")


# SIMULATE EXECUTION
# Rules from the live system:
# - Max 2 contracts total (hard limit)
# - 1 contract per symbol max
# - Execute strongest signal first
# - SL/TP via OCA, checked intraday

print()
print("=" * 70)
print("  SIMULATION EXECUTION (max 2 contrats, 1 par symbole)")
print("=" * 70)

positions = {}  # sym -> {side, entry, sl, tp, date, strat}
trade_results = []
capital = 10040.0

dates = sorted(all_signals.keys())

for d in dates:
    signals = all_signals[d]
    date = pd.Timestamp(d)
    i_mes = mes.index.get_loc(date) if date in mes.index else None
    i_mnq = mnq.index.get_loc(date) if date in mnq.index else None

    # Check SL/TP on existing positions using today's high/low
    for sym in list(positions.keys()):
        pos = positions[sym]
        if sym == 'MES' and i_mes is not None:
            h = mes['high'].iloc[i_mes]
            l = mes['low'].iloc[i_mes]
        elif sym == 'MNQ' and i_mnq is not None:
            h = mnq['high'].iloc[i_mnq]
            l = mnq['low'].iloc[i_mnq]
        else:
            continue

        mult = MES_MULT if sym == 'MES' else MNQ_MULT
        hit = None

        if pos['side'] == 'BUY':
            if l <= pos['sl']:
                hit = 'SL'
                exit_p = pos['sl']
            elif h >= pos['tp']:
                hit = 'TP'
                exit_p = pos['tp']
        else:  # SELL
            if h >= pos['sl']:
                hit = 'SL'
                exit_p = pos['sl']
            elif l <= pos['tp']:
                hit = 'TP'
                exit_p = pos['tp']

        if hit:
            if pos['side'] == 'BUY':
                pnl = (exit_p - pos['entry']) * mult - COMM_RT
            else:
                pnl = (pos['entry'] - exit_p) * mult - COMM_RT

            trade_results.append({
                'date_entry': pos['date'],
                'date_exit': d,
                'strat': pos['strat'],
                'sym': sym,
                'side': pos['side'],
                'entry': pos['entry'],
                'exit': exit_p,
                'exit_type': hit,
                'pnl': round(pnl, 2),
            })
            capital += pnl
            print(f"  {d}: {hit} {sym} {pos['side']} entry={pos['entry']:.0f} exit={exit_p:.0f} -> PnL ${pnl:+.2f} ({pos['strat']})")
            del positions[sym]

    # Close overnight positions at open (Overnight strategy = exit at next open)
    for sym in list(positions.keys()):
        pos = positions[sym]
        if 'Overnight' in pos['strat'] and pos['date'] != d:
            if sym == 'MES' and i_mes is not None:
                exit_p = mes['open'].iloc[i_mes]
            elif sym == 'MNQ' and i_mnq is not None:
                exit_p = mnq['open'].iloc[i_mnq]
            else:
                continue

            mult = MES_MULT if sym == 'MES' else MNQ_MULT
            if pos['side'] == 'BUY':
                pnl = (exit_p - pos['entry']) * mult - COMM_RT
            else:
                pnl = (pos['entry'] - exit_p) * mult - COMM_RT

            trade_results.append({
                'date_entry': pos['date'],
                'date_exit': d,
                'strat': pos['strat'],
                'sym': sym,
                'side': pos['side'],
                'entry': pos['entry'],
                'exit': exit_p,
                'exit_type': 'OVERNIGHT_EXIT',
                'pnl': round(pnl, 2),
            })
            capital += pnl
            print(f"  {d}: EXIT_OPEN {sym} {pos['side']} entry={pos['entry']:.0f} exit={exit_p:.0f} -> PnL ${pnl:+.2f} ({pos['strat']})")
            del positions[sym]

    # Open new positions (strongest first, max 2 total, 1 per symbol)
    if len(positions) < 2 and signals:
        sorted_sigs = sorted(signals, key=lambda x: x[6], reverse=True)
        for name, side, sym, entry, sl, tp, strength in sorted_sigs:
            if len(positions) >= 2:
                break
            if sym in positions:
                continue
            positions[sym] = {
                'side': side,
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'date': d,
                'strat': name,
            }
            print(f"  {d}: OPEN {side} {sym} @ {entry:.0f} SL={sl:.0f} TP={tp:.0f} ({name})")

# Close remaining at last close
for sym in list(positions.keys()):
    pos = positions[sym]
    if sym == 'MES':
        exit_p = mes['close'].iloc[-1]
    else:
        exit_p = mnq['close'].iloc[-1]
    mult = MES_MULT if sym == 'MES' else MNQ_MULT
    if pos['side'] == 'BUY':
        pnl = (exit_p - pos['entry']) * mult - COMM_RT
    else:
        pnl = (pos['entry'] - exit_p) * mult - COMM_RT
    trade_results.append({
        'date_entry': pos['date'],
        'date_exit': 'OPEN',
        'strat': pos['strat'],
        'sym': sym,
        'side': pos['side'],
        'entry': pos['entry'],
        'exit': exit_p,
        'exit_type': 'STILL_OPEN',
        'pnl': round(pnl, 2),
    })
    capital += pnl
    print(f"  STILL OPEN: {sym} {pos['side']} entry={pos['entry']:.0f} mark={exit_p:.0f} -> PnL ${pnl:+.2f}")

print()
print("=" * 70)
print("  RESULTATS BACKTEST vs REEL")
print("=" * 70)

total_bt_pnl = sum(t['pnl'] for t in trade_results)
n_trades = len(trade_results)
wins = sum(1 for t in trade_results if t['pnl'] > 0)

print(f"\n  BACKTEST (theorique):")
print(f"    Capital depart : EUR 10,040")
print(f"    Trades         : {n_trades}")
print(f"    Win rate       : {wins}/{n_trades} ({100*wins/n_trades:.0f}%)" if n_trades > 0 else "    Win rate       : N/A")
print(f"    PnL total      : ${total_bt_pnl:+,.2f}")
print(f"    Capital final   : EUR {capital:,.2f}")

print(f"\n  REEL (IBKR):")
print(f"    Capital depart : EUR 10,040 (fin avril 8)")
print(f"    Capital actuel : EUR 9,878")
print(f"    PnL reel       : ${9878 - 10040:+,.2f}")
print(f"    Trades reels   : MES SHORT @ 6815 -> SL @ 6853 = -$191")
print(f"                     MNQ BUY @ 25018 -> SELL @ 25019 = +$1")

print(f"\n  ECART:")
print(f"    Backtest : ${total_bt_pnl:+,.2f}")
print(f"    Reel     : ${-162:+,.2f}")
print(f"    Delta    : ${total_bt_pnl - (-162):+,.2f}")

print(f"\n  DETAIL DES TRADES BACKTEST:")
for t in trade_results:
    print(f"    {t['date_entry']} -> {t['date_exit']} | {t['strat']:<20} {t['side']:>4} {t['sym']} @ {t['entry']:.0f} -> {t['exit']:.0f} [{t['exit_type']}] PnL ${t['pnl']:+,.2f}")
