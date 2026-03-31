"""
Tests for core.data.resync_guard — backfill-to-live resync guard.

Covers:
  1. Backfill-to-live transition: clean, gap, duplicate, price discontinuity
  2. Feed latency measurement: ok, exceeded, drifting
  3. Sequence integrity: monotonic, gaps, duplicates, out-of-order
  4. Drift detection: stable, increasing, reset
  5. Frequency configs and recommendation logic
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core.data.resync_guard import (
    DRIFT_WINDOW_SIZE,
    FREQ_SECONDS,
    ResyncGuard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(
    start: datetime,
    freq_minutes: int,
    n_candles: int,
    base_price: float = 100.0,
) -> pd.DataFrame:
    """Build a tiny OHLCV DataFrame for testing."""
    dates = pd.date_range(start=start, periods=n_candles, freq=f"{freq_minutes}min", tz="UTC")
    close_prices = [base_price + i * 0.1 for i in range(n_candles)]
    return pd.DataFrame(
        {
            "open": [p - 0.05 for p in close_prices],
            "high": [p + 0.5 for p in close_prices],
            "low": [p - 0.5 for p in close_prices],
            "close": close_prices,
            "volume": [1000] * n_candles,
        },
        index=dates,
    )


def _ts(minutes_offset: int = 0, base: datetime = None) -> datetime:
    """UTC datetime shifted by *minutes_offset* from *base*."""
    if base is None:
        base = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes_offset)


@pytest.fixture
def tmp_log_dir(tmp_path):
    """Return a temporary directory for resync logs."""
    return str(tmp_path)


@pytest.fixture
def guard(tmp_log_dir):
    return ResyncGuard(log_dir=tmp_log_dir)


# ---------------------------------------------------------------------------
# 1. Backfill-to-live transition
# ---------------------------------------------------------------------------

class TestBackfillToLive:

    def test_clean_transition(self, guard):
        """Live candle starts exactly one period after the last backfill candle."""
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=10)
        last_ts = bf.index[-1]
        live = {
            "timestamp": last_ts + timedelta(minutes=5),
            "open": float(bf["close"].iloc[-1]),  # no price disc
        }
        res = guard.check_backfill_to_live(bf, live, "5m")
        assert res["ok"] is True
        assert res["has_duplicate"] is False
        assert res["has_gap"] is False
        assert res["has_price_disc"] is False
        assert res["gap_seconds"] == 300.0

    def test_duplicate_detected(self, guard):
        """Live candle has the same timestamp as the last backfill candle."""
        bf = _make_ohlcv(_ts(0), freq_minutes=15, n_candles=5)
        last_ts = bf.index[-1]
        live = {"timestamp": last_ts, "open": float(bf["close"].iloc[-1])}
        res = guard.check_backfill_to_live(bf, live, "15m")
        assert res["ok"] is False
        assert res["has_duplicate"] is True
        assert res["gap_seconds"] == 0.0

    def test_gap_detected(self, guard):
        """Live candle is far later than expected (> 2x tolerance)."""
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=5)
        last_ts = bf.index[-1]
        # 20 min gap for a 5-min candle -> exceeds 2x = 10 min
        live = {"timestamp": last_ts + timedelta(minutes=20), "open": float(bf["close"].iloc[-1])}
        res = guard.check_backfill_to_live(bf, live, "5m")
        assert res["ok"] is False
        assert res["has_gap"] is True
        assert res["gap_seconds"] == 1200.0

    def test_no_gap_within_tolerance(self, guard):
        """Gap inside the 2x tolerance is still ok."""
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=5)
        last_ts = bf.index[-1]
        # 9 min gap for 5-min -> within 2x = 10 min
        live = {"timestamp": last_ts + timedelta(minutes=9), "open": float(bf["close"].iloc[-1])}
        res = guard.check_backfill_to_live(bf, live, "5m")
        assert res["ok"] is True
        assert res["has_gap"] is False

    def test_price_discontinuity(self, guard):
        """Live open is far from backfill close."""
        bf = _make_ohlcv(_ts(0), freq_minutes=15, n_candles=5, base_price=100.0)
        last_ts = bf.index[-1]
        last_close = float(bf["close"].iloc[-1])
        live = {
            "timestamp": last_ts + timedelta(minutes=15),
            "open": last_close * 1.10,  # 10 % jump
        }
        res = guard.check_backfill_to_live(bf, live, "15m")
        assert res["ok"] is False
        assert res["has_price_disc"] is True

    def test_no_price_disc_within_threshold(self, guard):
        """Small price move is tolerated."""
        bf = _make_ohlcv(_ts(0), freq_minutes=15, n_candles=5, base_price=100.0)
        last_ts = bf.index[-1]
        last_close = float(bf["close"].iloc[-1])
        live = {
            "timestamp": last_ts + timedelta(minutes=15),
            "open": last_close * 1.02,  # 2 % -> below 5 % default
        }
        res = guard.check_backfill_to_live(bf, live, "15m")
        assert res["has_price_disc"] is False

    def test_empty_backfill(self, guard):
        bf = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        bf.index = pd.DatetimeIndex([], tz="UTC")
        live = {"timestamp": _ts(0), "open": 100.0}
        res = guard.check_backfill_to_live(bf, live, "5m")
        assert res["ok"] is False
        assert "empty" in res["detail"]

    def test_missing_live_timestamp(self, guard):
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=3)
        res = guard.check_backfill_to_live(bf, {"open": 100.0}, "5m")
        assert res["ok"] is False
        assert "no valid timestamp" in res["detail"]

    def test_negative_gap(self, guard):
        """Live timestamp is before the last backfill candle."""
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=5)
        last_ts = bf.index[-1]
        live = {"timestamp": last_ts - timedelta(minutes=5), "open": 100.0}
        res = guard.check_backfill_to_live(bf, live, "5m")
        assert res["ok"] is False
        assert res["has_gap"] is True
        assert res["gap_seconds"] < 0


# ---------------------------------------------------------------------------
# 2. Feed latency measurement
# ---------------------------------------------------------------------------

class TestFeedLatency:

    def test_ok_latency(self, guard):
        server = _ts(0)
        local = server + timedelta(milliseconds=50)
        res = guard.check_feed_latency(server, local)
        assert res["ok"] is True
        assert res["drifting"] is False
        assert abs(res["latency_ms"] - 50.0) < 1.0

    def test_exceeded_latency(self, guard):
        server = _ts(0)
        local = server + timedelta(milliseconds=800)
        res = guard.check_feed_latency(server, local)
        assert res["ok"] is False
        assert res["latency_ms"] > 500

    def test_negative_latency(self, guard):
        """Local clock is behind server (NTP issue)."""
        server = _ts(0)
        local = server - timedelta(milliseconds=600)
        res = guard.check_feed_latency(server, local)
        assert res["ok"] is False
        assert res["latency_ms"] < 0


# ---------------------------------------------------------------------------
# 3. Sequence integrity
# ---------------------------------------------------------------------------

class TestSequenceIntegrity:

    def test_monotonic_sequence(self, guard):
        timestamps = [_ts(i * 5) for i in range(10)]  # every 5 min
        res = guard.check_sequence_integrity(timestamps, "5m")
        assert res["ok"] is True
        assert res["gaps"] == []
        assert res["duplicates"] == []
        assert res["out_of_order"] == []

    def test_gap_in_sequence(self, guard):
        base = [_ts(i * 5) for i in range(5)]
        # Insert a 20-min gap after the 5th candle
        gap_start = base[-1] + timedelta(minutes=20)
        tail = [gap_start + timedelta(minutes=5 * i) for i in range(3)]
        timestamps = base + tail
        res = guard.check_sequence_integrity(timestamps, "5m")
        assert res["ok"] is False
        assert len(res["gaps"]) == 1
        assert res["gaps"][0]["delta_s"] == 1200.0

    def test_duplicate_in_sequence(self, guard):
        timestamps = [_ts(i * 5) for i in range(5)]
        timestamps.insert(3, timestamps[2])  # duplicate the 3rd
        res = guard.check_sequence_integrity(timestamps, "5m")
        assert res["ok"] is False
        assert len(res["duplicates"]) == 1

    def test_out_of_order(self, guard):
        timestamps = [_ts(i * 5) for i in range(5)]
        # Swap positions 2 and 3
        timestamps[2], timestamps[3] = timestamps[3], timestamps[2]
        res = guard.check_sequence_integrity(timestamps, "5m")
        assert res["ok"] is False
        assert len(res["out_of_order"]) == 1

    def test_single_element(self, guard):
        res = guard.check_sequence_integrity([_ts(0)], "5m")
        assert res["ok"] is True

    def test_empty_list(self, guard):
        res = guard.check_sequence_integrity([], "5m")
        assert res["ok"] is True


# ---------------------------------------------------------------------------
# 4. Drift detection
# ---------------------------------------------------------------------------

class TestDriftDetection:

    def test_stable_drift(self, guard):
        """Constant latency should NOT be flagged as drifting."""
        server_base = _ts(0)
        for i in range(DRIFT_WINDOW_SIZE + 5):
            server = server_base + timedelta(seconds=i)
            local = server + timedelta(milliseconds=100)  # constant 100ms
            res = guard.check_feed_latency(server, local)
        assert res["drifting"] is False

    def test_increasing_drift(self, guard):
        """Monotonically increasing absolute drift triggers DRIFTING."""
        server_base = _ts(0)
        for i in range(DRIFT_WINDOW_SIZE):
            server = server_base + timedelta(seconds=i)
            # Drift grows: 10ms, 20ms, 30ms, ...
            local = server + timedelta(milliseconds=10 * (i + 1))
            guard.check_feed_latency(server, local)
        # One more sample to fill the window and trigger detection
        server = server_base + timedelta(seconds=DRIFT_WINDOW_SIZE)
        local = server + timedelta(milliseconds=10 * (DRIFT_WINDOW_SIZE + 1))
        res = guard.check_feed_latency(server, local)
        # The window should now show monotonically increasing abs drift
        assert res["drifting"] is True

    def test_drift_reset(self, guard):
        """Reset clears the window, so drifting goes away."""
        server_base = _ts(0)
        # Fill window with increasing drift
        for i in range(DRIFT_WINDOW_SIZE):
            server = server_base + timedelta(seconds=i)
            local = server + timedelta(milliseconds=10 * (i + 1))
            guard.check_feed_latency(server, local)

        guard.reset_drift()

        # After reset, single sample cannot trigger drift
        res = guard.check_feed_latency(_ts(100), _ts(100) + timedelta(milliseconds=50))
        assert res["drifting"] is False


# ---------------------------------------------------------------------------
# 5. Frequency configs
# ---------------------------------------------------------------------------

class TestFrequencyConfigs:

    def test_known_frequencies(self):
        assert FREQ_SECONDS["5m"] == 300
        assert FREQ_SECONDS["15m"] == 900
        assert FREQ_SECONDS["1h"] == 3600
        assert FREQ_SECONDS["1d"] == 86400

    def test_custom_max_gap(self, tmp_log_dir):
        """Custom max_gap_seconds overrides defaults."""
        g = ResyncGuard(max_gap_seconds={"5m": 600}, log_dir=tmp_log_dir)
        # Internal freq dict should have the override
        assert g._freq_seconds["5m"] == 600
        # Others remain unchanged
        assert g._freq_seconds["1h"] == 3600

    def test_unknown_freq_fallback(self, guard):
        """Unknown frequency falls back to 900s (logged warning)."""
        assert guard._expected_gap("weird") == 900.0


# ---------------------------------------------------------------------------
# 6. Recommendation logic
# ---------------------------------------------------------------------------

class TestResyncRecommendation:

    def test_ok_returns_continue(self):
        assert ResyncGuard.get_resync_recommendation({"ok": True}) == "CONTINUE"

    def test_duplicate_only_returns_continue(self):
        res = {"ok": False, "has_duplicate": True, "has_gap": False}
        assert ResyncGuard.get_resync_recommendation(res) == "CONTINUE"

    def test_drifting_returns_wait(self):
        res = {"ok": False, "drifting": True}
        assert ResyncGuard.get_resync_recommendation(res) == "WAIT"

    def test_gap_returns_reload(self):
        res = {"ok": False, "has_gap": True}
        assert ResyncGuard.get_resync_recommendation(res) == "RELOAD_BACKFILL"

    def test_price_disc_returns_alert(self):
        res = {"ok": False, "has_price_disc": True}
        assert ResyncGuard.get_resync_recommendation(res) == "ALERT"

    def test_out_of_order_returns_reload(self):
        res = {"ok": False, "out_of_order": [{"index": 3}]}
        assert ResyncGuard.get_resync_recommendation(res) == "RELOAD_BACKFILL"

    def test_generic_failure_returns_alert(self):
        res = {"ok": False}
        assert ResyncGuard.get_resync_recommendation(res) == "ALERT"


# ---------------------------------------------------------------------------
# 7. JSONL logging
# ---------------------------------------------------------------------------

class TestLogging:

    def test_log_file_created(self, tmp_log_dir):
        g = ResyncGuard(log_dir=tmp_log_dir)
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=3)
        live = {"timestamp": bf.index[-1], "open": 100.0}
        g.check_backfill_to_live(bf, live, "5m")

        log_path = Path(tmp_log_dir) / "resync_log.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert "ts" in record
        assert record["event"] == "backfill_to_live"

    def test_multiple_events_appended(self, tmp_log_dir):
        g = ResyncGuard(log_dir=tmp_log_dir)
        bf = _make_ohlcv(_ts(0), freq_minutes=5, n_candles=3)
        live = {"timestamp": bf.index[-1], "open": 100.0}
        g.check_backfill_to_live(bf, live, "5m")

        timestamps = [_ts(i * 5) for i in range(5)]
        g.check_sequence_integrity(timestamps, "5m")

        log_path = Path(tmp_log_dir) / "resync_log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        events = [json.loads(l)["event"] for l in lines]
        assert "backfill_to_live" in events
        assert "sequence_integrity" in events
