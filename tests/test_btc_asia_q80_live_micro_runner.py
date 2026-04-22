"""Tests core/runtime/btc_asia_q80_live_micro_runner.py (Phase 2 2026-04-22).

Tests unitaires des helpers: state I/O, kill flag, extract_fill_details.
Les fonctions run_entry/run_exit utilisent BinanceBroker + telegram reels, donc
pas de test de bout en bout ici (a faire en integration sur VPS). Le but est
d'attraper les regressions sur la logique locale sans broker.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _reload_runner(tmp_path, monkeypatch):
    """Reload the runner with STATE_DIR pointing to tmp_path."""
    import core.runtime.btc_asia_q80_live_micro_runner as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(mod, "JOURNAL_PATH", tmp_path / "journal.jsonl")
    monkeypatch.setattr(mod, "KILL_FLAG_PATH", tmp_path / "_kill_switch.json")
    monkeypatch.setattr(mod, "LAST_CYCLE_PATH", tmp_path / "_last_cycle.json")
    return mod


class TestStateIO:
    def test_empty_positions_if_no_file(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        assert mod._load_positions() == {}

    def test_save_and_load_positions(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        positions = {
            "q80_lm_20260423_abc123": {
                "pos_id": "q80_lm_20260423_abc123",
                "symbol": "BTCUSDC",
                "qty": 0.002,
                "entry_price": 100000.0,
                "entry_time_utc": "2026-04-23T08:30:00+00:00",
                "entry_cost_usd": 0.2,
            },
        }
        mod._save_positions(positions)
        loaded = mod._load_positions()
        assert loaded == positions

    def test_corrupted_positions_returns_empty(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        mod.POSITIONS_PATH.write_text("not valid json {", encoding="utf-8")
        assert mod._load_positions() == {}


class TestKillSwitch:
    def test_no_kill_flag_returns_false(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        on, data = mod._is_kill_switch_on()
        assert on is False
        assert data is None

    def test_kill_flag_set_returns_true(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        mod._trigger_kill_switch("test_reason", {"foo": "bar"})
        on, data = mod._is_kill_switch_on()
        assert on is True
        assert data["reason"] == "test_reason"
        assert data["detail"]["foo"] == "bar"
        assert "triggered_at_utc" in data
        assert "instruction" in data


class TestExtractFillDetails:
    def test_successful_fill(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        result = {
            "executedQty": "0.002",
            "fills": [
                {"price": "100000.0", "qty": "0.001"},
                {"price": "100010.0", "qty": "0.001"},
            ],
        }
        qty, avg = mod._extract_fill_details(result)
        assert qty == 0.002
        assert avg == pytest.approx(100005.0)

    def test_zero_fill_returns_zero(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        qty, avg = mod._extract_fill_details({"executedQty": "0", "fills": []})
        assert qty == 0.0
        assert avg == 0.0


class TestJournalEvent:
    def test_journal_append(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        mod._journal_event({"event": "entry", "pos_id": "x"})
        mod._journal_event({"event": "exit", "pos_id": "x"})

        lines = mod.JOURNAL_PATH.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        e0 = json.loads(lines[0])
        e1 = json.loads(lines[1])
        assert e0["event"] == "entry"
        assert e1["event"] == "exit"
        # Metadata auto-added
        assert "ts_utc" in e0
        assert e0["strategy_id"] == "btc_asia_mes_leadlag_q80_v80_long_only"


class TestRunEntrySkipSignalPath:
    """Validate that run_entry_if_needed refuses to trade on non-BUY signals
    without needing a real broker."""

    def test_sell_signal_skipped(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        result = mod.run_entry_if_needed(
            broker=SimpleNamespace(),
            signal_side="SELL",
            signal_details={"target_date": "2026-04-23"},
            live_start_at_iso="2026-04-23",
        )
        assert result is False

    def test_none_signal_skipped(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        result = mod.run_entry_if_needed(
            broker=SimpleNamespace(),
            signal_side="NONE",
            signal_details={"target_date": "2026-04-23"},
            live_start_at_iso="2026-04-23",
        )
        assert result is False

    def test_kill_switch_active_skips_entry(self, tmp_path, monkeypatch):
        mod = _reload_runner(tmp_path, monkeypatch)
        mod._trigger_kill_switch("test_setup", {})
        result = mod.run_entry_if_needed(
            broker=SimpleNamespace(),
            signal_side="BUY",
            signal_details={"target_date": "2026-04-23"},
            live_start_at_iso="2026-04-23",
        )
        assert result is False
