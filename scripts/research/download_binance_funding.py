#!/usr/bin/env python3
"""T1-C' — Download Binance perpetual funding rate history.

Telecharge le funding rate historique pour BTCUSDT et ETHUSDT depuis
2019-09-08 (lancement perp) jusqu'a aujourd'hui via API publique
`/fapi/v1/fundingRate`. Les funding events sont toutes les 8h.

Output:
  data/crypto/funding/BTCUSDT_funding.parquet
  data/crypto/funding/ETHUSDT_funding.parquet
  + daily aggregate columns: sum_3 events per day -> annualized rate

Usage:
  python scripts/research/download_binance_funding.py
  python scripts/research/download_binance_funding.py --backtest  # re-run T1-C
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "data" / "crypto" / "funding"
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_BASE = "https://fapi.binance.com"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
START_TS = int(pd.Timestamp("2019-09-08", tz="UTC").timestamp() * 1000)
END_TS = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)


def fetch_funding(symbol: str) -> pd.DataFrame:
    """Fetch all funding records for symbol via paged GET."""
    rows = []
    cur = START_TS
    while cur < END_TS:
        url = f"{API_BASE}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "startTime": cur, "limit": 1000}
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ERR {symbol} at {pd.Timestamp(cur, unit='ms')}: {e}")
            time.sleep(2)
            continue
        if not data:
            break
        rows.extend(data)
        last_ts = int(data[-1]["fundingTime"])
        if last_ts <= cur:
            break  # safety
        cur = last_ts + 1
        print(f"  {symbol} fetched {len(rows)} so far, latest {pd.Timestamp(last_ts, unit='ms')}")
        time.sleep(0.5)  # rate limit

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = df.set_index("fundingTime").sort_index()
    return df


def to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 8h funding events to daily annualized rate."""
    daily = df["fundingRate"].resample("1D").sum()  # 3 events/day typical
    # Annualized: rate per 8h * 3 events/day * 365
    annualized = daily * 3 * 365 / 3  # = daily * 365 (since daily is sum of 3)
    out = pd.DataFrame({
        "funding_daily_sum": daily,
        "funding_annualized": daily * 365,
        "n_events": df["fundingRate"].resample("1D").count(),
    })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true",
                    help="Re-run T1-C with real funding data")
    args = ap.parse_args()

    print("=== T1-C' Download Binance funding history ===\n")
    for sym in SYMBOLS:
        print(f"Fetching {sym}...")
        df = fetch_funding(sym)
        if df.empty:
            print(f"  EMPTY for {sym}")
            continue
        out_path = OUT_DIR / f"{sym}_funding.parquet"
        df.to_parquet(out_path)
        print(f"  Saved {len(df)} records -> {out_path}")

        # Daily aggregate
        daily = to_daily(df)
        daily_path = OUT_DIR / f"{sym}_funding_daily.parquet"
        daily.to_parquet(daily_path)
        print(f"  Daily aggregate -> {daily_path}")
        print(f"  Funding stats {sym}:")
        print(f"    median annualized: {daily['funding_annualized'].median()*100:.2f}%")
        print(f"    p10: {daily['funding_annualized'].quantile(0.1)*100:.2f}%")
        print(f"    p90: {daily['funding_annualized'].quantile(0.9)*100:.2f}%")
        print(f"    range: {daily.index.min().date()} -> {daily.index.max().date()}")

    if args.backtest:
        print("\n--- Re-running T1-C with real funding ---")
        # TODO in next iteration: import backtest_crypto_basis_carry and override funding source
        print("  TODO: integrate to scripts/research/backtest_crypto_basis_carry.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
