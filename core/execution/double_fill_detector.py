"""
D3-03 — Double-Fill Detector.

Real-time detection of duplicate fills within a sliding time window.
If same ticker + same side + same quantity filled within 60s → ALERT.

Distinguishes intentional pyramiding (different order_id) from
accidental double fills (same or similar order_id pattern).

Actions:
  - Close excess position at market immediately
  - CRITICAL Telegram alert
  - Log to data/execution/double_fills.jsonl
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "execution" / "double_fills.jsonl"


@dataclass
class Fill:
    """Represents a single fill event."""
    timestamp: float
    order_id: str
    ticker: str
    side: str       # BUY / SELL
    quantity: float
    price: float
    broker: str
    strategy: str = ""


class DoubleFillDetector:
    """Detects and handles duplicate fills.

    Usage::

        detector = DoubleFillDetector(
            alert_callback=send_telegram,
            close_callback=emergency_close,
        )
        # On each fill:
        is_double = detector.check_fill(fill)
    """

    WINDOW_SECONDS = 60.0
    MAX_HISTORY = 1000

    def __init__(
        self,
        alert_callback: Optional[Callable] = None,
        close_callback: Optional[Callable] = None,
        window_seconds: float = 60.0,
    ):
        self._alert = alert_callback
        self._close = close_callback
        self._window = window_seconds
        self._recent_fills: deque[Fill] = deque(maxlen=self.MAX_HISTORY)
        self._detected_count = 0

    def check_fill(self, fill: Fill) -> bool:
        """Check if a fill is a potential double fill.

        Args:
            fill: The new fill to check.

        Returns:
            True if double fill detected.
        """
        now = time.time()

        # Clean old fills outside window
        while self._recent_fills and (now - self._recent_fills[0].timestamp > self._window):
            self._recent_fills.popleft()

        # Check for duplicates
        for existing in self._recent_fills:
            if self._is_duplicate(fill, existing):
                self._handle_double_fill(fill, existing)
                self._recent_fills.append(fill)
                return True

        self._recent_fills.append(fill)
        return False

    def _is_duplicate(self, new: Fill, existing: Fill) -> bool:
        """Determine if two fills are duplicates.

        Criteria:
          - Same broker (different brokers = legitimate multi-venue execution)
          - Same ticker
          - Same side (both BUY or both SELL)
          - Same quantity (within 1% tolerance)
          - Different order_id (same order_id = intentional partial)
          - Within time window
        """
        # Different brokers = legitimate cross-venue fills, not duplicates
        if getattr(new, "broker", "") != getattr(existing, "broker", ""):
            return False
        if new.ticker != existing.ticker:
            return False
        if new.side.upper() != existing.side.upper():
            return False
        if new.order_id == existing.order_id:
            return False  # Same order = partial fill, not double

        # Quantity match with tolerance
        qty_ratio = abs(new.quantity / existing.quantity) if existing.quantity > 0 else 0
        if not (0.99 <= qty_ratio <= 1.01):
            return False

        # Time check
        time_delta = abs(new.timestamp - existing.timestamp)
        if time_delta > self._window:
            return False

        return True

    def _handle_double_fill(self, new: Fill, existing: Fill) -> None:
        """Handle detected double fill."""
        self._detected_count += 1

        msg = (
            f"DOUBLE FILL DETECTED #{self._detected_count}\n"
            f"Ticker: {new.ticker} | Side: {new.side}\n"
            f"Qty: {new.quantity} | Price: {new.price}\n"
            f"Order 1: {existing.order_id} @ {existing.price}\n"
            f"Order 2: {new.order_id} @ {new.price}\n"
            f"Broker: {new.broker} | Strategy: {new.strategy}\n"
            f"Time delta: {abs(new.timestamp - existing.timestamp):.1f}s"
        )
        logger.critical(msg)

        # Alert
        if self._alert:
            self._alert(msg, level="critical")

        # Close excess position
        if self._close:
            try:
                close_side = "SELL" if new.side.upper() == "BUY" else "BUY"
                self._close(
                    ticker=new.ticker,
                    side=close_side,
                    quantity=new.quantity,
                    broker=new.broker,
                    reason="double_fill_correction",
                )
                logger.warning(
                    "Double fill: closed excess %s %s %s on %s",
                    close_side, new.quantity, new.ticker, new.broker,
                )
            except Exception as e:
                logger.error("Failed to close double fill excess: %s", e)

        # Log
        self._log_detection(new, existing)

    def _log_detection(self, new: Fill, existing: Fill) -> None:
        """Log double fill detection to JSONL."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": new.ticker,
            "side": new.side,
            "quantity": new.quantity,
            "fill_1": {
                "order_id": existing.order_id,
                "price": existing.price,
                "time": existing.timestamp,
            },
            "fill_2": {
                "order_id": new.order_id,
                "price": new.price,
                "time": new.timestamp,
            },
            "broker": new.broker,
            "strategy": new.strategy,
        }
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to log double fill: %s", e)

    @property
    def detected_count(self) -> int:
        return self._detected_count

    def get_recent_fills(self, limit: int = 20) -> list[dict]:
        """Return recent fills for debugging."""
        fills = list(self._recent_fills)[-limit:]
        return [
            {
                "ticker": f.ticker,
                "side": f.side,
                "qty": f.quantity,
                "price": f.price,
                "order_id": f.order_id,
                "broker": f.broker,
                "time": f.timestamp,
            }
            for f in fills
        ]
