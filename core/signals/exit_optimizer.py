"""P2-04: Exit Optimization — trailing stops, time-based exits, partial profits.

Three exit mechanisms:
  1. Trailing Stop: ATR-based trail that tightens as profit grows
  2. Time-based Exit: max holding period per strategy type
  3. Partial Profit Taking: 50% at 1.5x SL, 50% trailing
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ExitReason(str, Enum):
    TRAILING_STOP = "TRAILING_STOP"
    TIME_EXPIRY = "TIME_EXPIRY"
    TAKE_PROFIT = "TAKE_PROFIT"
    PARTIAL_PROFIT = "PARTIAL_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL = "MANUAL"
    NONE = "NONE"


@dataclass
class TrailingStopState:
    """State of a trailing stop for an active position."""
    symbol: str
    direction: str       # BUY or SELL
    entry_price: float
    current_stop: float  # Current trailing stop price
    atr: float
    peak_price: float    # Best price since entry
    profit_atr: float    # Current profit in ATR units
    trail_multiplier: float  # Current trail distance multiplier

    def update(self, current_price: float, atr: float | None = None):
        """Update trailing stop with new price."""
        if atr is not None:
            self.atr = atr

        if self.direction == "BUY":
            if current_price > self.peak_price:
                self.peak_price = current_price
            self.profit_atr = (current_price - self.entry_price) / self.atr if self.atr > 0 else 0
        else:
            if current_price < self.peak_price:
                self.peak_price = current_price
            self.profit_atr = (self.entry_price - current_price) / self.atr if self.atr > 0 else 0

        # Tighten trail as profit increases
        if self.profit_atr < 1.0:
            self.trail_multiplier = 2.0    # Wide — let it breathe
        elif self.profit_atr < 3.0:
            self.trail_multiplier = 1.5    # Tighten
        else:
            self.trail_multiplier = 1.0    # Tight — protect gains

        trail_distance = self.atr * self.trail_multiplier

        if self.direction == "BUY":
            new_stop = self.peak_price - trail_distance
            self.current_stop = max(self.current_stop, new_stop)  # Never lower
        else:
            new_stop = self.peak_price + trail_distance
            self.current_stop = min(self.current_stop, new_stop)  # Never higher

    @property
    def triggered(self) -> bool:
        """Check if trailing stop is triggered (need current price)."""
        return False  # Must be checked externally

    def is_triggered(self, current_price: float) -> bool:
        if self.direction == "BUY":
            return current_price <= self.current_stop
        else:
            return current_price >= self.current_stop


# Strategy-specific max holding periods
MAX_HOLDING_PERIODS = {
    # US strategies
    "dow_seasonal": timedelta(days=5),       # Mon-Fri
    "opex_gamma": timedelta(hours=6),        # Intraday
    "gap_continuation": timedelta(hours=3),  # Quick gap play
    "vwap_micro": timedelta(hours=4),        # Intraday
    "orb_v2": timedelta(hours=6),            # Intraday
    "meanrev_v2": timedelta(hours=4),        # Mean rev = fast
    # EU strategies
    "eu_gap_open": timedelta(hours=3),
    "bce_momentum_drift": timedelta(days=3),
    # FX strategies
    "fx_carry_vs": timedelta(days=30),       # Carry = hold
    "fx_vol_scaling": timedelta(days=14),
    # Crypto strategies
    "btc_eth_dual_momentum": timedelta(days=7),
    "margin_mean_reversion": timedelta(hours=12),
    "vol_breakout": timedelta(hours=24),
    "weekend_gap": timedelta(hours=48),
    "liquidation_momentum": timedelta(hours=6),
    # Default
    "default": timedelta(days=5),
}


@dataclass
class ExitDecision:
    """Exit optimizer's recommendation."""
    should_exit: bool
    exit_reason: ExitReason
    exit_price: float | None = None
    partial_pct: float = 1.0  # 1.0 = full exit, 0.5 = partial
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "should_exit": self.should_exit,
            "exit_reason": self.exit_reason.value,
            "exit_price": self.exit_price,
            "partial_pct": self.partial_pct,
            "details": self.details,
        }


