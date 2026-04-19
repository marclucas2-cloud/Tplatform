"""Tests for F2 log_incident_auto JSONL timeline (plan 9.0 2026-04-19)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import core.monitoring.incident_report as ir
from core.monitoring.incident_report import log_incident_auto


@pytest.fixture
def tmp_incidents(tmp_path, monkeypatch):
    monkeypatch.setattr(ir, "INCIDENTS_JSONL_DIR", tmp_path)
    return tmp_path


class TestLogIncidentAuto:
    def test_basic_append(self, tmp_incidents):
        path = log_incident_auto(
            category="reconciliation",
            severity="critical",
            source="reconciliation_cycle",
            message="RECONCILIATION CRITICAL [binance] only_in_broker",
            context={"symbols": ["BTCUSDC"]},
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert path == tmp_incidents / f"{today}.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["category"] == "reconciliation"
        assert entry["severity"] == "critical"
        assert entry["message"].startswith("RECONCILIATION")
        assert entry["context"]["symbols"] == ["BTCUSDC"]

    def test_appends_to_same_day_file(self, tmp_incidents):
        log_incident_auto(category="c", severity="warning",
                          source="s", message="m1")
        log_incident_auto(category="c", severity="critical",
                          source="s", message="m2")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = (tmp_incidents / f"{today}.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["message"] == "m1"
        assert json.loads(lines[1])["message"] == "m2"

    def test_context_optional(self, tmp_incidents):
        log_incident_auto(
            category="kill_switch", severity="critical",
            source="risk_manager_crypto", message="DD trigger",
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = json.loads(
            (tmp_incidents / f"{today}.jsonl").read_text(encoding="utf-8").strip()
        )
        assert entry["context"] == {}

    def test_message_truncated_to_2000(self, tmp_incidents):
        long_msg = "x" * 5000
        log_incident_auto(
            category="c", severity="warning", source="s", message=long_msg
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = json.loads(
            (tmp_incidents / f"{today}.jsonl").read_text(encoding="utf-8").strip()
        )
        assert len(entry["message"]) == 2000

    def test_never_raises_on_write_error(self, tmp_incidents, monkeypatch):
        # Simulate write failure by pointing to an invalid directory
        bad = tmp_incidents / "nope" / "deeper"
        monkeypatch.setattr(ir, "INCIDENTS_JSONL_DIR", bad)  # doesn't exist
        # Should not raise
        path = log_incident_auto(
            category="c", severity="w", source="s", message="test",
        )
        # Returns sentinel path on failure
        assert "FAILED_WRITE" in str(path) or path.exists()
