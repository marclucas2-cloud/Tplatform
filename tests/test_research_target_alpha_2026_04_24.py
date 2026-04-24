from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research.target_alpha_us_sectors_and_new_assets_2026_04_24 import (
    pair_ratio_momentum,
    sector_top1_long_only,
)


def _price_df(columns: list[str], periods: int = 70) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=periods, freq="B")
    data = {}
    for i, col in enumerate(columns, start=1):
        data[col] = np.linspace(100 + i, 120 + i, periods)
    return pd.DataFrame(data, index=idx)


def test_sector_top1_long_only_has_no_pnl_before_shifted_entry():
    prices = _price_df(["XLK", "XLE", "XLV"], periods=80)
    pnl = sector_top1_long_only(prices, lookback=20, hold_days=5, label="test")
    # With a shifted position, no PnL should appear before the first day after
    # the first valid rebalance date.
    assert (pnl.iloc[:21] == 0).all()


def test_pair_ratio_momentum_constant_ratio_stays_flat():
    idx = pd.date_range("2020-01-01", periods=90, freq="B")
    base = np.linspace(100, 130, len(idx))
    prices = pd.DataFrame(
        {
            "XLE": base,
            "XLK": base * 2.0,
        },
        index=idx,
    )
    pnl = pair_ratio_momentum(prices, "XLE", "XLK", lookback=20, band=1.0, label="pair")
    assert np.isclose(pnl.fillna(0).values, 0.0, atol=1e-12).all()
