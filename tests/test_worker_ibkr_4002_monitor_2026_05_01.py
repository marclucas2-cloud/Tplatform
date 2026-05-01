"""Regression tests for persistent IBKR 4002 flap monitoring."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


def _load_ibkr_4002_helpers():
    src = (ROOT_PATH / "worker.py").read_text(encoding="utf-8")
    start = src.index("_IBKR_4002_STATE_PATH")
    end = src.index("\n\n@lru_cache", start)
    helper_src = src[start:end]
    stub = """
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
logger = logging.getLogger('test')
ALERTS = []
def _send_alert(message, level='info'):
    ALERTS.append((level, message))
"""
    return stub, helper_src


def test_ibkr_4002_monitor_alerts_when_live_positions_exist(tmp_path: Path):
    stub, helper = _load_ibkr_4002_helpers()
    stub += f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
    ns: dict = {}
    exec(stub + helper, ns)

    live_state = tmp_path / "data" / "state" / "futures_positions_live.json"
    live_state.parent.mkdir(parents=True, exist_ok=True)
    live_state.write_text(json.dumps({"MCL": {"qty": 1}}), encoding="utf-8")

    ns["_note_ibkr_4002_connectivity_issue"]("bracket_watchdog_connect", "timeout")

    state_path = tmp_path / "data" / "state" / "ibkr_4002_monitor.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["active"] is True
    assert payload["estimated_live_positions"] == 1
    assert payload["count"] == 1
    assert ns["ALERTS"]
    assert ns["ALERTS"][0][0] == "critical"


def test_ibkr_4002_monitor_clears_and_emits_recovery(tmp_path: Path):
    stub, helper = _load_ibkr_4002_helpers()
    stub += f"\nROOT = Path(r'{tmp_path.as_posix()}')\n"
    ns: dict = {}
    exec(stub + helper, ns)

    live_state = tmp_path / "data" / "state" / "futures_positions_live.json"
    live_state.parent.mkdir(parents=True, exist_ok=True)
    live_state.write_text(json.dumps({"MCL": {"qty": 1}}), encoding="utf-8")

    ns["_note_ibkr_4002_connectivity_issue"]("bracket_watchdog_connect", "timeout")
    ns["_clear_ibkr_4002_connectivity_issue"]("live_risk_snapshot")

    state_path = tmp_path / "data" / "state" / "ibkr_4002_monitor.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["active"] is False
    assert payload["resolved_by"] == "live_risk_snapshot"
    assert any("connectivity restored" in msg.lower() for _, msg in ns["ALERTS"])
