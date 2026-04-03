"""V10 Throttler EU — execution throttle rules for EU strategies.

Rules:
  - 3 consecutive losses -> pause 24h
  - Max 5 trades/day per strategy
  - No trading during BCE meeting days (except BCE strategy)
  - Reduce size 50% on day after EU holiday (low liquidity)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# BCE meeting dates 2026 (8 meetings per year, announced in advance)
BCE_MEETING_DATES_2026 = [
    date(2026, 1, 22),
    date(2026, 3, 12),
    date(2026, 4, 16),
    date(2026, 6, 4),
    date(2026, 7, 16),
    date(2026, 9, 10),
    date(2026, 10, 29),
    date(2026, 12, 17),
]

# EU holidays (common across major exchanges)
EU_HOLIDAYS_2026 = [
    date(2026, 1, 1),   # New Year
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 5, 1),   # Labour Day
    date(2026, 12, 25), # Christmas
    date(2026, 12, 26), # Boxing Day
]


class V10ThrottlerEU:
    """EU-specific strategy throttle rules."""

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        pause_hours: int = 24,
        max_trades_per_day: int = 5,
        post_holiday_size_mult: float = 0.5,
        bce_dates: list[date] | None = None,
        eu_holidays: list[date] | None = None,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.pause_hours = pause_hours
        self.max_trades_per_day = max_trades_per_day
        self.post_holiday_size_mult = post_holiday_size_mult
        self.bce_dates = bce_dates or BCE_MEETING_DATES_2026
        self.eu_holidays = eu_holidays or EU_HOLIDAYS_2026

        # State per strategy
        self._consecutive_losses: dict[str, int] = {}
        self._pause_until: dict[str, datetime] = {}
        self._daily_trade_count: dict[str, dict[str, int]] = {}  # {strategy: {date_str: count}}

    def record_trade_result(self, strategy: str, pnl: float, now: datetime) -> None:
        """Record a trade result for throttle tracking."""
        if pnl < 0:
            self._consecutive_losses[strategy] = self._consecutive_losses.get(strategy, 0) + 1
            if self._consecutive_losses[strategy] >= self.max_consecutive_losses:
                pause_end = now + timedelta(hours=self.pause_hours)
                self._pause_until[strategy] = pause_end
                logger.warning(
                    "%s: %d consecutive losses — paused until %s",
                    strategy, self._consecutive_losses[strategy],
                    pause_end.isoformat(),
                )
        else:
            self._consecutive_losses[strategy] = 0

        # Track daily count
        date_str = now.strftime("%Y-%m-%d")
        if strategy not in self._daily_trade_count:
            self._daily_trade_count[strategy] = {}
        self._daily_trade_count[strategy][date_str] = (
            self._daily_trade_count[strategy].get(date_str, 0) + 1
        )

    def should_trade(
        self, strategy: str, now: datetime
    ) -> tuple[bool, str, float]:
        """Check if a strategy is allowed to trade.

        Returns:
            (allowed, reason, size_multiplier)
            size_multiplier: 1.0 = normal, 0.5 = reduced, 0.0 = blocked
        """
        today = now.date() if hasattr(now, 'date') else now

        # Check pause (consecutive losses)
        if strategy in self._pause_until:
            if now < self._pause_until[strategy]:
                remaining = (self._pause_until[strategy] - now).total_seconds() / 3600
                return False, f"PAUSED — {remaining:.1f}h remaining ({self.max_consecutive_losses} consecutive losses)", 0.0
            else:
                del self._pause_until[strategy]
                self._consecutive_losses[strategy] = 0

        # Check daily trade limit
        date_str = today.strftime("%Y-%m-%d") if hasattr(today, 'strftime') else str(today)
        daily_count = self._daily_trade_count.get(strategy, {}).get(date_str, 0)
        if daily_count >= self.max_trades_per_day:
            return False, f"DAILY_LIMIT — {daily_count}/{self.max_trades_per_day} trades today", 0.0

        # Check BCE meeting day (block all except BCE strategy)
        if today in self.bce_dates and "bce" not in strategy.lower():
            return False, "BCE_MEETING — only BCE strategy allowed today", 0.0

        # Check post-holiday (reduced size)
        yesterday = today - timedelta(days=1)
        if yesterday in self.eu_holidays:
            return True, f"POST_HOLIDAY — size reduced {self.post_holiday_size_mult:.0%}", self.post_holiday_size_mult

        return True, "OK", 1.0

    def get_status(self) -> dict:
        """Return current throttle state for all strategies."""
        return {
            "paused": {s: t.isoformat() for s, t in self._pause_until.items()},
            "consecutive_losses": dict(self._consecutive_losses),
            "daily_counts": {
                s: dict(counts) for s, counts in self._daily_trade_count.items()
            },
        }
