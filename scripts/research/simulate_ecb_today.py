"""Simulate ECB strategy on today's data — answer: would we have traded?

Pour chaque indice EU (DAX, CAC40, ESTX50):
  1. Fetch 5min bars from yfinance (^GDAXI, ^FCHI, ^STOXX50E)
  2. Replay logic MacroECB sur 14:15-14:45 Paris
  3. Si signal : compute entry/SL/TP, check si SL/TP touche depuis
  4. Output: signal? side? entry? still open?
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

PARIS_TZ = ZoneInfo("Europe/Paris")
TODAY = date(2026, 4, 16)

# Maps : strat symbol -> yfinance ticker
TICKERS = {
    "DAX":    "^GDAXI",
    "CAC40":  "^FCHI",
    "ESTX50": "^STOXX50E",
}

# Strategy parameters (from strategies_v2/futures/macro_ecb.py)
MOMENTUM_THRESHOLD = 0.0015  # 0.15%
OBS_MINUTES = 30
SL_PCT_OF_MOVE = 0.5
TP_MULT_OF_MOVE = 2.0
MAX_HOLD_MINUTES = 180


def fetch_5min(ticker: str) -> pd.DataFrame:
    # yfinance 5min data, last 5 days (covers today + buffer)
    df = yf.download(
        ticker, interval="5m",
        start=(TODAY - timedelta(days=2)).isoformat(),
        end=(TODAY + timedelta(days=1)).isoformat(),
        progress=False, auto_adjust=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = pd.to_datetime(df.index).tz_convert(PARIS_TZ) if df.index.tz is not None else pd.to_datetime(df.index).tz_localize("UTC").tz_convert(PARIS_TZ)
    return df


def simulate_for_symbol(symbol: str, ticker: str):
    print(f"\n=== {symbol} ({ticker}) ===")
    df = fetch_5min(ticker)
    today_bars = df[df.index.date == TODAY]
    if today_bars.empty:
        print(f"  No data for {TODAY}")
        return None

    # Find bar at 14:15 Paris (decision time)
    target_t0 = time(14, 15)
    t0_mask = today_bars.index.time >= target_t0
    if not t0_mask.any():
        print(f"  No bar >= 14:15 Paris")
        return None
    t0_bars = today_bars[t0_mask]
    t0_open = float(t0_bars["Open"].iloc[0])
    t0_ts = t0_bars.index[0]
    print(f"  T0 bar (14:15+): {t0_ts.strftime('%H:%M')} open={t0_open:.2f}")

    # Find bar 30min later (close at 14:45 Paris). 6 bars of 5min after T0.
    obs_bars = OBS_MINUTES // 5  # 6
    if len(t0_bars) < obs_bars:
        print(f"  Insufficient bars after T0 ({len(t0_bars)} < {obs_bars})")
        return None
    obs_close_bar = t0_bars.iloc[obs_bars - 1]
    obs_close = float(obs_close_bar["Close"])
    obs_ts = t0_bars.index[obs_bars - 1]
    print(f"  T0+30 close: {obs_ts.strftime('%H:%M')} close={obs_close:.2f}")

    # Compute move
    move = (obs_close - t0_open) / t0_open
    print(f"  Move 30min: {move*100:+.3f}% (threshold ±{MOMENTUM_THRESHOLD*100}%)")

    if abs(move) < MOMENTUM_THRESHOLD:
        print(f"  -> NO SIGNAL (below threshold)")
        return None

    # Build signal
    # Entry = bar close at evaluation time (here ~14:45 = obs_close)
    entry = obs_close
    move_abs = abs(move) * entry
    if move > 0:
        side = "BUY"
        sl = entry - SL_PCT_OF_MOVE * move_abs
        tp = entry + TP_MULT_OF_MOVE * move_abs
    else:
        side = "SELL"
        sl = entry + SL_PCT_OF_MOVE * move_abs
        tp = entry - TP_MULT_OF_MOVE * move_abs

    print(f"  -> SIGNAL {side}")
    print(f"     Entry: {entry:.2f} @ {obs_ts.strftime('%H:%M')}")
    print(f"     SL: {sl:.2f} ({(sl-entry)/entry*100:+.3f}%)")
    print(f"     TP: {tp:.2f} ({(tp-entry)/entry*100:+.3f}%)")

    # Now check what happened AFTER entry: did SL or TP get hit? Or still open?
    # Look at bars from obs_ts+1 to end of session (or +180min max)
    after = today_bars[today_bars.index > obs_ts]
    deadline = obs_ts + timedelta(minutes=MAX_HOLD_MINUTES)
    after = after[after.index <= deadline]

    if after.empty:
        print(f"     No bars after entry yet, position still open")
        return {"side": side, "entry": entry, "sl": sl, "tp": tp,
                "exit": None, "exit_reason": "still_open"}

    exit_price = None
    exit_reason = None
    exit_ts = None
    for ts, bar in after.iterrows():
        hi = float(bar["High"])
        lo = float(bar["Low"])
        if side == "BUY":
            if hi >= tp:
                exit_price = tp
                exit_reason = "TP_hit"
                exit_ts = ts
                break
            if lo <= sl:
                exit_price = sl
                exit_reason = "SL_hit"
                exit_ts = ts
                break
        else:  # SELL
            if lo <= tp:
                exit_price = tp
                exit_reason = "TP_hit"
                exit_ts = ts
                break
            if hi >= sl:
                exit_price = sl
                exit_reason = "SL_hit"
                exit_ts = ts
                break

    last_bar = after.iloc[-1]
    last_price = float(last_bar["Close"])
    last_ts = after.index[-1]

    if exit_price is None:
        # Check if reached deadline
        if last_ts < deadline:
            print(f"     Position STILL OPEN @ {last_ts.strftime('%H:%M')}")
            print(f"     Last price: {last_price:.2f}")
            unrealized = (last_price - entry) if side == "BUY" else (entry - last_price)
            print(f"     Unrealized: {unrealized:+.2f} ({unrealized/entry*100:+.3f}%)")
            return {"side": side, "entry": entry, "sl": sl, "tp": tp,
                    "exit": last_price, "exit_reason": "still_open",
                    "exit_ts": last_ts.isoformat(),
                    "pnl_pct": unrealized / entry * 100}
        else:
            # Time exit
            print(f"     TIME EXIT @ {last_ts.strftime('%H:%M')} (deadline {deadline.strftime('%H:%M')})")
            print(f"     Exit price: {last_price:.2f}")
            pnl = (last_price - entry) if side == "BUY" else (entry - last_price)
            print(f"     P&L: {pnl:+.2f} ({pnl/entry*100:+.3f}%)")
            return {"side": side, "entry": entry, "sl": sl, "tp": tp,
                    "exit": last_price, "exit_reason": "time_exit",
                    "exit_ts": last_ts.isoformat(), "pnl_pct": pnl/entry*100}
    else:
        pnl = (exit_price - entry) if side == "BUY" else (entry - exit_price)
        print(f"     {exit_reason} @ {exit_ts.strftime('%H:%M')} price={exit_price:.2f}")
        print(f"     P&L: {pnl:+.2f} ({pnl/entry*100:+.3f}%)")
        return {"side": side, "entry": entry, "sl": sl, "tp": tp,
                "exit": exit_price, "exit_reason": exit_reason,
                "exit_ts": exit_ts.isoformat(), "pnl_pct": pnl/entry*100}


def main():
    print(f"=== ECB Simulation pour {TODAY} ===")
    print(f"  Now: {datetime.now(PARIS_TZ).strftime('%Y-%m-%d %H:%M Paris')}")
    print(f"  Threshold: ±{MOMENTUM_THRESHOLD*100}%, obs window {OBS_MINUTES}min")
    results = {}
    for sym, tk in TICKERS.items():
        try:
            r = simulate_for_symbol(sym, tk)
            results[sym] = r
        except Exception as e:
            print(f"  ERR {sym}: {e}")
            results[sym] = None

    # Recap
    print(f"\n\n=== RECAP ===")
    n_signals = sum(1 for r in results.values() if r is not None)
    print(f"Signals generated: {n_signals}/3")
    for sym, r in results.items():
        if r is None:
            print(f"  {sym}: no signal")
        else:
            status = r.get("exit_reason", "?")
            pnl = r.get("pnl_pct", 0)
            print(f"  {sym}: {r['side']} @ {r['entry']:.2f} -> {status} pnl {pnl:+.2f}%")


if __name__ == "__main__":
    main()
