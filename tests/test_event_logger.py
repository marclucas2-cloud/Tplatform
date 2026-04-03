"""Tests for EventLogger (R5-01)."""

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from core.worker.event_logger import EventLogger


@pytest.fixture
def tmp_events_dir(tmp_path):
    return str(tmp_path / "events")


class TestEventLoggerBasic:
    def test_creates_directory(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        assert Path(tmp_events_dir).exists()
        el.close()

    def test_log_creates_file(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log("crypto", "CYCLE_START", {"test": True})
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        assert len(files) == 1
        assert date.today().isoformat() in files[0].name

    def test_log_writes_valid_jsonl(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log("crypto", "CYCLE_START", {"test": True})
        el.log("fx", "SIGNAL", {"symbol": "EURUSD"})
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 2

        event1 = json.loads(lines[0])
        assert event1["cycle"] == "crypto"
        assert event1["type"] == "CYCLE_START"
        assert event1["data"]["test"] is True
        assert "ts" in event1

        event2 = json.loads(lines[1])
        assert event2["cycle"] == "fx"
        assert event2["type"] == "SIGNAL"

    def test_event_count(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        assert el.event_count == 0
        el.log("test", "TEST", {})
        el.log("test", "TEST", {})
        assert el.event_count == 2
        el.close()


class TestEventLoggerConvenience:
    def test_log_cycle_start(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_cycle_start("crypto", {"positions": {}, "regime": "RISK_ON"})
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["type"] == "CYCLE_START"
        assert event["snapshot"]["regime"] == "RISK_ON"

    def test_log_cycle_end(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_cycle_end(
            "crypto",
            output={"signals": ["BUY_BTC"]},
            duration_ms=123.45,
            success=True,
        )
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["type"] == "CYCLE_END"
        assert event["data"]["duration_ms"] == 123.45
        assert event["data"]["success"] is True

    def test_log_cycle_end_with_error(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_cycle_end(
            "crypto",
            output={},
            duration_ms=50.0,
            success=False,
            error="Binance timeout",
        )
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["data"]["error"] == "Binance timeout"

    def test_log_signal(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_signal("crypto", {
            "symbol": "BTCUSDC",
            "side": "BUY",
            "size": 0.1,
        })
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["type"] == "SIGNAL"
        assert event["data"]["symbol"] == "BTCUSDC"

    def test_log_order(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_order("crypto", {
            "order_id": "ORD-1",
            "state": "SUBMITTED",
        })
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["type"] == "ORDER"

    def test_log_error(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        el.log_error("crypto", "Binance API error", context={"status": 429})
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["type"] == "ERROR"
        assert event["data"]["error"] == "Binance API error"
        assert event["data"]["status"] == 429


class TestEventLoggerSnapshot:
    def test_snapshot_included(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        snapshot = {
            "positions": {"BTCUSDC": {"qty": 0.1}},
            "prices": {"BTCUSDC": 45000},
            "regime": "RISK_ON",
            "kelly_mode": "FULL",
        }
        el.log("crypto", "CYCLE_START", {}, input_snapshot=snapshot)
        el.close()

        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        event = json.loads(files[0].read_text().strip())
        assert event["snapshot"]["regime"] == "RISK_ON"
        assert event["snapshot"]["positions"]["BTCUSDC"]["qty"] == 0.1


class TestEventLoggerPurge:
    def test_purge_old_files(self, tmp_events_dir):
        base = Path(tmp_events_dir)
        base.mkdir(parents=True, exist_ok=True)

        # Create fake old file
        old_file = base / "events_2020-01-01.jsonl"
        old_file.write_text('{"test": true}\n')

        # Create recent file
        today_file = base / f"events_{date.today().isoformat()}.jsonl"
        today_file.write_text('{"test": true}\n')

        el = EventLogger(base_dir=tmp_events_dir)
        deleted = el.purge_old(retention_days=30)
        el.close()

        assert deleted == 1
        assert not old_file.exists()
        assert today_file.exists()


class TestEventLoggerCurrentFile:
    def test_current_file(self, tmp_events_dir):
        el = EventLogger(base_dir=tmp_events_dir)
        assert el.current_file is None
        el.log("test", "TEST", {})
        assert el.current_file is not None
        el.close()
        assert el.current_file is None


class TestEventLoggerThreadSafety:
    def test_concurrent_writes(self, tmp_events_dir):
        """Multiple threads writing events concurrently."""
        import threading

        el = EventLogger(base_dir=tmp_events_dir)
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    el.log(f"thread_{thread_id}", "TEST", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        el.close()

        assert len(errors) == 0
        assert el.event_count == 250

        # Verify all lines are valid JSON
        files = list(Path(tmp_events_dir).glob("events_*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 250
        for line in lines:
            json.loads(line)  # Should not raise
