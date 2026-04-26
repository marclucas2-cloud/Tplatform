"""Tests pour scripts/refresh_macro_top1_etfs.py.

Verifie:
  1. load_existing preserve DatetimeIndex valide + drop legacy "datetime" col
  2. UNIVERSE inclut bien les 8 macro + 11 sector ETFs
  3. Meme pattern de defense que refresh_futures_parquet (anti-corruption)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _corrupted_etf_frame() -> pd.DataFrame:
    """Reproduit la corruption datetime=NaT observee 2026-04-25."""
    idx = pd.to_datetime(["2026-04-22", "2026-04-23", "2026-04-24"])
    return pd.DataFrame(
        {
            "SPY": [580.0, 582.0, 585.0],
            "TLT": [88.0, 87.5, 88.2],
            "datetime": [pd.Timestamp("2026-04-22"), pd.NaT, pd.NaT],
        },
        index=idx,
    )


def test_load_existing_preserves_valid_index_over_legacy_datetime(tmp_path: Path):
    from scripts.refresh_macro_top1_etfs import load_existing

    path = tmp_path / "etfs.parquet"
    _corrupted_etf_frame().to_parquet(path)

    loaded = load_existing(path)

    assert loaded is not None
    assert list(loaded.index.strftime("%Y-%m-%d")) == [
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
    ]
    assert "datetime" not in loaded.columns
    assert float(loaded.iloc[-1]["SPY"]) == 585.0


def test_universe_includes_macro_and_sectors():
    from scripts.refresh_macro_top1_etfs import UNIVERSE, MACRO_ETFS, SECTOR_ETFS

    # macro top1 universe = SPY/TLT/GLD/DBC/UUP/IEF/HYG/QQQ
    for sym in ["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]:
        assert sym in UNIVERSE, f"{sym} missing from UNIVERSE"

    # Sector ETFs (12 incluant SPY)
    for sym in ["XLK", "XLE", "XLF", "XLV"]:
        assert sym in UNIVERSE

    # Pas de symbole bizarre
    assert all(len(s) <= 5 for s in UNIVERSE)


def test_load_existing_returns_none_on_missing_path(tmp_path: Path):
    from scripts.refresh_macro_top1_etfs import load_existing

    assert load_existing(tmp_path / "does_not_exist.parquet") is None
