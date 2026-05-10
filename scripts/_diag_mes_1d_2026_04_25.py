"""Diagnostic 2026-04-25: pourquoi mes_mr_vix_spike a vu bar 2026-04-08 vendredi.

Reproduit le pattern de chargement DataFeed de futures_runner.py L187+.
Verifie aussi MES_LONG.parquet et VIX_1D.parquet.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

ROOT = Path("/opt/trading-platform")
data_dir = ROOT / "data" / "futures"

print("=" * 70)
print("Reproduction futures_runner.py loading pattern (lines 186-203)")
print("=" * 70)

data_sources = {}
for sym in ["MES", "MNQ", "M2K", "MIB", "ESTX50", "VIX", "MGC", "MCL", "DAX", "CAC40"]:
    fpath = data_dir / f"{sym}_1D.parquet"
    if not fpath.exists():
        print(f"{sym}: NO FILE")
        continue
    df = pd.read_parquet(fpath)
    df.columns = [c.lower() for c in df.columns]
    if "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"])
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    data_sources[sym] = df

print()
for sym, df in data_sources.items():
    last_close = df["close"].iloc[-1] if "close" in df.columns else "N/A"
    print(f"{sym:8s}: rows={len(df):4d} last_index={df.index.max()} last_close={last_close}")

print()
print("=" * 70)
print("Diagnostic profond MES_1D")
print("=" * 70)
df_mes = data_sources["MES"]
print(f"\nFull index head: {df_mes.index[:3].tolist()}")
print(f"Full index tail: {df_mes.index[-3:].tolist()}")
print(f"\nLast 5 rows:")
print(df_mes.tail(5))
print(f"\nLooking for bar 2026-04-08:")
target = pd.Timestamp("2026-04-08")
if target in df_mes.index:
    print(f"  Found: {df_mes.loc[target]}")
else:
    print(f"  Not found in index")

print()
print("=" * 70)
print("VIX_1D last bar")
print("=" * 70)
df_vix = data_sources.get("VIX")
if df_vix is not None:
    print(df_vix.tail(3))
    print(f"VIX last close: {df_vix['close'].iloc[-1]}")
    age = (pd.Timestamp.now().normalize() - df_vix.index.max()).days
    print(f"VIX_1D age days: {age}")

print()
print("=" * 70)
print("MES_LONG.parquet (used by some research scripts, not by futures_runner)")
print("=" * 70)
df_long = pd.read_parquet(data_dir / "MES_LONG.parquet")
df_long.index = pd.to_datetime(df_long.index)
if df_long.index.tz is not None:
    df_long.index = df_long.index.tz_localize(None)
print(f"Rows={len(df_long)} last={df_long.index.max()} last_close={df_long['close'].iloc[-1]}")

print()
print("=" * 70)
print("Now simulate DataFeed with set_timestamp = vendredi 14h UTC")
print("=" * 70)
import sys
sys.path.insert(0, str(ROOT))
from core.backtester_v2.data_feed import DataFeed
feed = DataFeed(data_sources)
# Simulate cycle vendredi 24/04 14h UTC
ts_friday_14h = pd.Timestamp("2026-04-24 14:00:00", tz="UTC")
feed.set_timestamp(ts_friday_14h)
bar_mes = feed.get_latest_bar("MES")
bar_vix = feed.get_latest_bar("VIX")
print(f"feed.set_timestamp({ts_friday_14h})")
print(f"feed.get_latest_bar('MES'): {bar_mes}")
print(f"feed.get_latest_bar('VIX'): {bar_vix}")

print()
print("Now simulate set_timestamp = aujourd'hui 17h UTC (samedi)")
ts_now = pd.Timestamp.now(tz="UTC")
feed.set_timestamp(ts_now)
bar_mes_now = feed.get_latest_bar("MES")
print(f"feed.set_timestamp({ts_now})")
print(f"feed.get_latest_bar('MES'): {bar_mes_now}")
