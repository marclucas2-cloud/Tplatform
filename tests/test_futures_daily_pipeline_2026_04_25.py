"""Regression tests for the daily futures data pipeline fix (2026-04-25).

Bug caught in production:
  refreshed *_1D.parquet files kept a legacy ``datetime`` column with NaT on
  new rows. The runtime loader overwrote a valid DatetimeIndex with that column,
  then dropped the fresh rows as NaT.

This suite locks three things:
  1. futures_runner keeps a valid DatetimeIndex instead of trusting the stale
     legacy column.
  2. refresh_futures_parquet.load_existing() sanitizes the same corruption.
  3. VIX is part of the default refresh map so paper sleeves using a fear filter
     do not silently run on stale data.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _corrupted_daily_frame() -> pd.DataFrame:
    idx = pd.to_datetime(["2026-04-22", "2026-04-23", "2026-04-24"])
    return pd.DataFrame(
        {
            "open": [7100.0, 7120.0, 7140.0],
            "high": [7150.0, 7180.0, 7200.0],
            "low": [7080.0, 7075.0, 7130.0],
            "close": [7122.25, 7132.50, 7195.50],
            "volume": [100.0, 120.0, 130.0],
            # Legacy corruption: only old rows had a datetime column populated.
            "datetime": [pd.Timestamp("2026-04-22"), pd.NaT, pd.NaT],
        },
        index=idx,
    )


def test_futures_runner_loader_preserves_valid_index_over_legacy_datetime(tmp_path: Path):
    from core.worker.cycles.futures_runner import _load_futures_daily_frame

    path = tmp_path / "MES_1D.parquet"
    _corrupted_daily_frame().to_parquet(path)

    loaded = _load_futures_daily_frame(path)

    assert list(loaded.index.strftime("%Y-%m-%d")) == [
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
    ]
    assert "datetime" not in loaded.columns
    assert float(loaded.iloc[-1]["close"]) == 7195.50


def test_refresh_loader_sanitizes_legacy_datetime_column(tmp_path: Path):
    from scripts.refresh_futures_parquet import load_existing

    path = tmp_path / "MGC_1D.parquet"
    _corrupted_daily_frame().to_parquet(path)

    loaded = load_existing(path)

    assert loaded is not None
    assert loaded.index.max() == pd.Timestamp("2026-04-24")
    assert "datetime" not in loaded.columns


def test_refresh_symbol_map_includes_vix():
    from scripts.refresh_futures_parquet import SYMBOL_MAP

    assert SYMBOL_MAP["VIX"] == "^VIX"
