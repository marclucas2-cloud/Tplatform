"""Audit FX data availability."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd

data_dir = Path(__file__).resolve().parent.parent / "data" / "fx"
for f in sorted(data_dir.glob("*.parquet")):
    df = pd.read_parquet(f)
    first = pd.Timestamp(df.index[0])
    last = pd.Timestamp(df.index[-1])
    cols = list(df.columns[:6])
    print(f"{f.name:25s} {len(df):>6} candles  {first.strftime('%Y-%m-%d')} -> {last.strftime('%Y-%m-%d')}  cols={cols}")
