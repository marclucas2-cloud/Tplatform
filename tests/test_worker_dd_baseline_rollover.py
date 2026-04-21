"""Tests _ensure_live_dd_baseline() de worker.py.

Regression bug 2026-04-21: live_risk_dd_state.json date='2026-04-20'
n'etait pas rolled-over au boot si run_live_risk_cycle scheduler
tardait. Fix: helper extrait + appele aussi au boot.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


def _load_helper():
    """Extract _ensure_live_dd_baseline from worker.py without full import
    (avoid side effects of worker.main()).
    """
    src = (ROOT_PATH / "worker.py").read_text(encoding="utf-8")
    start = src.index("def _ensure_live_dd_baseline(current_equity")
    end = src.index("\n\n@lru_cache", start)
    helper_src = src[start:end]
    stub = """
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
UTC = timezone.utc
logger = logging.getLogger('test')
"""
    return stub, helper_src


class TestEnsureLiveDdBaseline:

    def _run(self, tmp_path, current_equity, initial_file_content=None):
        """Helper: exec the function with ROOT=tmp_path."""
        stub, helper = _load_helper()
        stub = stub + f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
        ns: dict = {}
        exec(stub + helper, ns)
        if initial_file_content is not None:
            dd_path = tmp_path / "data" / "live_risk_dd_state.json"
            dd_path.parent.mkdir(parents=True, exist_ok=True)
            dd_path.write_text(json.dumps(initial_file_content))
        return ns["_ensure_live_dd_baseline"](current_equity), tmp_path

    def test_first_call_creates_baseline(self, tmp_path):
        result, _ = self._run(tmp_path, 11275.73)
        assert result == 11275.73
        dd = json.loads((tmp_path / "data" / "live_risk_dd_state.json").read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert dd["date"] == today
        assert dd["daily_start_equity"] == 11275.73

    def test_rollover_when_date_is_yesterday(self, tmp_path):
        """Regression bug: date=2026-04-20 au lieu de today -> rollover."""
        old_date = "2026-04-20"
        result, _ = self._run(tmp_path, 11275.73, {
            "daily_start_equity": 11276.83, "date": old_date,
        })
        # New baseline = current_equity, not old one
        assert result == 11275.73
        dd = json.loads((tmp_path / "data" / "live_risk_dd_state.json").read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert dd["date"] == today
        assert dd["date"] != old_date

    def test_same_day_no_rollover(self, tmp_path):
        """Si date == today, on renvoie baseline existante (no-op)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result, _ = self._run(tmp_path, 11500.00, {
            "daily_start_equity": 11276.83, "date": today,
        })
        # Keep original baseline, ignore current_equity
        assert result == 11276.83
        dd = json.loads((tmp_path / "data" / "live_risk_dd_state.json").read_text())
        assert dd["daily_start_equity"] == 11276.83

    def test_corrupted_json_gives_rollover(self, tmp_path):
        """Si fichier JSON invalide -> fallback current_equity."""
        dd_path = tmp_path / "data" / "live_risk_dd_state.json"
        dd_path.parent.mkdir(parents=True, exist_ok=True)
        dd_path.write_text("not json {")
        stub, helper = _load_helper()
        stub += f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
        ns: dict = {}
        exec(stub + helper, ns)
        result = ns["_ensure_live_dd_baseline"](11275.73)
        # Fallback to current_equity (no crash)
        assert result == 11275.73

    def test_zero_equity_fallback(self, tmp_path):
        """Si saved_eq == 0, on roll over (0 invalide)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result, _ = self._run(tmp_path, 11275.73, {
            "daily_start_equity": 0, "date": today,
        })
        assert result == 11275.73
