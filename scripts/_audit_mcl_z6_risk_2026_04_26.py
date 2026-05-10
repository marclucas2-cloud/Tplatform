"""Audit risque cible 2026-04-26: position MCL live ouverte 24/04 14:00:48.

Question: le SL 77.47 -> 56.58 (27%) est-il calibre sur MCLZ6 reel
ou sur le ticker proxy CL=F (front-month continuous) ?

Sources tentees:
  - yfinance CL=F (front-month continuous, deja dans MCL_1D.parquet)
  - yfinance CLZ26.NYM ou similaire pour Z6 reel
  - IBKR via ib_insync si yfinance ne donne pas Z6
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.data.parquet_safe_loader import load_daily_parquet_safe  # noqa: E402

ENTRY = 77.47
SL = 56.58
TP = 109.49
MULTIPLIER = 100  # MCL


def annualized_vol(returns: pd.Series, period_days: int = 252) -> float:
    return float(returns.std() * np.sqrt(period_days))


def realised_vol(close: pd.Series, lookback: int) -> float:
    rets = close.pct_change().dropna().tail(lookback)
    if len(rets) < lookback // 2:
        return float("nan")
    return float(rets.std())


def atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def main():
    cl_path = ROOT / "data" / "futures" / "MCL_1D.parquet"
    cl_df = load_daily_parquet_safe(cl_path)
    print(f"=== CL=F (front continuous, via yfinance ticker CL=F) ===")
    print(f"Rows: {len(cl_df)}, last: {cl_df.index.max().date()}, last_close: {cl_df['close'].iloc[-1]:.2f}")

    # Compute realised vol over 20/40/60 days (in % daily then annualized)
    last_close = cl_df["close"].iloc[-1]
    rv20 = cl_df["close"].pct_change().dropna().tail(20).std()
    rv40 = cl_df["close"].pct_change().dropna().tail(40).std()
    rv60 = cl_df["close"].pct_change().dropna().tail(60).std()
    atr14 = atr(cl_df.tail(40), period=14)
    atr20 = atr(cl_df.tail(40), period=20)
    daily_range_usd_avg_20 = (cl_df["high"] - cl_df["low"]).tail(20).mean() * MULTIPLIER

    print(f"\nRealised vol daily (sigma) — CL=F:")
    print(f"  20d: {rv20*100:.2f}%   (annualized {rv20*np.sqrt(252)*100:.1f}%)")
    print(f"  40d: {rv40*100:.2f}%   (annualized {rv40*np.sqrt(252)*100:.1f}%)")
    print(f"  60d: {rv60*100:.2f}%   (annualized {rv60*np.sqrt(252)*100:.1f}%)")
    print(f"\nATR — CL=F:")
    print(f"  ATR(14) = {atr14:.3f} pts = ${atr14*MULTIPLIER:.0f}/contract")
    print(f"  ATR(20) = {atr20:.3f} pts = ${atr20*MULTIPLIER:.0f}/contract")
    print(f"\nRange journalier moyen 20d (high-low) = ${daily_range_usd_avg_20:.0f}/contract")

    # ============================================================================
    # Try fetch MCLZ6 / CLZ26 specifically
    # ============================================================================
    print(f"\n=== Tentative fetch MCLZ6 / CLZ26 (deferred dec 2026) via yfinance ===")
    try:
        import yfinance as yf
        for ticker in ["CLZ26.NYM", "CL=Z26", "CLZ6.NYM", "MCLZ6.NYM", "MCLZ26.NYM"]:
            print(f"  Trying {ticker}...")
            try:
                df = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=False)
                if df is not None and len(df) > 5:
                    print(f"  OK: {ticker} {len(df)} rows last={df.index.max().date()}")
                    break
                else:
                    print(f"    empty")
            except Exception as e:
                print(f"    err: {e}")
        else:
            print("  AUCUN ticker yfinance ne renvoie de data pour Z6.")
    except ImportError:
        print("  yfinance not installed")

    # ============================================================================
    # Tente IBKR via paper gateway pour MCLZ6 historique
    # ============================================================================
    print(f"\n=== Tentative IBKR MCLZ6 historical (via paper gateway 4003) ===")
    try:
        from ib_insync import IB, Future
        ib = IB()
        try:
            ib.connect("127.0.0.1", 4003, clientId=98, timeout=10)
        except Exception as e:
            print(f"  IBKR paper connect FAIL: {e}")
            ib = None
        if ib is not None and ib.isConnected():
            contract = Future(symbol="MCL", exchange="NYMEX", currency="USD",
                              localSymbol="MCLZ6")
            details = ib.reqContractDetails(contract)
            if details:
                qualified = details[0].contract
                print(f"  MCLZ6 qualified: conId={qualified.conId}")
                # Request 60 daily bars
                bars = ib.reqHistoricalData(
                    qualified,
                    endDateTime="",
                    durationStr="60 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    timeout=20,
                )
                if bars:
                    z6_df = pd.DataFrame([
                        {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
                         "close": b.close, "volume": b.volume}
                        for b in bars
                    ])
                    z6_df["date"] = pd.to_datetime(z6_df["date"])
                    z6_df = z6_df.set_index("date").sort_index()
                    print(f"  MCLZ6 bars: {len(z6_df)} last={z6_df.index.max().date()} last_close={z6_df['close'].iloc[-1]:.2f}")
                    print(f"  MCLZ6 first 3:\n{z6_df.head(3)}")
                    print(f"  MCLZ6 last 3:\n{z6_df.tail(3)}")
                    # Compute Z6 vols
                    z6_rv20 = z6_df["close"].pct_change().dropna().tail(20).std()
                    z6_rv40 = z6_df["close"].pct_change().dropna().tail(40).std()
                    z6_atr14 = atr(z6_df.tail(40), period=14)
                    z6_atr20 = atr(z6_df.tail(40), period=20)
                    z6_range_usd = (z6_df["high"] - z6_df["low"]).tail(20).mean() * MULTIPLIER
                    print(f"\nMCLZ6 sigma daily 20d: {z6_rv20*100:.2f}% (ann {z6_rv20*np.sqrt(252)*100:.1f}%)")
                    print(f"MCLZ6 sigma daily 40d: {z6_rv40*100:.2f}% (ann {z6_rv40*np.sqrt(252)*100:.1f}%)")
                    print(f"MCLZ6 ATR(14): {z6_atr14:.3f} = ${z6_atr14*MULTIPLIER:.0f}/contract")
                    print(f"MCLZ6 ATR(20): {z6_atr20:.3f} = ${z6_atr20*MULTIPLIER:.0f}/contract")
                    print(f"MCLZ6 range journalier moy 20d: ${z6_range_usd:.0f}/contract")

                    # Base / spread CL=F vs MCLZ6
                    common_dates = cl_df.index.intersection(z6_df.index)
                    if len(common_dates) > 5:
                        base = cl_df.loc[common_dates, "close"] - z6_df.loc[common_dates, "close"]
                        print(f"\nBase CL=F - MCLZ6 (last 5 days):")
                        print(base.tail(5))
                        print(f"\nBase mean (recent 20d): ${base.tail(20).mean():.2f}")
                        print(f"Base std (recent 20d):  ${base.tail(20).std():.2f}")

                    # Verdict: SL 27% sur MCLZ6 = combien d'ATR / sigmas ?
                    sl_distance_pct = (ENTRY - SL) / ENTRY
                    sl_distance_pts = ENTRY - SL
                    print(f"\n=== VERDICT ===")
                    print(f"SL distance: {sl_distance_pts:.2f} points = {sl_distance_pct*100:.1f}%")
                    print(f"En ATR(14) MCLZ6:    {sl_distance_pts/z6_atr14:.1f}x")
                    print(f"En ATR(14) CL=F:     {sl_distance_pts/atr14:.1f}x")
                    print(f"En sigma daily MCLZ6 20d: {sl_distance_pct/z6_rv20:.1f}x")
                    print(f"En sigma daily CL=F 20d:  {sl_distance_pct/rv20:.1f}x")
                    z6_implied_days_to_sl = sl_distance_pct / z6_rv20
                    cl_implied_days_to_sl = sl_distance_pct / rv20
                    print(f"Si vol 20d Z6 stable: ~{z6_implied_days_to_sl:.0f} sigma-jours (1-day move) pour atteindre SL")
                    print(f"Si vol 20d CL stable: ~{cl_implied_days_to_sl:.0f} sigma-jours pour atteindre SL")
                else:
                    print("  No bars returned")
            else:
                print("  No contract details for MCLZ6")
            ib.disconnect()
    except ImportError:
        print("  ib_insync not installed")
    except Exception as e:
        print(f"  IBKR err: {e}")


if __name__ == "__main__":
    main()
