"""Base calendar interface and factory for BacktesterV2."""

from __future__ import annotations

import abc
from datetime import date, time
from typing import Dict, Tuple

import pandas as pd


class MarketCalendar(abc.ABC):
    """Abstract market calendar defining session hours for an asset class."""

    @abc.abstractmethod
    def is_open(self, timestamp: pd.Timestamp) -> bool:
        """Return True if the market is open at *timestamp*.

        Args:
            timestamp: Timezone-aware timestamp to check.
        """

    @abc.abstractmethod
    def get_session_times(self, dt: date) -> Tuple[time, time]:
        """Return (open, close) times for the given calendar date.

        Args:
            dt: Calendar date.

        Returns:
            Tuple of (market_open, market_close) as naive times in
            the calendar's native timezone.
        """


class CalendarFactory:
    """Create calendar instances for a list of asset classes."""

    _REGISTRY: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, calendar_cls: type) -> None:
        """Register a calendar class under *name*."""
        cls._REGISTRY[name] = calendar_cls

    @classmethod
    def create(cls, asset_classes: list[str]) -> Dict[str, MarketCalendar]:
        """Instantiate calendars for each requested asset class.

        Args:
            asset_classes: e.g. ["equity", "fx", "futures", "crypto"].

        Returns:
            Dict mapping asset class name to MarketCalendar instance.
        """
        result: Dict[str, MarketCalendar] = {}
        for ac in asset_classes:
            if ac in cls._REGISTRY:
                result[ac] = cls._REGISTRY[ac]()
        return result
