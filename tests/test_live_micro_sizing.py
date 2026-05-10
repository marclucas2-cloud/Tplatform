"""Tests core/governance/live_micro_sizing.py — caps + guardrails.

REFACTOR 2026-05-10: caps now expressed as % of live equity, not hard-coded $.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.governance.live_micro_sizing import (
    MAX_NEW_LIVE_MICRO_PER_WEEK,
    MAX_NOTIONAL_PCT_BY_GRADE,
    MAX_RISK_PCT_BY_GRADE,
    MIN_DAYS_BEFORE_PYRAMID,
    LiveMicroViolation,
    can_promote_new_live_micro,
    can_pyramid,
    count_recent_live_micro_promotions,
    enforce_sizing,
    get_max_notional_pct,
    get_max_notional_usd,
    get_max_risk_pct,
    get_max_risk_usd,
)

UTC = timezone.utc

# Reference equity for backwards-compat with the old hard-coded values.
# Caps were $500/$300/$200 calibrated on $25K -> 2.0% / 1.2% / 0.8%.
REF_EQUITY = 25_000.0


class TestCapsByGrade:
    def test_pct_caps_defined_for_S_A_B(self):
        assert MAX_NOTIONAL_PCT_BY_GRADE == {"S": 0.0200, "A": 0.0120, "B": 0.0080}
        assert MAX_RISK_PCT_BY_GRADE == {"S": 0.0020, "A": 0.0012, "B": 0.0008}

    def test_get_max_notional_pct_known_grade(self):
        assert get_max_notional_pct("S") == 0.02
        assert get_max_notional_pct("A") == 0.012
        assert get_max_notional_pct("B") == 0.008

    def test_get_max_notional_pct_lowercase_normalized(self):
        assert get_max_notional_pct("b") == 0.008

    def test_get_max_notional_pct_unknown_returns_zero(self):
        assert get_max_notional_pct("C") == 0.0
        assert get_max_notional_pct("REJECTED") == 0.0
        assert get_max_notional_pct(None) == 0.0
        assert get_max_notional_pct("") == 0.0

    def test_get_max_risk_pct_consistent(self):
        assert get_max_risk_pct("S") == 0.0020
        assert get_max_risk_pct("A") == 0.0012
        assert get_max_risk_pct("B") == 0.0008
        assert get_max_risk_pct(None) == 0.0

    def test_usd_caps_scale_with_equity(self):
        """At $25K equity (legacy), caps match historical $500/$300/$200."""
        assert get_max_notional_usd("S", REF_EQUITY) == pytest.approx(500.0)
        assert get_max_notional_usd("A", REF_EQUITY) == pytest.approx(300.0)
        assert get_max_notional_usd("B", REF_EQUITY) == pytest.approx(200.0)
        assert get_max_risk_usd("S", REF_EQUITY) == pytest.approx(50.0)
        assert get_max_risk_usd("A", REF_EQUITY) == pytest.approx(30.0)
        assert get_max_risk_usd("B", REF_EQUITY) == pytest.approx(20.0)

    def test_usd_caps_double_when_equity_doubles(self):
        """At $50K equity, caps are 2x the legacy values."""
        assert get_max_notional_usd("S", 50_000.0) == pytest.approx(1000.0)
        assert get_max_notional_usd("A", 50_000.0) == pytest.approx(600.0)
        assert get_max_notional_usd("B", 50_000.0) == pytest.approx(400.0)

    def test_usd_caps_zero_when_equity_invalid(self):
        assert get_max_notional_usd("S", 0) == 0.0
        assert get_max_notional_usd("S", -1) == 0.0
        assert get_max_notional_usd("S", None) == 0.0
        assert get_max_risk_usd("S", 0) == 0.0


class TestEnforceSizing:
    def test_within_cap_passes_silently(self):
        enforce_sizing("B", 200.0, REF_EQUITY)
        enforce_sizing("A", 299.99, REF_EQUITY)
        enforce_sizing("S", 500.0, REF_EQUITY, risk_usd=50.0)

    def test_within_cap_passes_at_higher_equity(self):
        # On $50K equity, $999 notional is OK for S (cap = $1000).
        enforce_sizing("S", 999.0, 50_000.0, risk_usd=99.0)

    def test_over_notional_cap_raises(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("B", 201.0, REF_EQUITY)
        assert exc.value.reason == "notional_cap_exceeded"
        assert exc.value.detail["cap_notional_usd"] == 200.0
        assert exc.value.detail["requested_usd"] == 201.0
        assert exc.value.detail["cap_notional_pct"] == 0.008

    def test_over_notional_cap_at_lower_equity_raises_earlier(self):
        # On $10K equity, B cap is $80, so $200 fails (would have passed at $25K).
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("B", 200.0, 10_000.0)
        assert exc.value.reason == "notional_cap_exceeded"
        assert exc.value.detail["cap_notional_usd"] == 80.0
        assert exc.value.detail["equity_usd"] == 10_000.0

    def test_over_risk_cap_raises(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("A", 250.0, REF_EQUITY, risk_usd=35.0)
        assert exc.value.reason == "risk_cap_exceeded"

    def test_unknown_grade_blocks(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("REJECTED", 100.0, REF_EQUITY)
        assert exc.value.reason == "unknown_or_missing_grade"

    def test_none_grade_blocks(self):
        with pytest.raises(LiveMicroViolation):
            enforce_sizing(None, 100.0, REF_EQUITY)

    def test_risk_none_skips_risk_check(self):
        enforce_sizing("B", 150.0, REF_EQUITY, risk_usd=None)

    def test_zero_equity_blocks(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("S", 100.0, 0.0)
        assert exc.value.reason == "missing_or_invalid_equity"

    def test_negative_equity_blocks(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("S", 100.0, -5.0)
        assert exc.value.reason == "missing_or_invalid_equity"

    def test_missing_equity_blocks(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("S", 100.0, None)  # type: ignore[arg-type]
        assert exc.value.reason == "missing_or_invalid_equity"


class TestCanPyramid:
    def test_zero_open_positions_always_allowed(self):
        ok, reason = can_pyramid(live_start_at="2026-01-01", current_open_positions=0)
        assert ok is True
        assert reason == "no_position_open"

    def test_position_open_and_j_plus_5_blocks(self):
        now = datetime(2026, 4, 20, tzinfo=UTC)
        start = "2026-04-15"  # J+5
        ok, reason = can_pyramid(start, current_open_positions=1, now=now)
        assert ok is False
        assert f"no_pyramid_before_j{MIN_DAYS_BEFORE_PYRAMID}" in reason

    def test_position_open_and_j_plus_14_allowed(self):
        now = datetime(2026, 4, 29, tzinfo=UTC)
        start = "2026-04-15"  # J+14
        ok, reason = can_pyramid(start, current_open_positions=1, now=now)
        assert ok is True
        assert "review_passed" in reason

    def test_missing_live_start_blocks(self):
        ok, reason = can_pyramid(None, current_open_positions=1)
        assert ok is False
        assert reason == "missing_live_start_at"

    def test_invalid_live_start_blocks(self):
        ok, reason = can_pyramid("not-a-date", current_open_positions=1)
        assert ok is False
        assert "invalid_live_start_at" in reason

    def test_live_start_with_time_suffix_accepted(self):
        now = datetime(2026, 4, 29, tzinfo=UTC)
        ok, reason = can_pyramid("2026-04-15T10:30:00", current_open_positions=1, now=now)
        assert ok is True


class TestCanPromoteNewLiveMicro:
    def test_empty_registry_allows(self):
        ok, reason = can_promote_new_live_micro([])
        assert ok is True
        assert "rate_budget_0" in reason

    def test_zero_recent_live_micro_allows(self):
        now = datetime(2026, 4, 22, tzinfo=UTC)
        entries = [
            {"status": "live_micro", "live_start_at": "2026-04-01"},  # 21d ago, outside 7d window
            {"status": "paper_only", "live_start_at": None},
            {"status": "live_core", "live_start_at": "2026-04-21"},
        ]
        ok, _ = can_promote_new_live_micro(entries, now=now)
        assert ok is True

    def test_one_recent_live_micro_blocks(self):
        now = datetime(2026, 4, 22, tzinfo=UTC)
        entries = [
            {"status": "live_micro", "live_start_at": "2026-04-18"},  # 4d ago
        ]
        ok, reason = can_promote_new_live_micro(entries, now=now)
        assert ok is False
        assert f"rate_limit_1/{MAX_NEW_LIVE_MICRO_PER_WEEK}" in reason

    def test_count_recent_ignores_paper_only(self):
        now = datetime(2026, 4, 22, tzinfo=UTC)
        entries = [
            {"status": "paper_only", "live_start_at": "2026-04-21"},
            {"status": "live_core", "live_start_at": "2026-04-21"},
            {"status": "frozen", "live_start_at": "2026-04-21"},
        ]
        assert count_recent_live_micro_promotions(entries, now=now) == 0

    def test_count_recent_ignores_missing_live_start(self):
        now = datetime(2026, 4, 22, tzinfo=UTC)
        entries = [
            {"status": "live_micro", "live_start_at": None},
            {"status": "live_micro"},
        ]
        assert count_recent_live_micro_promotions(entries, now=now) == 0
