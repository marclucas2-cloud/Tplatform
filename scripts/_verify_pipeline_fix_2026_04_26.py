"""Verify post-fix loader voit la bonne data."""
from core.worker.cycles.futures_runner import _load_futures_daily_frame
from pathlib import Path

data_dir = Path("data/futures")
print("=== Post-fix state via _load_futures_daily_frame ===")
for sym in ["MES", "MNQ", "M2K", "MGC", "MCL", "VIX"]:
    fpath = data_dir / f"{sym}_1D.parquet"
    if fpath.exists():
        df = _load_futures_daily_frame(fpath)
        last = df.index.max()
        close = df["close"].iloc[-1] if "close" in df.columns else None
        has_dt_col = "datetime" in df.columns
        print(f"{sym:6s}: rows={len(df):4d} last={last} close={close} datetime_col={has_dt_col}")
