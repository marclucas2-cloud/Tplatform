"""IncidentReportGenerator regression tests (Phase 13 XXL)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.monitoring.incident_report import IncidentReportGenerator


@pytest.fixture
def gen(tmp_path):
    events_dir = tmp_path / "events"
    output_dir = tmp_path / "incidents"
    return IncidentReportGenerator(
        events_dir=str(events_dir),
        output_dir=str(output_dir),
    )


def _write_events(events_dir: Path, events: list[dict]):
    """Write events to a JSONL file format used by the loader.

    Loader expects: events_YYYY-MM-DD.jsonl
    """
    events_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = events_dir / f"events_{today}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# Generation basics
# ---------------------------------------------------------------------------

class TestGeneration:
    def test_creates_markdown_file(self, gen, tmp_path):
        path = gen.generate(
            anomaly_message="Daily DD -7%",
            anomaly_level="CRITICAL",
        )
        p = Path(path)
        assert p.exists()
        assert p.suffix == ".md"
        content = p.read_text(encoding="utf-8")
        assert "Incident Report" in content
        assert "CRITICAL" in content
        assert "Daily DD -7%" in content

    def test_includes_no_events_message_when_dir_empty(self, gen):
        path = gen.generate(anomaly_message="x")
        content = Path(path).read_text(encoding="utf-8")
        assert "No events found" in content

    def test_filename_format(self, gen):
        path = Path(gen.generate(anomaly_message="x"))
        assert path.name.startswith("incident_")
        assert path.name.endswith(".md")

    def test_anomaly_level_default_critical(self, gen):
        path = Path(gen.generate(anomaly_message="x"))
        content = path.read_text(encoding="utf-8")
        assert "CRITICAL" in content

    def test_custom_anomaly_level(self, gen):
        path = Path(gen.generate(anomaly_message="warn case", anomaly_level="WARN"))
        content = path.read_text(encoding="utf-8")
        assert "WARN" in content


# ---------------------------------------------------------------------------
# Event timeline rendering
# ---------------------------------------------------------------------------

class TestEventTimeline:
    def test_renders_signal_event(self, gen, tmp_path):
        now = datetime.now()
        _write_events(tmp_path / "events", [
            {"ts": now.isoformat(), "cycle": "crypto", "type": "SIGNAL",
             "data": {"symbol": "BTCUSDT", "side": "BUY"}},
        ])
        content = Path(gen.generate(anomaly_message="x")).read_text(encoding="utf-8")
        assert "SIGNAL" in content
        assert "BTCUSDT" in content
        assert "BUY" in content

    def test_renders_error_event(self, gen, tmp_path):
        now = datetime.now()
        _write_events(tmp_path / "events", [
            {"ts": now.isoformat(), "cycle": "crypto", "type": "ERROR",
             "data": {"error": "broker unreachable"}},
        ])
        content = Path(gen.generate(anomaly_message="x")).read_text(encoding="utf-8")
        assert "ERROR" in content
        assert "broker unreachable" in content

    def test_renders_cycle_end_with_duration(self, gen, tmp_path):
        now = datetime.now()
        _write_events(tmp_path / "events", [
            {"ts": now.isoformat(), "cycle": "futures", "type": "CYCLE_END",
             "data": {"duration_ms": 1500, "success": True}},
        ])
        content = Path(gen.generate(anomaly_message="x")).read_text(encoding="utf-8")
        assert "1500ms" in content


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------

class TestStateSnapshot:
    def test_includes_worker_state_when_provided(self, gen):
        snapshot = {
            "positions": {"BTC": {}, "ETH": {}},
            "regimes": {"crypto": "BULL"},
            "kills": {"crypto": False},
        }
        path = Path(gen.generate(
            anomaly_message="x",
            worker_state_snapshot=snapshot,
        ))
        content = path.read_text(encoding="utf-8")
        assert "Positions" in content
        assert "2" in content  # 2 positions
        assert "BULL" in content

    def test_no_state_section_pollution_when_absent(self, gen):
        path = Path(gen.generate(anomaly_message="x"))
        content = path.read_text(encoding="utf-8")
        # System State section header still present
        assert "System State" in content
