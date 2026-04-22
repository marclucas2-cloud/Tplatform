"""Tests Phase 3.2 desk productif 2026-04-22: TTL 72h incidents.

Valide que:
  - incident < 72h est actif
  - incident > 72h SANS re-trigger meme (sev, book, cat) est auto-exclu
  - incident > 72h AVEC re-trigger dans la fenetre 72h reste actif
  - chaines de re-triggers maintiennent un groupe actif tant que le plus
    recent est < 72h
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.governance.incidents_ttl import (
    TTL_HOURS_DEFAULT,
    _incident_key,
    _parse_ts,
    filter_active_incidents,
    is_incident_active,
)

UTC = timezone.utc
NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)


def _incident(ts_offset_hours: float, sev="CRITICAL", book="alpaca_us", cat="reconciliation") -> dict:
    ts = NOW - timedelta(hours=ts_offset_hours)
    return {
        "timestamp": ts.isoformat(),
        "severity": sev,
        "context": {"book": book},
        "category": cat,
    }


class TestFilterActiveIncidents:

    def test_recent_incident_active(self):
        inc = _incident(ts_offset_hours=10)
        active = filter_active_incidents([inc], now=NOW)
        assert len(active) == 1

    def test_old_isolated_incident_excluded(self):
        """Incident > 72h SANS re-trigger -> exclu."""
        inc = _incident(ts_offset_hours=96)
        active = filter_active_incidents([inc], now=NOW)
        assert len(active) == 0

    def test_old_with_retrigger_within_72h_kept(self):
        """Incident a -96h et re-trigger a -80h: la chaine reste avec le plus
        recent = -80h < 72h donc actif. Les DEUX sont gardes."""
        old = _incident(ts_offset_hours=96)
        retrigger = _incident(ts_offset_hours=40)  # re-trigger a -40h du now (donc 56h apres old)
        active = filter_active_incidents([old, retrigger], now=NOW)
        # 40h < 72h donc tout le groupe est actif
        assert len(active) == 2

    def test_old_chain_all_expired_excluded(self):
        """Chaine de 2 incidents tous > 72h -> tous exclus."""
        old1 = _incident(ts_offset_hours=120)
        old2 = _incident(ts_offset_hours=90)
        active = filter_active_incidents([old1, old2], now=NOW)
        # latest = 90h > 72h -> groupe entier exclu
        assert len(active) == 0

    def test_different_books_isolated(self):
        """Incidents avec differents books ne forment pas la meme chaine."""
        alpaca = _incident(ts_offset_hours=10, book="alpaca_us")
        binance_old = _incident(ts_offset_hours=96, book="binance_crypto")
        active = filter_active_incidents([alpaca, binance_old], now=NOW)
        # alpaca garde, binance_old exclu (chaine differente isolee + vieille)
        assert len(active) == 1
        assert active[0]["context"]["book"] == "alpaca_us"

    def test_different_severities_isolated(self):
        """P0 et P1 meme book/cat ne forment pas la meme chaine."""
        p0 = _incident(ts_offset_hours=10, sev="P0")
        p1_old = _incident(ts_offset_hours=96, sev="P1")
        active = filter_active_incidents([p0, p1_old], now=NOW)
        assert len(active) == 1
        assert active[0]["severity"] == "P0"

    def test_missing_timestamp_ignored(self):
        bad = {"severity": "P0", "context": {"book": "alpaca_us"}, "category": "x"}
        active = filter_active_incidents([bad], now=NOW)
        assert len(active) == 0

    def test_invalid_timestamp_ignored(self):
        bad = {
            "timestamp": "not-a-date",
            "severity": "P0",
            "context": {"book": "alpaca_us"},
            "category": "reconciliation",
        }
        active = filter_active_incidents([bad], now=NOW)
        assert len(active) == 0


class TestIsIncidentActive:
    def test_recent_is_active(self):
        inc = _incident(ts_offset_hours=10)
        assert is_incident_active(inc, [inc], now=NOW) is True

    def test_old_isolated_not_active(self):
        inc = _incident(ts_offset_hours=96)
        assert is_incident_active(inc, [inc], now=NOW) is False

    def test_old_with_retrigger_is_active(self):
        old = _incident(ts_offset_hours=96)
        retrigger = _incident(ts_offset_hours=10)
        assert is_incident_active(old, [old, retrigger], now=NOW) is True


class TestIncidentKey:
    def test_key_structure(self):
        inc = _incident(ts_offset_hours=1, sev="P1", book="ibkr_eu", cat="preflight")
        key = _incident_key(inc)
        assert key == ("P1", "ibkr_eu", "preflight")

    def test_key_normalizes_severity_case(self):
        inc = {"severity": "critical", "context": {"book": "x"}, "category": "y"}
        assert _incident_key(inc)[0] == "CRITICAL"


class TestParseTs:
    def test_iso_with_tz(self):
        dt = _parse_ts("2026-04-22T12:00:00+00:00")
        assert dt.tzinfo is not None

    def test_iso_without_tz_assumed_utc(self):
        dt = _parse_ts("2026-04-22T12:00:00")
        assert dt.tzinfo == UTC

    def test_z_suffix_handled(self):
        dt = _parse_ts("2026-04-22T12:00:00Z")
        assert dt.tzinfo == UTC

    def test_invalid_returns_none(self):
        assert _parse_ts("not a date") is None
        assert _parse_ts(None) is None
