"""Tests core/governance/live_micro_sizing.py — caps + guardrails."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.governance.live_micro_sizing import (
    MAX_NEW_LIVE_MICRO_PER_WEEK,
    MAX_NOTIONAL_USD_BY_GRADE,
    MAX_RISK_USD_BY_GRADE,
    MIN_DAYS_BEFORE_PYRAMID,
    LiveMicroViolation,
    can_promote_new_live_micro,
    can_pyramid,
    count_recent_live_micro_promotions,
    enforce_sizing,
    get_max_notional_usd,
    get_max_risk_usd,
)

UTC = timezone.utc


class TestCapsByGrade:
    def test_caps_defined_for_S_A_B(self):
        assert MAX_NOTIONAL_USD_BY_GRADE == {"S": 500.0, "A": 300.0, "B": 200.0}
        assert MAX_RISK_USD_BY_GRADE == {"S": 50.0, "A": 30.0, "B": 20.0}

    def test_get_max_notional_known_grade(self):
        assert get_max_notional_usd("S") == 500.0
        assert get_max_notional_usd("A") == 300.0
        assert get_max_notional_usd("B") == 200.0

    def test_get_max_notional_lowercase_normalized(self):
        assert get_max_notional_usd("b") == 200.0

    def test_get_max_notional_unknown_grade_returns_zero(self):
        assert get_max_notional_usd("C") == 0.0
        assert get_max_notional_usd("REJECTED") == 0.0
        assert get_max_notional_usd(None) == 0.0
        assert get_max_notional_usd("") == 0.0

    def test_get_max_risk_consistent(self):
        assert get_max_risk_usd("S") == 50.0
        assert get_max_risk_usd("A") == 30.0
        assert get_max_risk_usd("B") == 20.0
        assert get_max_risk_usd(None) == 0.0


class TestEnforceSizing:
    def test_within_cap_passes_silently(self):
        enforce_sizing("B", 200.0)
        enforce_sizing("A", 299.99)
        enforce_sizing("S", 500.0, risk_usd=50.0)

    def test_over_notional_cap_raises(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("B", 201.0)
        assert exc.value.reason == "notional_cap_exceeded"
        assert exc.value.detail["cap_notional_usd"] == 200.0
        assert exc.value.detail["requested_usd"] == 201.0

    def test_over_risk_cap_raises(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("A", 250.0, risk_usd=35.0)
        assert exc.value.reason == "risk_cap_exceeded"

    def test_unknown_grade_blocks(self):
        with pytest.raises(LiveMicroViolation) as exc:
            enforce_sizing("REJECTED", 100.0)
        assert exc.value.reason == "unknown_or_missing_grade"

    def test_none_grade_blocks(self):
        with pytest.raises(LiveMicroViolation):
            enforce_sizing(None, 100.0)

    def test_risk_none_skips_risk_check(self):
        enforce_sizing("B", 150.0, risk_usd=None)


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
