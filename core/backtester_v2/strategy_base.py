"""Abstract base class for BacktesterV2 strategies.

Every strategy must subclass StrategyBase and implement on_bar().
The engine calls lifecycle methods in this order per bar:
  1. on_bar(bar, portfolio_state) -> Optional[Signal]
  2. on_fill(fill)  (if a fill occurred)
  3. on_eod(timestamp) (at end of each trading day)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from core.backtester_v2.types import Bar, Fill, PortfolioState, Signal


class StrategyBase(ABC):
    """Abstract strategy interface for BacktesterV2.

    Subclasses must implement:
        - on_bar: Core signal generation logic.
        - name: Unique strategy identifier.
        - asset_class: Target asset class.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name used for logging and position tracking."""
        ...

    @property
    def asset_class(self) -> str:
        """Asset class this strategy trades. Default: equity."""
        return "equity"

    @abstractmethod
    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Optional[Signal]:
        """Process a new bar and optionally emit a signal.

        IMPORTANT: The bar passed here is the latest CLOSED candle.
        Do not assume it represents "now" — it represents the most
        recent completed period.

        Args:
            bar: The latest closed OHLCV bar.
            portfolio_state: Current portfolio snapshot.

        Returns:
            A Signal to act on, or None to do nothing.
        """
        ...

    def on_fill(self, fill: Fill) -> None:
        """Called when an order from this strategy is filled.

        Args:
            fill: The execution report.
        """

    def on_eod(self, timestamp: "pd.Timestamp") -> None:
        """Called at end of each trading day.

        Args:
            timestamp: The EOD timestamp.
        """

    def get_parameters(self) -> Dict[str, Any]:
        """Return current strategy parameters for serialization.

        Returns:
            Dictionary of parameter names to values.
        """
        return {}

    def set_parameters(self, params: Dict[str, Any]) -> None:
        """Update strategy parameters (e.g., during walk-forward optimization).

        Args:
            params: Dictionary of parameter names to new values.
        """
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)
