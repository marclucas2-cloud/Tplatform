"""Tests Phase 3.3 desk productif 2026-04-22: metrics etendus live_pnl_tracker.

Couvre:
  - _count_live_trades_in_window compte correctement les exits
  - _count_live_trades_in_window exclut entry_skipped, entry_rejected, exec_error
  - _count_live_trades_in_window respecte la fenetre temporelle
  - _compute_capital_exposure_snapshot retourne exposed_usd/pct/idle_pct
  - _compute_running_stats ajoute trades_count_30d et capital_exposure
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc
ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


def _reload_tracker(tmp_path, monkeypatch):
    """Import + patch ROOT to tmp for isolation."""
    import scripts.live_pnl_tracker as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    return mod


class TestCountLiveTradesInWindow:

    def test_empty_state_returns_zero(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        result = mod._count_live_trades_in_window(30)
        assert result["count"] == 0
        assert result["by_strategy"] == {}
        assert result["window_days"] == 30

    def test_exit_event_counted(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        journal = tmp_path / "data" / "state" / "btc_asia_q80_live_micro" / "journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        events = [
            {"event": "entry", "ts_utc": (now - timedelta(hours=2)).isoformat(),
             "strategy_id": "btc_asia_q80"},
            {"event": "exit", "ts_utc": (now - timedelta(hours=1)).isoformat(),
             "strategy_id": "btc_asia_q80"},
        ]
        with journal.open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        result = mod._count_live_trades_in_window(30)
        assert result["count"] == 1
        assert result["by_strategy"].get("btc_asia_q80") == 1

    def test_skipped_events_not_counted(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        journal = tmp_path / "data" / "state" / "btc_asia_q80_live_micro" / "journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        events = [
            {"event": "entry_skipped", "ts_utc": now.isoformat(), "strategy_id": "x"},
            {"event": "entry_rejected", "ts_utc": now.isoformat(), "strategy_id": "x"},
            {"event": "exec_error", "ts_utc": now.isoformat(), "strategy_id": "x"},
            {"event": "entry", "ts_utc": now.isoformat(), "strategy_id": "x"},
        ]
        with journal.open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        result = mod._count_live_trades_in_window(30)
        assert result["count"] == 0  # pas d'exit

    def test_old_exit_outside_window_excluded(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        journal = tmp_path / "data" / "state" / "btc_asia_q80_live_micro" / "journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        old_ts = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        event = {"event": "exit", "ts_utc": old_ts, "strategy_id": "x"}
        journal.write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = mod._count_live_trades_in_window(30)
        assert result["count"] == 0

    def test_multiple_strategies_aggregated(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        now = datetime.now(UTC)
        for strat_name, count in [("btc_asia_q80", 3), ("cam", 1)]:
            journal = tmp_path / "data" / "state" / f"{strat_name}_live_micro" / "journal.jsonl"
            journal.parent.mkdir(parents=True, exist_ok=True)
            with journal.open("w", encoding="utf-8") as f:
                for i in range(count):
                    f.write(json.dumps({
                        "event": "exit",
                        "ts_utc": (now - timedelta(hours=i + 1)).isoformat(),
                        "strategy_id": strat_name,
                    }) + "\n")

        result = mod._count_live_trades_in_window(30)
        assert result["count"] == 4
        assert result["by_strategy"]["btc_asia_q80"] == 3
        assert result["by_strategy"]["cam"] == 1


class TestCapitalExposureSnapshot:

    def test_no_positions_returns_zero(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        result = mod._compute_capital_exposure_snapshot(total_equity_usd=10000)
        assert result["exposed_usd"] == 0.0
        assert result["exposed_pct"] == 0.0
        assert result["idle_pct"] == 100.0

    def test_btc_asia_position_exposure(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        positions_path = tmp_path / "data" / "state" / "btc_asia_mes_leadlag_q80_live_micro" / "positions.json"
        positions_path.parent.mkdir(parents=True, exist_ok=True)
        positions_path.write_text(json.dumps({
            "pos1": {
                "pos_id": "pos1",
                "symbol": "BTCUSDC",
                "qty": 0.002,
                "entry_price": 100000.0,
            },
        }), encoding="utf-8")

        result = mod._compute_capital_exposure_snapshot(total_equity_usd=10000)
        # 0.002 * 100000 = $200
        assert result["exposed_usd"] == 200.0
        assert result["exposed_pct"] == 2.0  # 200/10000
        assert result["idle_pct"] == 98.0

    def test_zero_equity_safe(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        result = mod._compute_capital_exposure_snapshot(total_equity_usd=0)
        assert result["exposed_pct"] == 0.0
        # idle_pct doit etre 0 si equity=0 (pas de base pour calcul)
        assert result["idle_pct"] == 0.0


class TestRunningStatsExtended:

    def test_insufficient_rows_still_returns_exposure(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        result = mod._compute_running_stats([{"date": "2026-04-22", "total_equity_usd": 20000}])
        assert result["insufficient"] is True
        assert "trades_count_30d" in result
        assert "capital_exposure" in result

    def test_enough_rows_has_all_new_fields(self, tmp_path, monkeypatch):
        mod = _reload_tracker(tmp_path, monkeypatch)
        rows = [
            {"date": "2026-04-01", "total_equity_usd": 20000, "daily_return_pct": 0.0,
             "peak_equity_usd": 20000, "drawdown_pct": 0.0},
            {"date": "2026-04-22", "total_equity_usd": 20500, "daily_return_pct": 0.5,
             "peak_equity_usd": 20500, "drawdown_pct": 0.0},
        ]
        result = mod._compute_running_stats(rows)
        assert result.get("insufficient") is not True
        assert "max_dd_live_pct" in result
        assert "trades_count_30d" in result
        assert "capital_exposure" in result
        # Backward compat
        assert "max_dd_pct" in result
        assert "sharpe_annual" in result
