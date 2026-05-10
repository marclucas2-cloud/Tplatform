"""Live micro sizing caps + anti-dispersion guardrails.

Purpose: let the desk buy CHEAP live truth without burning meaningful capital.

REFACTOR 2026-05-10 (Marc decision: "aucun seuil en dur, tout en fonction du
capital reel"). Caps are now expressed as % of live equity, not hard-coded $.
The caller passes `equity_usd` so the cap scales with the broker NAV.

Sizing caps per grade (% of live equity):
  S: 2.00% notional / 0.20% risk-if-stopped
  A: 1.20% notional / 0.12% risk-if-stopped
  B: 0.80% notional / 0.08% risk-if-stopped

Origin of the percentages: previously caps were $500/$50 (S), $300/$30 (A),
$200/$20 (B) calibrated for a $25K account. Converted 1:1 to fractions of
$25K to preserve calibration, future capital changes scale automatically.

Guardrails (unchanged):
  - No pyramiding before J+14 review per sleeve
  - Rate limit: max 1 NEW live_micro sleeve promoted per rolling 7 days

Validator entry points:
  - enforce_sizing(grade, notional_usd, equity_usd, risk_usd) -> raises LiveMicroViolation
  - can_pyramid(live_start_at, open_positions) -> (bool, reason)
  - can_promote_new_live_micro(registry_entries) -> (bool, reason)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

UTC = timezone.utc

# Caps as fraction of live equity (NOT hard-coded $).
# S: 2.00% notional / 0.20% risk = $500/$50 on $25K, scales with capital.
MAX_NOTIONAL_PCT_BY_GRADE: dict[str, float] = {
    "S": 0.0200,
    "A": 0.0120,
    "B": 0.0080,
}
MAX_RISK_PCT_BY_GRADE: dict[str, float] = {
    "S": 0.0020,
    "A": 0.0012,
    "B": 0.0008,
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


def get_max_notional_pct(grade: str | None) -> float:
    """Return notional cap as fraction of equity (e.g. 0.02 = 2%)."""
    if not grade:
        return 0.0
    return MAX_NOTIONAL_PCT_BY_GRADE.get(grade.upper(), 0.0)


def get_max_risk_pct(grade: str | None) -> float:
    """Return risk-if-stopped cap as fraction of equity."""
    if not grade:
        return 0.0
    return MAX_RISK_PCT_BY_GRADE.get(grade.upper(), 0.0)


def get_max_notional_usd(grade: str | None, equity_usd: float) -> float:
    """Return notional cap in $ for a given equity. equity_usd REQUIRED.

    No fallback to a hard-coded value: passing equity_usd<=0 returns 0
    (effectively blocking the order).
    """
    if equity_usd is None or equity_usd <= 0:
        return 0.0
    return get_max_notional_pct(grade) * equity_usd


def get_max_risk_usd(grade: str | None, equity_usd: float) -> float:
    """Return risk-if-stopped cap in $ for a given equity."""
    if equity_usd is None or equity_usd <= 0:
        return 0.0
    return get_max_risk_pct(grade) * equity_usd


def enforce_sizing(
    grade: str | None,
    notional_usd: float,
    equity_usd: float,
    risk_usd: float | None = None,
) -> None:
    """Raise LiveMicroViolation if sizing exceeds caps for the given grade.

    equity_usd MUST be provided (broker NAV at time of order). Caps are
    computed dynamically as `equity_usd * pct_for_grade`.
    """
    if equity_usd is None or equity_usd <= 0:
        raise LiveMicroViolation(
            "missing_or_invalid_equity",
            {"grade": grade, "equity_usd": equity_usd, "notional_usd": notional_usd},
        )

    cap_notional = get_max_notional_usd(grade, equity_usd)
    if cap_notional <= 0:
        raise LiveMicroViolation(
            "unknown_or_missing_grade",
            {"grade": grade, "notional_usd": notional_usd, "equity_usd": equity_usd},
        )
    if notional_usd > cap_notional:
        raise LiveMicroViolation(
            "notional_cap_exceeded",
            {
                "grade": grade,
                "cap_notional_usd": cap_notional,
                "cap_notional_pct": get_max_notional_pct(grade),
                "requested_usd": notional_usd,
                "equity_usd": equity_usd,
            },
        )
    if risk_usd is not None:
        cap_risk = get_max_risk_usd(grade, equity_usd)
        if risk_usd > cap_risk:
            raise LiveMicroViolation(
                "risk_cap_exceeded",
                {
                    "grade": grade,
                    "cap_risk_usd": cap_risk,
                    "cap_risk_pct": get_max_risk_pct(grade),
                    "requested_risk_usd": risk_usd,
                    "equity_usd": equity_usd,
                },
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
