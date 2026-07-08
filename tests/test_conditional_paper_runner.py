from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from core.worker.cycles.conditional_paper_runner import (
    BNB_STRATEGY_ID,
    ZN_STRATEGY_ID,
    _simulate_bnb_trade,
    compute_bnb_defensive_signal,
    month_end_entry_date,
    month_end_exit_date,
)

ROOT = Path(__file__).resolve().parent.parent


def _synthetic_bnb_panel() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=121, freq="D")
    bnb = [100.0 + i * 0.5 for i in range(121)]
    btc = [1000.0 + i * 1.0 for i in range(121)]
    # Current target bar is intentionally awful. The signal must ignore it and
    # use bars strictly before target_date.
    bnb[-1] = 50.0
    btc[-1] = 900.0
    return pd.DataFrame(
        {
            "bnb_open": bnb,
            "bnb_high": [x * 1.02 for x in bnb],
            "bnb_low": [x * 0.98 for x in bnb],
            "bnb_close": bnb,
            "btc_open": btc,
            "btc_high": [x * 1.01 for x in btc],
            "btc_low": [x * 0.99 for x in btc],
            "btc_close": btc,
        },
        index=dates,
    )


def test_bnb_signal_uses_prior_closed_bars_only():
    panel = _synthetic_bnb_panel()
    target = panel.index[-1]

    signal = compute_bnb_defensive_signal(panel, target)

    assert signal.enter is True
    assert signal.reason == "enter"
    assert signal.bnb_close != 50.0


def test_bnb_signal_blocks_when_btc_below_sma100():
    panel = _synthetic_bnb_panel()
    panel.loc[panel.index[:-1], "btc_close"] = list(reversed([1000.0 + i for i in range(120)]))

    signal = compute_bnb_defensive_signal(panel, panel.index[-1])

    assert signal.enter is False
    assert "btc_above_sma100" in signal.reason


def test_bnb_same_bar_stop_is_conservative():
    bar = pd.Series({"bnb_open": 100.0, "bnb_high": 110.0, "bnb_low": 95.0, "bnb_close": 108.0})

    trade = _simulate_bnb_trade(bar)

    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl_usd"] < 0


def test_zn_month_end_calendar_dates():
    assert month_end_entry_date(date(2026, 5, 14)) == date(2026, 5, 27)
    assert month_end_exit_date(date(2026, 5, 27)) == date(2026, 6, 2)


def test_new_paper_strategies_are_registry_only_paper():
    reg = yaml.safe_load((ROOT / "config" / "quant_registry.yaml").read_text(encoding="utf-8"))
    ids = {row["strategy_id"]: row for row in reg["strategies"]}

    for strategy_id in (BNB_STRATEGY_ID, ZN_STRATEGY_ID):
        row = ids[strategy_id]
        assert row["status"] == "paper_only"
        assert row["is_live"] is False
        assert row["paper_start_at"] == "2026-05-14"
        manifest = ROOT / row["wf_manifest_path"]
        assert manifest.exists()
