"""Live micro sizing caps + anti-dispersion guardrails (2026-04-22).

Purpose: let the desk buy CHEAP live truth without burning meaningful capital.

Sizing caps per grade (USD notional, USD risk-if-stopped):
  S: 500 / 50
  A: 300 / 30
  B: 200 / 20
  (10% stop convention => risk ~= 10% of notional)

Guardrails:
  - No pyramiding before J+14 review per sleeve (open_positions >= 1 blocks new entry)
  - Rate limit: max 1 NEW live_micro sleeve promoted per rolling 7 days

Validator entry points:
  - enforce_sizing(grade, notional_usd, risk_usd) -> raises LiveMicroViolation
  - can_pyramid(live_start_at, open_positions) -> (bool, reason)
  - can_promote_new_live_micro(registry_entries) -> (bool, reason)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

UTC = timezone.utc

MAX_NOTIONAL_USD_BY_GRADE: dict[str, float] = {
    "S": 500.0,
    "A": 300.0,
    "B": 200.0,
}
MAX_RISK_USD_BY_GRADE: dict[str, float] = {
    "S": 50.0,
    "A": 30.0,
    "B": 20.0,
}

MIN_DAYS_BEFORE_PYRAMID: int = 14
MAX_NEW_LIVE_MICRO_PER_WEEK: int = 1
ROLLING_WINDOW_DAYS: int = 7


class LiveMicroViolation(Exception):
    """Raised when a live_micro guardrail is breached."""

    def __init__(self, reason: str, detail: dict | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(f"LIVE_MICRO_VIOLATION: {reason} detail={self.detail}")


def get_max_notional_usd(grade: str | None) -> float:
    if not grade:
        return 0.0
    return MAX_NOTIONAL_USD_BY_GRADE.get(grade.upper(), 0.0)


def get_max_risk_usd(grade: str | None) -> float:
    if not grade:
        return 0.0
    return MAX_RISK_USD_BY_GRADE.get(grade.upper(), 0.0)


def enforce_sizing(
    grade: str | None,
    notional_usd: float,
    risk_usd: float | None = None,
) -> None:
    """Raise LiveMicroViolation if sizing exceeds caps for the given grade."""
    cap_notional = get_max_notional_usd(grade)
    if cap_notional <= 0:
        raise LiveMicroViolation(
            "unknown_or_missing_grade",
            {"grade": grade, "notional_usd": notional_usd},
        )
    if notional_usd > cap_notional:
        raise LiveMicroViolation(
            "notional_cap_exceeded",
            {"grade": grade, "cap_notional_usd": cap_notional, "requested_usd": notional_usd},
        )
    if risk_usd is not None:
        cap_risk = get_max_risk_usd(grade)
        if risk_usd > cap_risk:
            raise LiveMicroViolation(
                "risk_cap_exceeded",
                {"grade": grade, "cap_risk_usd": cap_risk, "requested_risk_usd": risk_usd},
            )


def can_pyramid(
    live_start_at: str | None,
    current_open_positions: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """No pyramiding until J+14 review.

    Returns (can_pyramid, reason). If 0 positions open, always True.
    """
    if current_open_positions == 0:
        return True, "no_position_open"
    if not live_start_at:
        return False, "missing_live_start_at"
    try:
        start = date.fromisoformat(str(live_start_at)[:10])
    except (ValueError, TypeError):
        return False, f"invalid_live_start_at={live_start_at}"
    today = (now or datetime.now(UTC)).date()
    days_since = (today - start).days
    if days_since < MIN_DAYS_BEFORE_PYRAMID:
        return False, (
            f"no_pyramid_before_j{MIN_DAYS_BEFORE_PYRAMID} "
            f"(days_since={days_since}, open_positions={current_open_positions})"
        )
    return True, f"j{days_since}_review_passed"


def count_recent_live_micro_promotions(
    entries: Iterable[dict],
    now: datetime | None = None,
) -> int:
    """Count sleeves with status=live_micro and live_start_at in the rolling window."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=ROLLING_WINDOW_DAYS)).date()
    count = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("status") != "live_micro":
            continue
        start = e.get("live_start_at")
        if not start:
            continue
        try:
            d = date.fromisoformat(str(start)[:10])
        except (ValueError, TypeError):
            continue
        if d >= cutoff:
            count += 1
    return count


def can_promote_new_live_micro(
    registry_entries: Iterable[dict],
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Rate limit: max 1 new live_micro sleeve per rolling 7 days."""
    count = count_recent_live_micro_promotions(registry_entries, now=now)
    if count >= MAX_NEW_LIVE_MICRO_PER_WEEK:
        return False, (
            f"rate_limit_{count}/{MAX_NEW_LIVE_MICRO_PER_WEEK}"
            f"_per_{ROLLING_WINDOW_DAYS}d"
        )
    return True, f"rate_budget_{count}/{MAX_NEW_LIVE_MICRO_PER_WEEK}"
