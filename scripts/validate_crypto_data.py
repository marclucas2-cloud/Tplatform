"""
Validate collected crypto data quality.

Usage:
    python scripts/validate_crypto_data.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "crypto" / "candles"
FUNDING_DIR = ROOT / "data" / "crypto" / "funding"


def validate_candles(path: Path) -> dict:
    """Validate a single candle Parquet file."""
    df = pd.read_parquet(path)
    issues = []

    if df.empty:
        return {"file": path.name, "status": "EMPTY", "rows": 0, "issues": ["empty file"]}

    # Check required columns
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        issues.append(f"missing columns: {missing}")

    # Zero volume
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > 0:
        issues.append(f"{zero_vol} zero-volume candles ({zero_vol / len(df) * 100:.1f}%)")

    # OHLC consistency
    bad = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
    ).sum()
    if bad > 0:
        issues.append(f"{bad} invalid OHLC")

    # Duplicates
    dupes = df.duplicated(subset=["timestamp"]).sum()
    if dupes > 0:
        issues.append(f"{dupes} duplicate timestamps")

    # Time range
    span_days = 0
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        span_days = (df["timestamp"].max() - df["timestamp"].min()).days

    return {
        "file": path.name,
        "status": "OK" if not issues else "WARNINGS",
        "rows": len(df),
        "span_days": span_days,
        "issues": issues,
    }


def main():
    print("=== Crypto Data Validation ===\n")

    # Candles
    if DATA_DIR.exists():
        parquet_files = sorted(DATA_DIR.glob("*.parquet"))
        print(f"Candle files: {len(parquet_files)}")
        for f in parquet_files:
            result = validate_candles(f)
            status = "OK" if result["status"] == "OK" else "WARN"
            print(f"  [{status}] {result['file']}: {result['rows']} rows, {result['span_days']}d")
            for issue in result["issues"]:
                print(f"       {issue}")
    else:
        print(f"  No candle data found at {DATA_DIR}")

    # Funding
    print()
    if FUNDING_DIR.exists():
        funding_files = sorted(FUNDING_DIR.glob("*.parquet"))
        print(f"Funding files: {len(funding_files)}")
        for f in funding_files:
            df = pd.read_parquet(f)
            print(f"  {f.name}: {len(df)} rates")
    else:
        print(f"  No funding data found at {FUNDING_DIR}")


if __name__ == "__main__":
    main()
