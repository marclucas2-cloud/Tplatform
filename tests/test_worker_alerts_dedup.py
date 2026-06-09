"""Tests for core/worker/alerts.py — Telegram dedup added 2026-06-07.

Validates that repeating CRITICAL/WARNING alerts (e.g. BRACKET WATCHDOG firing
every 5 min on the same symbol) collapse to a single Telegram push within
_ALERT_DEDUP_TTL_SEC while still being persisted in the JSONL fallback.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.worker import alerts as alerts_mod


@pytest.fixture(autouse=True)
def reset_dedup_state(tmp_path):
    alerts_mod._ALERT_LAST_SENT.clear()
    fallback = tmp_path / "alerts.jsonl"
    with patch.object(alerts_mod, "_ALERTS_FALLBACK_PATH", fallback):
        yield fallback
    alerts_mod._ALERT_LAST_SENT.clear()


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_dedup_key_collapses_numbers_and_timestamps():
    k1 = alerts_mod._alert_dedup_key(
        "CRITICAL: WATCHDOG could not repose bracket on MNQ\n"
        "Reason: no SL/TP available (all 3 tiers failed)"
    )
    k2 = alerts_mod._alert_dedup_key(
        "CRITICAL: WATCHDOG could not repose bracket on MNQ\n"
        "Reason: timed out at 2026-06-05T14:32:17Z"
    )
    assert k1 == k2, "Same first-line cause should collapse to same dedup key"

    k_mes = alerts_mod._alert_dedup_key(
        "CRITICAL: WATCHDOG could not repose bracket on MES"
    )
    assert k_mes != k1, "Different symbol must produce a different key"


def test_first_critical_sends_telegram(reset_dedup_state):
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.critical.return_value = None
        alerts_mod.send_alert(
            "CRITICAL: WATCHDOG could not repose bracket on MNQ\nReason: x",
            level="critical",
        )
    assert mock_tg.critical.call_count == 1
    rows = _read_jsonl(reset_dedup_state)
    assert len(rows) == 1
    assert rows[0]["telegram_ok"] is True
    assert rows[0]["telegram_suppressed"] is False


def test_repeat_within_ttl_is_suppressed(reset_dedup_state):
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.critical.return_value = None
        for _ in range(5):
            alerts_mod.send_alert(
                "CRITICAL: WATCHDOG could not repose bracket on MNQ\nReason: x",
                level="critical",
            )
    # Telegram sent ONCE (first), four suppressed
    assert mock_tg.critical.call_count == 1
    rows = _read_jsonl(reset_dedup_state)
    assert len(rows) == 5, "All occurrences persisted to JSONL for audit"
    assert rows[0]["telegram_suppressed"] is False
    assert all(r["telegram_suppressed"] for r in rows[1:])


def test_distinct_symbols_each_get_one_send(reset_dedup_state):
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.critical.return_value = None
        for sym in ("MNQ", "MES", "M2K"):
            alerts_mod.send_alert(
                f"CRITICAL: WATCHDOG could not repose bracket on {sym}",
                level="critical",
            )
    assert mock_tg.critical.call_count == 3
    rows = _read_jsonl(reset_dedup_state)
    assert all(r["telegram_suppressed"] is False for r in rows)


def test_dedup_expires_after_ttl(reset_dedup_state, monkeypatch):
    fake_time = [1_000.0]
    monkeypatch.setattr(alerts_mod.time, "time", lambda: fake_time[0])
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.critical.return_value = None
        alerts_mod.send_alert("CRITICAL: WATCHDOG broken on MNQ", "critical")
        # 30 min later, still suppressed
        fake_time[0] += 1800
        alerts_mod.send_alert("CRITICAL: WATCHDOG broken on MNQ", "critical")
        # +1h01 from origin: ttl expired, sends again
        fake_time[0] += 1900
        alerts_mod.send_alert("CRITICAL: WATCHDOG broken on MNQ", "critical")
    assert mock_tg.critical.call_count == 2


def test_warning_level_also_deduped(reset_dedup_state):
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.warning.return_value = None
        alerts_mod.send_alert("WARNING: spread wide on EURUSD", "warning")
        alerts_mod.send_alert("WARNING: spread wide on EURUSD", "warning")
    assert mock_tg.warning.call_count == 1


def test_info_alerts_not_deduped(reset_dedup_state):
    # info-level isn't routed through dedup (it's digest-only anyway)
    with patch("core.telegram_v2.tg") as mock_tg:
        mock_tg.info.return_value = None
        alerts_mod.send_alert("info: heartbeat", "info")
        alerts_mod.send_alert("info: heartbeat", "info")
    assert mock_tg.info.call_count == 2
    assert _read_jsonl(reset_dedup_state) == [], "info alerts not persisted to fallback"
