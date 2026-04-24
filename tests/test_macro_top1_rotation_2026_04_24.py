"""Tests unitaires + wiring pour macro_top1_rotation paper.

Couvre:
  1. Strategy: decide() returns correct action per state+prices
  2. Runner: state file read/write, journal append, freshness check
  3. Wiring: registry + whitelist + worker.py imports
  4. Pattern: simulation locale pure (pas d ordre broker)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Strategy decide()
# ============================================================================

class TestMacroTop1RotationStrategy:
    def _get(self):
        from strategies_v2.us.macro_top1_rotation import MacroTop1Rotation
        return MacroTop1Rotation()

    def _prices(self, periods: int = 90) -> pd.DataFrame:
        """Synthetic prices with QQQ trending up strongest."""
        idx = pd.bdate_range("2024-01-01", periods=periods)
        syms = ["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]
        data = {}
        rng = np.random.default_rng(42)
        for s in syms:
            base = 100.0
            mult = 1.002 if s == "QQQ" else 1.0005  # QQQ strongest
            path = base * np.cumprod(1 + mult - 1 + rng.normal(0, 0.005, periods))
            data[s] = path
        return pd.DataFrame(data, index=idx)

    def test_default_params(self):
        s = self._get()
        assert s.lookback_days == 60
        assert s.hold_days == 21
        assert s.UNIVERSE == ["SPY", "TLT", "GLD", "DBC", "UUP", "IEF", "HYG", "QQQ"]
        assert s.name == "macro_top1_rotation"
        assert s.broker == "alpaca"

    def test_should_rebalance_no_prior(self):
        s = self._get()
        assert s.should_rebalance(pd.Timestamp("2024-02-01"), None) is True

    def test_should_rebalance_within_hold(self):
        s = self._get()
        last = pd.Timestamp("2024-02-01")
        # 10 busdays after = within hold (21)
        now = last + pd.offsets.BDay(10)
        assert s.should_rebalance(now, last) is False

    def test_should_rebalance_past_hold(self):
        s = self._get()
        last = pd.Timestamp("2024-02-01")
        now = last + pd.offsets.BDay(25)
        assert s.should_rebalance(now, last) is True

    def test_decide_hold_when_within_window(self):
        s = self._get()
        prices = self._prices(70)
        last = prices.index[-5]  # 5 days ago
        now = prices.index[-1]
        d = s.decide(prices, now, last, "SPY")
        assert d.action == "hold"
        assert d.target_symbol == "SPY"
        assert d.rebalance_due is False

    def test_decide_rebalance_picks_top1(self):
        s = self._get()
        prices = self._prices(70)
        now = prices.index[-1]
        d = s.decide(prices, now, None, None)
        assert d.action == "rebalance"
        # QQQ was synthetic strongest
        assert d.target_symbol == "QQQ"
        assert len(d.top3) == 3
        assert d.rebalance_due is True

    def test_decide_no_signal_missing_symbols(self):
        s = self._get()
        partial = self._prices(70)[["SPY", "QQQ"]]  # missing others
        d = s.decide(partial, partial.index[-1], None, None)
        assert d.action == "no_signal"
        assert "missing_symbols" in d.reason

    def test_decide_no_signal_insufficient_history(self):
        s = self._get()
        short = self._prices(30)  # < lookback=60
        d = s.decide(short, short.index[-1], None, None)
        assert d.action == "no_signal"
        assert "insufficient_history" in d.reason


# ============================================================================
# Runner — journal + state
# ============================================================================

class TestRunnerStateAndJournal:
    def test_state_roundtrip_empty(self, tmp_path, monkeypatch):
        from core.worker.cycles import macro_top1_rotation_runner as mod
        monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
        monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(mod, "JOURNAL_FILE", tmp_path / "journal.jsonl")

        state = mod._load_state()
        assert state["current_symbol"] is None
        assert state["rebal_count"] == 0

    def test_state_save_load(self, tmp_path, monkeypatch):
        from core.worker.cycles import macro_top1_rotation_runner as mod
        monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
        monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")

        mod._save_state({
            "last_rebal_date": "2026-04-24",
            "current_symbol": "QQQ",
            "last_cycle_utc": "2026-04-24T14:30:00+00:00",
            "rebal_count": 1,
        })
        loaded = mod._load_state()
        assert loaded["current_symbol"] == "QQQ"
        assert loaded["rebal_count"] == 1

    def test_journal_append(self, tmp_path, monkeypatch):
        from core.worker.cycles import macro_top1_rotation_runner as mod
        monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
        monkeypatch.setattr(mod, "JOURNAL_FILE", tmp_path / "journal.jsonl")

        mod._append_journal({"event": "signal_emit", "target": "QQQ"})
        mod._append_journal({"event": "hold"})

        lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "signal_emit"
        assert json.loads(lines[1])["event"] == "hold"


# ============================================================================
# Wiring — registry + whitelist + worker
# ============================================================================

class TestWiring:
    def test_quant_registry(self):
        reg = yaml.safe_load((ROOT / "config" / "quant_registry.yaml").read_text(encoding="utf-8"))
        entry = next(
            (s for s in reg["strategies"] if s["strategy_id"] == "macro_top1_rotation"),
            None,
        )
        assert entry is not None, "macro_top1_rotation missing from quant_registry"
        assert entry["book"] == "alpaca_us"
        assert entry["status"] == "paper_only"
        assert entry["is_live"] is False
        assert entry["grade"] == "B"

    def test_live_whitelist(self):
        wl = yaml.safe_load((ROOT / "config" / "live_whitelist.yaml").read_text(encoding="utf-8"))
        entries = wl.get("alpaca_us", [])
        ids = [e["strategy_id"] for e in entries]
        assert "macro_top1_rotation" in ids
        entry = next(e for e in entries if e["strategy_id"] == "macro_top1_rotation")
        assert entry["status"] == "paper_only"
        assert "run_macro_top1_rotation_cycle" in entry["runtime_entrypoint"]
        assert entry["lookback_days"] == 60
        assert entry["hold_days"] == 21

    def test_wf_manifest_exists(self):
        m = ROOT / "data" / "research" / "wf_manifests" / "macro_top1_rotation_2026-04-24.json"
        assert m.exists()
        data = json.loads(m.read_text(encoding="utf-8"))
        assert data["strategy_id"] == "macro_top1_rotation"
        assert data["summary"]["verdict"] == "VALIDATED"

    def test_worker_imports_runner(self):
        src = (ROOT / "worker.py").read_text(encoding="utf-8")
        assert "from core.worker.cycles.macro_top1_rotation_runner import" in src
        assert "run_macro_top1_rotation_cycle" in src

    def test_worker_has_cycle_runner_and_schedule(self):
        src = (ROOT / "worker.py").read_text(encoding="utf-8")
        assert '"macro_top1_rotation": CycleRunner' in src
        # Schedule at 16h30 Paris
        assert re.search(
            r'now_paris\.hour\s*==\s*16.*now_paris\.minute\s*>=\s*30.*macro_top1_rotation',
            src, re.DOTALL,
        ), "Schedule pattern 16h30 Paris for macro_top1_rotation not found"

    def test_no_broker_order_call_in_runner(self):
        """Runner must be pure local simulation — NO alpaca/broker order calls."""
        src = (ROOT / "core" / "worker" / "cycles" / "macro_top1_rotation_runner.py").read_text(encoding="utf-8")
        # No placeOrder, no alpaca submit_order, no broker client
        forbidden = ["placeOrder", "submit_order", "AlpacaClient", "ibkr_bracket", "binance"]
        for term in forbidden:
            assert term not in src, f"Forbidden broker call {term!r} in runner (must be pure sim)"
