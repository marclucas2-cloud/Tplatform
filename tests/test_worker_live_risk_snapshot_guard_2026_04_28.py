"""Regression tests for live risk snapshot fallback hardening.

Goals:
1. Never treat config capital as if it were live equity when IBKR snapshot fails.
2. Accept only fresh cached equity snapshots for live DD / kill switch checks.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


def _load_snapshot_helper():
    src = (ROOT_PATH / "worker.py").read_text(encoding="utf-8")
    start = src.index("def _load_cached_live_equity_snapshot")
    end = src.index("\n\n@lru_cache", start)
    helper_src = src[start:end]
    stub = """
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
logger = logging.getLogger('test')
"""
    return stub, helper_src


class TestLoadCachedLiveEquitySnapshot:
    def _run(self, tmp_path: Path, payload: dict, *, max_age_minutes: float = 30.0):
        stub, helper = _load_snapshot_helper()
        stub += f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
        ns: dict = {}
        exec(stub + helper, ns)
        state_path = tmp_path / "data" / "state" / "ibkr_futures" / "equity_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        return ns["_load_cached_live_equity_snapshot"](max_age_minutes=max_age_minutes)

    def test_returns_fresh_snapshot_from_updated_at(self, tmp_path: Path):
        snap = self._run(
            tmp_path,
            {
                "equity": 11283.42,
                "cash": 9412.11,
                "updated_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            },
        )
        assert snap is not None
        assert snap["equity"] == 11283.42
        assert snap["cash"] == 9412.11
        assert snap["age_minutes"] < 30

    def test_rejects_stale_snapshot(self, tmp_path: Path):
        snap = self._run(
            tmp_path,
            {
                "equity": 11283.42,
                "cash": 9412.11,
                "updated_at": (datetime.now(UTC) - timedelta(minutes=45)).isoformat(),
            },
        )
        assert snap is None

    def test_uses_file_mtime_when_updated_at_missing(self, tmp_path: Path):
        state_path = tmp_path / "data" / "state" / "ibkr_futures" / "equity_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"equity": 11111.0, "cash": 9000.0}), encoding="utf-8")

        stub, helper = _load_snapshot_helper()
        stub += f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
        ns: dict = {}
        exec(stub + helper, ns)
        snap = ns["_load_cached_live_equity_snapshot"](max_age_minutes=30.0)
        assert snap is not None
        assert snap["equity"] == 11111.0


def test_worker_source_no_config_capital_fallback_for_live_risk():
    src = (ROOT_PATH / "worker.py").read_text(encoding="utf-8")
    assert '"snapshot_source": "cache"' in src
    assert "skipping numeric live risk checks to avoid false kill switch" in src
    assert 'portfolio = {"equity": risk_mgr.capital' not in src