class ExitOptimizer:
    """Optimizes exits with trailing stops, time limits, and partial profits.

    Usage:
        optimizer = ExitOptimizer()

        # Create trailing stop for new position
        trail = optimizer.create_trailing_stop(
            symbol="BTCUSDC", direction="BUY",
            entry_price=45000, atr=1200,
        )

        # On each price update:
        trail.update(current_price=45500, atr=1100)
        decision = optimizer.evaluate_exit(
            trail, current_price=45500,
            strategy="btc_eth_dual_momentum",
            entry_time=datetime(2026, 4, 1, 10, 0),
        )
    """

    def __init__(
        self,
        partial_profit_enabled: bool = True,
        partial_profit_at: float = 1.5,   # Take partial at 1.5x SL
        partial_pct: float = 0.50,         # Take 50% of position
    ):
        self._partial_enabled = partial_profit_enabled
        self._partial_at = partial_profit_at
        self._partial_pct = partial_pct
        self._partial_taken: set[str] = set()  # Track which positions took partial

    def create_trailing_stop(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        atr: float,
        initial_multiplier: float = 2.0,
    ) -> TrailingStopState:
        """Create a new trailing stop for a position."""
        if atr <= 0:
            atr = entry_price * 0.01  # 1% fallback

        trail_distance = atr * initial_multiplier

        if direction.upper() == "BUY":
            stop = entry_price - trail_distance
        else:
            stop = entry_price + trail_distance

        return TrailingStopState(
            symbol=symbol,
            direction=direction.upper(),
            entry_price=entry_price,
            current_stop=stop,
            atr=atr,
            peak_price=entry_price,
            profit_atr=0.0,
            trail_multiplier=initial_multiplier,
        )

    def evaluate_exit(
        self,
        trail: TrailingStopState,
        current_price: float,
        strategy: str = "default",
        entry_time: datetime | None = None,
        sl_price: float | None = None,
    ) -> ExitDecision:
        """Evaluate all exit conditions for a position.

        Priority:
          1. Trailing stop triggered -> EXIT
          2. Time expiry -> EXIT
          3. Partial profit threshold -> PARTIAL EXIT
          4. No exit signal -> HOLD
        """
        # 1. Trailing stop
        trail.update(current_price)
        if trail.is_triggered(current_price):
            return ExitDecision(
                should_exit=True,
                exit_reason=ExitReason.TRAILING_STOP,
                exit_price=trail.current_stop,
                details={
                    "peak_price": trail.peak_price,
                    "profit_atr": round(trail.profit_atr, 2),
                    "trail_multiplier": trail.trail_multiplier,
                },
            )

        # 2. Time-based exit
        if entry_time is not None:
            max_hold = MAX_HOLDING_PERIODS.get(strategy, MAX_HOLDING_PERIODS["default"])
            now = datetime.now(timezone.utc)
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            elapsed = now - entry_time

            if elapsed > max_hold:
                return ExitDecision(
                    should_exit=True,
                    exit_reason=ExitReason.TIME_EXPIRY,
                    details={
                        "max_hold": str(max_hold),
                        "elapsed": str(elapsed),
                        "strategy": strategy,
                    },
                )

        # 3. Partial profit
        position_key = f"{trail.symbol}:{trail.direction}:{trail.entry_price}"
        if (
            self._partial_enabled
            and position_key not in self._partial_taken
            and sl_price is not None
        ):
            sl_distance = abs(trail.entry_price - sl_price)
            profit_distance = abs(current_price - trail.entry_price)

            if sl_distance > 0 and profit_distance >= sl_distance * self._partial_at:
                self._partial_taken.add(position_key)
                return ExitDecision(
                    should_exit=True,
                    exit_reason=ExitReason.PARTIAL_PROFIT,
                    partial_pct=self._partial_pct,
                    details={
                        "profit_vs_sl": round(profit_distance / sl_distance, 2),
                        "partial_at_threshold": self._partial_at,
                        "pct_taken": self._partial_pct,
                    },
                )

        # No exit
        return ExitDecision(
            should_exit=False,
            exit_reason=ExitReason.NONE,
            details={
                "trailing_stop": round(trail.current_stop, 6),
                "profit_atr": round(trail.profit_atr, 2),
                "trail_mult": trail.trail_multiplier,
            },
        )

    def clear_partial(self, symbol: str):
        """Clear partial profit tracking for a symbol (on position close)."""
        self._partial_taken = {
            k for k in self._partial_taken if not k.startswith(f"{symbol}:")
        }
