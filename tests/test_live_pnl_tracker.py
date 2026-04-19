"""Tests for scripts/live_pnl_tracker.py — daily P&L tracking math."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location(
    "live_pnl_tracker", ROOT / "scripts" / "live_pnl_tracker.py"
)
tracker = importlib.util.module_from_spec(SPEC)


@pytest.fixture
def tmp_tracker(tmp_path, monkeypatch):
    """Redirect tracker IO paths to tmp_path for isolation."""
    SPEC.loader.exec_module(tracker)
    out_dir = tmp_path / "live_pnl"
    out_dir.mkdir()
    monkeypatch.setattr(tracker, "OUT_DIR", out_dir)
    monkeypatch.setattr(tracker, "CSV_PATH", out_dir / "daily_equity.csv")
    monkeypatch.setattr(tracker, "JSONL_PATH", out_dir / "daily_pnl.jsonl")
    monkeypatch.setattr(tracker, "SUMMARY_PATH", out_dir / "summary.json")
    # Mock broker fetches: IBKR returns 10000, Binance returns 8000
    monkeypatch.setattr(tracker, "_fetch_ibkr_live_equity", lambda: 10000.0)
    monkeypatch.setattr(tracker, "_fetch_binance_live_equity", lambda: 8000.0)
    return tracker


def test_first_snapshot_creates_files(tmp_tracker):
    result = tmp_tracker.take_snapshot(date(2026, 4, 19))
    assert "row" in result
    assert result["row"]["total_equity_usd"] == 18000.0
    assert result["row"]["daily_return_pct"] == 0.0  # first snapshot
    assert result["row"]["drawdown_pct"] == 0.0
    assert tmp_tracker.CSV_PATH.exists()
    assert tmp_tracker.JSONL_PATH.exists()
    assert tmp_tracker.SUMMARY_PATH.exists()


def test_second_snapshot_computes_daily_return(tmp_tracker, monkeypatch):
    tmp_tracker.take_snapshot(date(2026, 4, 19))
    # Day 2: equity rises from 18000 → 18900 (+5%)
    monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda: 10500.0)
    monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda: 8400.0)
    result = tmp_tracker.take_snapshot(date(2026, 4, 20))
    row = result["row"]
    assert row["total_equity_usd"] == 18900.0
    assert abs(row["daily_return_pct"] - 5.0) < 0.01
    assert row["drawdown_pct"] == 0.0
    assert row["peak_equity_usd"] == 18900.0


def test_drawdown_computation(tmp_tracker, monkeypatch):
    tmp_tracker.take_snapshot(date(2026, 4, 19))  # 18000
    monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda: 11000.0)
    monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda: 9000.0)
    tmp_tracker.take_snapshot(date(2026, 4, 20))  # 20000 (peak)
    monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda: 9500.0)
    monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda: 8500.0)
    result = tmp_tracker.take_snapshot(date(2026, 4, 21))  # 18000 (drop -10%)
    row = result["row"]
    assert row["peak_equity_usd"] == 20000.0
    assert abs(row["drawdown_pct"] - (-10.0)) < 0.01


def test_no_double_snapshot_same_day(tmp_tracker):
    tmp_tracker.take_snapshot(date(2026, 4, 19))
    result = tmp_tracker.take_snapshot(date(2026, 4, 19))
    assert result.get("skipped") is True


def test_force_overrides_same_day(tmp_tracker, monkeypatch):
    tmp_tracker.take_snapshot(date(2026, 4, 19))
    monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda: 12000.0)
    monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda: 8000.0)
    result = tmp_tracker.take_snapshot(date(2026, 4, 19), force=True)
    assert result["row"]["total_equity_usd"] == 20000.0
    # CSV has 1 row (not 2)
    with open(tmp_tracker.CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1


def test_fail_closed_on_zero_equity(tmp_tracker, monkeypatch):
    monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda: 0.0)
    monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda: 0.0)
    result = tmp_tracker.take_snapshot(date(2026, 4, 19))
    assert result.get("error") == "no_equity"
    assert not tmp_tracker.CSV_PATH.exists()


def test_summary_computes_cagr_sharpe(tmp_tracker, monkeypatch):
    # 10 days with realistic daily-return variance (mean ~+0.6%, std ~1%)
    returns = [0.015, -0.005, 0.010, 0.020, -0.008, 0.012, 0.005, 0.008, -0.004, 0.010]
    eq = 18000.0
    for i, r in enumerate(returns):
        if i > 0:
            eq *= 1 + r
        ibkr_share = eq * 10000 / 18000
        bnb_share = eq - ibkr_share
        monkeypatch.setattr(tmp_tracker, "_fetch_ibkr_live_equity", lambda v=ibkr_share: v)
        monkeypatch.setattr(tmp_tracker, "_fetch_binance_live_equity", lambda v=bnb_share: v)
        tmp_tracker.take_snapshot(date(2026, 4, 19 + i))

    summary = json.loads(tmp_tracker.SUMMARY_PATH.read_text(encoding="utf-8"))
    assert summary["n_days"] == 10
    assert summary["cum_return_pct"] > 4.0
    assert summary["sharpe_annual"] > 1.0  # positive mean with realistic variance
    assert summary["max_dd_pct"] <= 0  # max_dd is non-positive (0 or negative)
