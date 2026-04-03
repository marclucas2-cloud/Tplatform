"""
FX Trailing Stop — ATR-based dynamic stop that locks partial profits.

Activates after a minimum profit threshold (1.5x ATR).
Trail distance = 1.0x ATR (tighter than initial SL of 2x ATR).
Never moves the stop backwards.
"""
import logging

logger = logging.getLogger(__name__)


class FXTrailingStop:
    """ATR-based trailing stop for FX swing positions.

    Workflow:
    1. Position opens with fixed SL (e.g., 2x ATR)
    2. Price moves in favor -> profit accumulates
    3. When profit >= activation_atr * ATR -> trailing stop activates
    4. Stop trails at trail_atr * ATR behind the best price
    5. Stop NEVER moves backwards (ratchet)
    6. When price reverses and hits trailing stop -> position closed

    Args:
        activation_atr: ATR multiplier to activate trailing (default 1.5)
        trail_atr: ATR multiplier for trail distance (default 1.0)
    """

    def __init__(self, activation_atr: float = 1.5, trail_atr: float = 1.0):
        if activation_atr <= 0 or trail_atr <= 0:
            raise ValueError("ATR multipliers must be positive")
        if trail_atr >= activation_atr:
            raise ValueError("trail_atr must be < activation_atr for meaningful trailing")

        self.activation_atr = activation_atr
        self.trail_atr = trail_atr

        # Track best prices per position
        self._best_prices: dict[str, float] = {}
        self._activated: dict[str, bool] = {}

    def update(self, position_id: str, entry_price: float, current_price: float,
               current_atr: float, direction: int, current_stop: float) -> float | None:
        """Calculate new trailing stop price if applicable.

        Args:
            position_id: unique position identifier
            entry_price: original entry price
            current_price: current market price
            current_atr: current ATR value (in price units, not pips)
            direction: 1 for LONG, -1 for SHORT
            current_stop: current stop loss price

        Returns:
            New stop price if it should be updated, None otherwise.
        """
        if current_atr <= 0:
            return None

        # Calculate profit in price units
        profit = (current_price - entry_price) * direction
        activation_threshold = self.activation_atr * current_atr

        # Track best price (highest for long, lowest for short)
        best_key = position_id
        if best_key not in self._best_prices:
            self._best_prices[best_key] = current_price

        if direction == 1:  # LONG
            self._best_prices[best_key] = max(self._best_prices[best_key], current_price)
        else:  # SHORT
            self._best_prices[best_key] = min(self._best_prices[best_key], current_price)

        best_price = self._best_prices[best_key]

        # Check activation
        if profit < activation_threshold:
            return None  # Not enough profit to activate

        if not self._activated.get(best_key, False):
            self._activated[best_key] = True
            logger.info(
                f"Trailing stop ACTIVATED for {position_id}: "
                f"profit={profit:.5f} >= {activation_threshold:.5f} ({self.activation_atr}x ATR)"
            )

        # Calculate trailing stop
        trail_distance = self.trail_atr * current_atr

        if direction == 1:  # LONG: stop below best price
            new_stop = best_price - trail_distance
        else:  # SHORT: stop above best price
            new_stop = best_price + trail_distance

        # Never move stop backwards (ratchet)
        if direction == 1:
            if new_stop <= current_stop:
                return None  # Would move stop down -- not allowed
        else:
            if new_stop >= current_stop:
                return None  # Would move stop up -- not allowed

        logger.info(
            f"Trailing stop UPDATE for {position_id}: "
            f"best={best_price:.5f}, new_stop={new_stop:.5f} "
            f"(was {current_stop:.5f}, trail={trail_distance:.5f})"
        )

        return round(new_stop, 5)  # FX precision

    def reset(self, position_id: str):
        """Reset tracking for a closed position."""
        self._best_prices.pop(position_id, None)
        self._activated.pop(position_id, None)

    def is_activated(self, position_id: str) -> bool:
        """Check if trailing stop is active for a position."""
        return self._activated.get(position_id, False)

    def get_status(self, position_id: str) -> dict:
        """Get trailing stop status for a position."""
        return {
            "activated": self._activated.get(position_id, False),
            "best_price": self._best_prices.get(position_id),
            "activation_atr": self.activation_atr,
            "trail_atr": self.trail_atr,
        }
