"""
ROC-C05 — Crypto entry timing optimiser.

Optimises entry timing based on crypto session spread/volume curves.
Crypto markets are 24/7 but liquidity varies drastically by hour.

Spread curve (hour UTC -> multiplier):
  - Best:  14-15 UTC (US + EU overlap) -> 0.8×
  - Good:  8-11 UTC (EU open), 16-20 UTC (US session) -> 1.0×
  - OK:    12-13 UTC, 21-23 UTC -> 1.2×
  - Bad:   4-7 UTC (Asia close) -> 1.5×
  - Worst: 0-3 UTC (dead zone) -> 2.0×

Strategy-type windows:
  - trend:          best 9-11, 14-16 UTC (high volume confirms)
  - mean_reversion: best 1-5 UTC (low volume, mean-revert more)
  - momentum:       best 13-17 UTC (US catalyst hours)
  - event:          NEVER delayed

Base spreads:
  - BTC: 2 bps
  - ETH: 3 bps
  - Altcoins: 5 bps
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# Hour UTC -> spread multiplier (0 = midnight UTC)
SPREAD_CURVE: dict[int, float] = {
    0: 2.0,
    1: 2.0,
    2: 2.0,
    3: 2.0,
    4: 1.5,
    5: 1.5,
    6: 1.5,
    7: 1.5,
    8: 1.0,
    9: 1.0,
    10: 1.0,
    11: 1.0,
    12: 1.2,
    13: 1.2,
    14: 0.8,
    15: 0.8,
    16: 1.0,
    17: 1.0,
    18: 1.0,
    19: 1.0,
    20: 1.0,
    21: 1.2,
    22: 1.2,
    23: 1.2,
}

# Strategy type -> optimal hours UTC and hours to avoid
OPTIMAL_WINDOWS: dict[str, dict] = {
    "trend": {
        "optimal": list(range(9, 12)) + list(range(14, 17)),   # 9-11, 14-16
        "avoid": list(range(0, 5)),                             # 0-4
    },
    "mean_reversion": {
        "optimal": list(range(1, 6)),                           # 1-5
        "avoid": list(range(14, 18)),                           # 14-17 (too trendy)
    },
    "momentum": {
        "optimal": list(range(13, 18)),                         # 13-17
        "avoid": list(range(0, 5)),                             # 0-4
    },
    "event": {
        "optimal": list(range(0, 24)),                          # Always
        "avoid": [],                                            # Never delayed
    },
}

# Base spreads in basis points
BASE_SPREADS_BPS: dict[str, float] = {
    "BTC": 2.0,
    "BTCUSDT": 2.0,
    "ETH": 3.0,
    "ETHUSDT": 3.0,
}
DEFAULT_BASE_SPREAD_BPS = 5.0  # Altcoins

MAX_DELAY_HOURS = 6


# ──────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────

class CryptoEntryTiming:
    """Optimises crypto entry timing based on session liquidity.

    Usage::

        timing = CryptoEntryTiming()
        delay, hours = timing.should_delay_entry(signal, current_hour_utc=2)
        spread = timing.get_spread_estimate("BTCUSDT", hour_utc=14)
    """

    def __init__(
        self,
        spread_curve: Optional[dict[int, float]] = None,
        optimal_windows: Optional[dict[str, dict]] = None,
    ):
        """Initialise with optional custom curves.

        Args:
            spread_curve: Dict of hour_utc (0-23) -> spread multiplier.
            optimal_windows: Dict of strategy_type -> {optimal, avoid} hour lists.
        """
        self._spread_curve = spread_curve or dict(SPREAD_CURVE)
        self._optimal_windows = optimal_windows or dict(OPTIMAL_WINDOWS)

    # ── Public API ────────────────────────────────────────────────────

    def should_delay_entry(
        self,
        signal: dict,
        current_hour_utc: int,
        conviction: float = 0.0,
    ) -> tuple[bool, int]:
        """Determine if entry should be delayed for better timing.

        Rules (in priority order):
          1. EVENT signals are NEVER delayed.
          2. Conviction > 0.9 is NEVER delayed.
          3. Never delay more than 6 hours.
          4. Delay if current hour is in "avoid" window for strategy type.

        Args:
            signal: Signal dict with ``strategy_type`` key
                (trend, mean_reversion, momentum, event).
            current_hour_utc: Current hour in UTC (0-23).
            conviction: Conviction score 0-1 (from ConvictionSizer).

        Returns:
            Tuple of (should_delay: bool, delay_hours: int).
            delay_hours = 0 if should_delay is False.
        """
        strategy_type = signal.get("strategy_type", "trend").lower()

        # Rule 1: Never delay EVENT signals
        if strategy_type == "event":
            logger.debug("EntryTiming: EVENT signal — no delay")
            return False, 0

        # Rule 2: Never delay high conviction
        if conviction > 0.9:
            logger.debug(
                "EntryTiming: conviction %.2f > 0.9 — no delay", conviction
            )
            return False, 0

        # Get windows for this strategy type
        windows = self._optimal_windows.get(strategy_type)
        if windows is None:
            # Unknown strategy type — use trend defaults
            windows = self._optimal_windows.get("trend", {"optimal": [], "avoid": []})

        avoid_hours: list[int] = windows.get("avoid", [])
        optimal_hours: list[int] = windows.get("optimal", [])

        # Rule 4: Delay if in avoid window
        if current_hour_utc in avoid_hours:
            delay = self._calculate_delay_to_optimal(
                current_hour_utc, optimal_hours
            )
            if delay > 0:
                logger.info(
                    "EntryTiming: hour %d in avoid window for %s — "
                    "delay %dh to optimal window",
                    current_hour_utc,
                    strategy_type,
                    delay,
                )
                return True, delay

        # Not in avoid window — no delay
        return False, 0

    def get_spread_estimate(
        self,
        symbol: str,
        hour_utc: int,
    ) -> float:
        """Estimate current spread in basis points.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT", "ETHUSDT", "SOLUSDT").
            hour_utc: Current hour in UTC (0-23).

        Returns:
            Estimated spread in basis points.
        """
        # Get base spread for the asset
        symbol_upper = symbol.upper()
        base_bps = BASE_SPREADS_BPS.get(
            symbol_upper, DEFAULT_BASE_SPREAD_BPS
        )

        # Apply hour multiplier
        hour_mult = self._spread_curve.get(hour_utc % 24, 1.0)

        spread = base_bps * hour_mult

        logger.debug(
            "EntryTiming: spread estimate for %s at %dh UTC = %.1f bps "
            "(base=%.1f, mult=%.1f)",
            symbol,
            hour_utc,
            spread,
            base_bps,
            hour_mult,
        )

        return round(spread, 2)

    def get_optimal_entry_hour(self, strategy_type: str) -> list[int]:
        """Return the optimal entry hours for a strategy type.

        Args:
            strategy_type: One of "trend", "mean_reversion", "momentum", "event".

        Returns:
            List of optimal hours (UTC).
        """
        windows = self._optimal_windows.get(strategy_type.lower(), {})
        return windows.get("optimal", [])

    def get_spread_curve_summary(self) -> dict[str, list[int]]:
        """Return spread curve grouped by quality tier.

        Returns:
            Dict with keys "best", "good", "ok", "bad", "worst"
            mapped to lists of hours.
        """
        tiers: dict[str, list[int]] = {
            "best": [],    # <= 0.8
            "good": [],    # <= 1.0
            "ok": [],      # <= 1.2
            "bad": [],     # <= 1.5
            "worst": [],   # > 1.5
        }

        for hour, mult in sorted(self._spread_curve.items()):
            if mult <= 0.8:
                tiers["best"].append(hour)
            elif mult <= 1.0:
                tiers["good"].append(hour)
            elif mult <= 1.2:
                tiers["ok"].append(hour)
            elif mult <= 1.5:
                tiers["bad"].append(hour)
            else:
                tiers["worst"].append(hour)

        return tiers

    # ── Private helpers ───────────────────────────────────────────────

    def _calculate_delay_to_optimal(
        self,
        current_hour: int,
        optimal_hours: list[int],
    ) -> int:
        """Calculate hours to wait until the next optimal window.

        Wraps around midnight. Capped at MAX_DELAY_HOURS.

        Args:
            current_hour: Current hour UTC (0-23).
            optimal_hours: List of optimal hours.

        Returns:
            Number of hours to delay (0 if already optimal, capped at 6).
        """
        if not optimal_hours:
            return 0

        if current_hour in optimal_hours:
            return 0

        # Find the nearest optimal hour forward (wrapping at 24)
        min_delay = MAX_DELAY_HOURS + 1

        for opt_hour in optimal_hours:
            delay = (opt_hour - current_hour) % 24
            if delay == 0:
                delay = 24  # full wrap
            if delay < min_delay:
                min_delay = delay

        # Rule 3: Cap at MAX_DELAY_HOURS
        if min_delay > MAX_DELAY_HOURS:
            return MAX_DELAY_HOURS

        return min_delay
