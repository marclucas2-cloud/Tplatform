"""Regression tests for CAM runtime executable-fallback behavior."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield


class _StubFeed:
    def __init__(self, closes: dict[str, list[float]]):
        self._frames = {
            symbol: pd.DataFrame({"close": values})
            for symbol, values in closes.items()
        }

    def get_bars(self, symbol: str, n: int):
        frame = self._frames.get(symbol)
        if frame is None:
            return None
        return frame.tail(n)

    def get_latest_bar(self, symbol: str):
        frame = self._frames.get(symbol)
        if frame is None or frame.empty:
            return None
        row = frame.iloc[-1]
        return type("Bar", (), {"close": float(row["close"])})()


def test_cross_asset_ranked_candidates_are_sorted_and_signal_keeps_pct_intent():
    from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum

    feed = _StubFeed({
        "MES": [100, 101, 102, 103, 108],
        "MNQ": [100, 101, 102, 103, 112],
        "M2K": [100, 100, 100, 100, 101],
        "MGC": [100, 101, 102, 102, 104],
        "MCL": [100, 101, 102, 103, 110],
    })
    strat = CrossAssetMomentum(lookback_days=3, min_momentum=0.02)
    strat.set_data_feed(feed)

    ranked = strat.get_ranked_candidates()

    assert [item["symbol"] for item in ranked[:3]] == ["MNQ", "MCL", "MES"]
    sig = strat.build_signal_for_candidate(ranked[1])
    assert sig.symbol == "MCL"
    assert sig.stop_loss == 106.7
    assert sig.take_profit == pytest.approx(118.8, rel=1e-9)
    assert strat.get_parameters()["sl_pct"] == 0.03
    assert strat.get_parameters()["tp_pct"] == 0.08


def test_cam_executable_fallback_skips_over_budget_mnq_and_picks_mes():
    from core.worker.cycles.futures_runner import _select_executable_cam_candidate

    ranked = [
        {"symbol": "MNQ", "momentum": 0.12, "close": 27191.0},
        {"symbol": "MES", "momentum": 0.09, "close": 7158.0},
        {"symbol": "MCL", "momentum": 0.08, "close": 105.0},
    ]

    selected = _select_executable_cam_candidate(
        ranked,
        current_risk_usd=0.0,
        risk_budget_usd=1318.0,
        sl_pct=0.03,
        min_momentum=0.02,
        occupied_symbols=set(),
        traded_symbols=set(),
        qty=1,
    )

    assert selected is not None
    assert selected["symbol"] == "MES"
    assert selected["estimated_risk_usd"] == pytest.approx(1073.7, rel=1e-4)


def test_futures_runner_cam_runtime_source_guards():
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(
        encoding="utf-8",
    )
    assert 'signals[-1][2]["ranked_candidates"] = list(_cam_ranked_candidates)' in src
    assert 'feed.get_latest_bar(sig.symbol)' in src
    assert '_maybe_rebind_cam_signal("risk_budget")' in src
