from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research.crypto_bull_bear_paper_candidates_2026_04_24 import (
    funding_hybridtrend,
    weekend_reversal,
)


def test_funding_hybridtrend_waits_for_warmup_and_shifted_signal():
    idx = pd.date_range("2020-01-01", periods=160, freq="D")
    price = pd.Series(np.linspace(100, 140, len(idx)), index=idx)
    funding = pd.Series(np.concatenate([np.zeros(100), np.full(60, 0.01)]), index=idx)
    regime = pd.Series(1, index=idx)

    pnl, trades = funding_hybridtrend(
        price=price,
        funding_daily=funding,
        regime_daily=regime,
        z_threshold=1.5,
        hold_days=3,
    )

    assert trades > 0
    assert (pnl.iloc[:90] == 0).all()


def test_weekend_reversal_only_trades_after_weekend_drop():
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    price = pd.Series(100.0, index=idx)
    # Force a Sunday drop, then a Monday bounce.
    sunday = idx[4]
    monday = idx[5]
    price.loc[sunday] = 94.0
    price.loc[monday] = 97.0

    pnl, trades = weekend_reversal(
        price=price,
        weekend_drop_threshold=-0.03,
        hold_days=3,
        bull_filter=False,
    )

    assert trades >= 1
    assert (pnl.iloc[:5] == 0).all()
