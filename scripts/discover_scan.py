"""Strategy Discovery — Market scan + gap analysis."""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

def main():
    print("=== MARKET CONDITIONS ===")
    mes = pd.read_parquet(ROOT / "data/futures/MES_1D.parquet")
    mes.columns = [c.lower() for c in mes.columns]
    c = mes["close"]
    ema20 = c.ewm(20).mean().iloc[-1]
    ema50 = c.ewm(50).mean().iloc[-1]
    ema200 = c.ewm(200).mean().iloc[-1]
    print(f"MES: {c.iloc[-1]:.0f} (EMA20={ema20:.0f} EMA50={ema50:.0f} EMA200={ema200:.0f})")
    ret_5d = (c.iloc[-1] / c.iloc[-5] - 1) * 100
    ret_21d = (c.iloc[-1] / c.iloc[-21] - 1) * 100
    ret_63d = (c.iloc[-1] / c.iloc[-63] - 1) * 100
    vol20 = c.pct_change().tail(20).std() * np.sqrt(252) * 100
    print(f"  5d: {ret_5d:+.1f}% | 21d: {ret_21d:+.1f}% | 63d: {ret_63d:+.1f}% | Vol20: {vol20:.0f}%")

    trend = "BULL" if ema50 > ema200 else "BEAR"
    delta = c.diff()
    gain = delta.clip(lower=0).tail(14).mean()
    loss = delta.clip(upper=0).abs().tail(14).mean()
    rsi14 = 100 - 100 / (1 + gain / loss) if loss > 0 else 50
    print(f"  Regime: {trend} | RSI14: {rsi14:.0f}")

    # Signals today
    print("\n=== SIGNALS TODAY ===")
    overnight = "BUY" if c.iloc[-1] > ema20 else "NO"
    tsmom = "BUY" if ret_63d > 0 else "SELL"
    print(f"  Overnight MES: {overnight} (close vs EMA20)")
    print(f"  TSMOM MES: {tsmom} (63d ret {ret_63d:+.1f}%)")

    # Multi-asset scan
    print("\n=== MULTI-ASSET SCAN ===")
    for sym in ["MNQ", "MCL", "MGC", "M2K", "VIX"]:
        try:
            df = pd.read_parquet(ROOT / f"data/futures/{sym}_1D.parquet")
            df.columns = [c.lower() for c in df.columns]
            cl = df["close"]
            e20 = cl.ewm(20).mean().iloc[-1]
            e50 = cl.ewm(50).mean().iloc[-1]
            e200 = cl.ewm(200).mean().iloc[-1]
            r5 = (cl.iloc[-1] / cl.iloc[-5] - 1) * 100
            r63 = (cl.iloc[-1] / cl.iloc[-63] - 1) * 100
            vol = cl.pct_change().tail(20).std() * np.sqrt(252) * 100
            trnd = "BULL" if e50 > e200 else "BEAR"
            print(f"  {sym}: {cl.iloc[-1]:.0f} | {trnd} | 5d:{r5:+.1f}% 63d:{r63:+.1f}% vol:{vol:.0f}%")
        except Exception:
            print(f"  {sym}: no data")

    # EU indices
    print("\n=== EU INDICES ===")
    for sym in ["DAX", "CAC40", "ESTX50", "MIB", "FTSE100"]:
        try:
            df = pd.read_parquet(ROOT / f"data/eu/{sym}_1D.parquet")
            df.columns = [c.lower() for c in df.columns]
            cl = df["close"]
            r5 = (cl.iloc[-1] / cl.iloc[-5] - 1) * 100
            r21 = (cl.iloc[-1] / cl.iloc[-21] - 1) * 100
            print(f"  {sym}: {cl.iloc[-1]:.0f} | 5d:{r5:+.1f}% 21d:{r21:+.1f}%")
        except Exception:
            pass

    # Cross-asset correlation (last 60 days)
    print("\n=== CORRELATION (60d returns) ===")
    rets = {}
    for sym in ["MES", "MNQ", "MCL", "MGC"]:
        try:
            df = pd.read_parquet(ROOT / f"data/futures/{sym}_1D.parquet")
            df.columns = [c.lower() for c in df.columns]
            rets[sym] = df["close"].pct_change().tail(60)
        except Exception:
            pass
    if len(rets) >= 2:
        corr = pd.DataFrame(rets).corr()
        for i in range(len(corr)):
            for j in range(i + 1, len(corr)):
                a, b = corr.index[i], corr.columns[j]
                print(f"  {a}/{b}: {corr.iloc[i, j]:.2f}")

    # Strategy ideas
    print("\n" + "=" * 60)
    print("  STRATEGY CANDIDATES")
    print("=" * 60)

    print("""
1. TURNAROUND TUESDAY (MES)
   Edge: Monday selloffs reverse on Tuesday. Well-documented calendar effect.
   Signal: If Monday close < Monday open (red), BUY Tuesday open, SELL Tuesday close.
   Expected: 55% WR, Sharpe 0.8-1.2, ~50 trades/year.
   Correlation: LOW with Overnight (different days).
   Capital: 1 MES contract = $1,400 margin.

2. GOLD-EQUITY DIVERGENCE (MGC vs MES)
   Edge: When gold and equities diverge, mean-revert within 5 days.
   Signal: If 5d return MES > +2% AND 5d return MGC < -1%, SHORT MES.
   Expected: 45% WR but high payoff ratio, Sharpe 0.7-1.0.
   Correlation: NEGATIVE with MES trend strats (hedge).
   Capital: 1 MGC = $1,000 margin.

3. VIX MEAN REVERSION (VIX level filter)
   Edge: After VIX spikes > 25, equities bounce within 3-5 days.
   Signal: If VIX > 25 AND MES RSI14 < 30, BUY MES. Exit when VIX < 20.
   Expected: 65% WR, Sharpe 1.5+, ~15 trades/year (rare but high conviction).
   Correlation: LOW (only triggers in crisis).
   Capital: 1 MES = $1,400.

4. CRYPTO RANGE BREAKOUT (BTC daily)
   Edge: After 10+ days in <5% range, breakout in direction of first move.
   Signal: If 10d range < 5%, wait for daily close outside range. Follow.
   Expected: 45% WR, 2:1 RR, ~20 trades/year.
   Why now: BTC in tight range ($67K-$72K), breakout imminent.

5. FIRST HOUR MOMENTUM (MES intraday)
   Edge: First 30min direction predicts rest of day 60% of time.
   Signal: If MES gains > 0.3% in first 30min, BUY. Hold until close.
   Requires: 5-minute data (we have it).
   Expected: 55% WR, Sharpe 1.0, ~200 trades/year.
   Correlation: LOW with daily strats.
""")

if __name__ == "__main__":
    main()
