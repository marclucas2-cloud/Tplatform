"""P3-02: Smart Order Routing v2 — cost-aware routing with spread monitoring.

Improvements over V1:
  1. Real-time spread monitoring (wait if spread > 2x avg, skip if > 3x)
  2. Order type selection by urgency (MARKET/LIMIT/cancel)
  3. Maker/taker optimization for Binance (prefer LIMIT)
  4. Spread logging for continuous calibration
"""

import json
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "execution"


@dataclass
class SpreadSnapshot:
    """A single spread observation."""
    symbol: str
    spread_bps: float
    timestamp: str
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class RoutingDecision:
    """Smart router's decision for an order."""
    order_type: str              # "MARKET", "LIMIT", "SKIP"
    limit_price: float | None    # For LIMIT orders
    patience_seconds: int = 0    # How long to wait for LIMIT fill
    fallback: str = "MARKET"     # After patience expires
    spread_bps: float = 0.0     # Current spread
    avg_spread_bps: float = 0.0  # Historical average
    spread_ratio: float = 0.0   # current / average
    reason: str = ""
    maker_fee_savings_bps: float = 0.0

    def to_dict(self) -> dict:
        return {
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "patience_seconds": self.patience_seconds,
            "fallback": self.fallback,
            "spread_bps": round(self.spread_bps, 2),
            "avg_spread_bps": round(self.avg_spread_bps, 2),
            "spread_ratio": round(self.spread_ratio, 2),
            "reason": self.reason,
            "maker_fee_savings_bps": round(self.maker_fee_savings_bps, 2),
        }


# Spread thresholds
SPREAD_WAIT_MULTIPLIER = 2.0   # Wait if current spread > 2x average
SPREAD_SKIP_MULTIPLIER = 3.0   # Skip if current spread > 3x average

# Maker/taker fee differential (Binance with BNB discount)
MAKER_FEE_BPS = 7.5    # 0.075%
TAKER_FEE_BPS = 7.5    # 0.075% (same with BNB discount, but maker can be lower)

# Order type by urgency
URGENCY_ORDER_TYPE = {
    "HIGH": {"type": "MARKET", "patience": 0},
    "NORMAL": {"type": "LIMIT", "patience": 300},    # 5 min
    "LOW": {"type": "LIMIT", "patience": 900},       # 15 min
}


class SpreadMonitor:
    """Monitors spreads per symbol for routing decisions.

    Thread-safe. Maintains a rolling window of spread observations.
    """

    def __init__(self, window_size: int = 100):
        self._spreads: dict[str, deque] = {}
        self._window = window_size
        self._lock = threading.Lock()

    def record(self, symbol: str, bid: float, ask: float):
        """Record a spread observation."""
        if bid <= 0 or ask <= 0 or ask < bid:
            return

        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000

        with self._lock:
            if symbol not in self._spreads:
                self._spreads[symbol] = deque(maxlen=self._window)
            self._spreads[symbol].append(SpreadSnapshot(
                symbol=symbol,
                spread_bps=spread_bps,
                timestamp=datetime.now(UTC).isoformat(),
                bid=bid,
                ask=ask,
            ))

    def get_current(self, symbol: str) -> float | None:
        """Get the most recent spread in bps."""
        with self._lock:
            q = self._spreads.get(symbol)
            if not q:
                return None
            return q[-1].spread_bps

    def get_average(self, symbol: str) -> float | None:
        """Get the rolling average spread in bps."""
        with self._lock:
            q = self._spreads.get(symbol)
            if not q:
                return None
            return sum(s.spread_bps for s in q) / len(q)

    def get_ratio(self, symbol: str) -> float | None:
        """Get current/average spread ratio."""
        current = self.get_current(symbol)
        avg = self.get_average(symbol)
        if current is None or avg is None or avg == 0:
            return None
        return current / avg


class SmartRouterV2:
    """Smart order router with spread awareness and cost optimization.

    Usage:
        router = SmartRouterV2()
        # Feed spread data
        router.spread_monitor.record("BTCUSDC", bid=44990, ask=45010)
        # Route an order
        decision = router.route(
            symbol="BTCUSDC",
            direction="BUY",
            notional=500,
            urgency="NORMAL",
            broker="binance",
            mid_price=45000,
        )
    """

    def __init__(self):
        self.spread_monitor = SpreadMonitor()
        self._order_log: list[dict] = []

    def route(
        self,
        symbol: str,
        direction: str,
        notional: float,
        urgency: str = "NORMAL",
        broker: str = "alpaca",
        mid_price: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
    ) -> RoutingDecision:
        """Route an order with cost-aware decision making.

        Args:
            symbol: Instrument symbol
            direction: "BUY" or "SELL"
            notional: Order notional value
            urgency: "HIGH", "NORMAL", "LOW"
            broker: Broker name for fee optimization
            mid_price: Mid-market price
            bid/ask: Best bid/ask (for spread recording)
        """
        # Record spread if we have bid/ask
        if bid > 0 and ask > 0:
            self.spread_monitor.record(symbol, bid, ask)
            if mid_price == 0:
                mid_price = (bid + ask) / 2

        # Get spread info
        current_spread = self.spread_monitor.get_current(symbol)
        avg_spread = self.spread_monitor.get_average(symbol)
        ratio = self.spread_monitor.get_ratio(symbol)

        # Check spread thresholds
        if ratio is not None and ratio > SPREAD_SKIP_MULTIPLIER:
            decision = RoutingDecision(
                order_type="SKIP",
                limit_price=None,
                spread_bps=current_spread or 0,
                avg_spread_bps=avg_spread or 0,
                spread_ratio=ratio,
                reason=f"Spread {ratio:.1f}x average — SKIP (>{SPREAD_SKIP_MULTIPLIER}x)",
            )
            self._log_decision(symbol, direction, notional, decision)
            return decision

        if ratio is not None and ratio > SPREAD_WAIT_MULTIPLIER and urgency != "HIGH":
            decision = RoutingDecision(
                order_type="LIMIT",
                limit_price=self._compute_limit_price(mid_price, direction, current_spread or 0),
                patience_seconds=60,  # Short patience during wide spread
                fallback="CANCEL",    # Don't chase in wide spread
                spread_bps=current_spread or 0,
                avg_spread_bps=avg_spread or 0,
                spread_ratio=ratio,
                reason=f"Spread {ratio:.1f}x average — WAIT with LIMIT (>{SPREAD_WAIT_MULTIPLIER}x)",
            )
            self._log_decision(symbol, direction, notional, decision)
            return decision

        # Normal routing by urgency
        urgency_cfg = URGENCY_ORDER_TYPE.get(urgency.upper(), URGENCY_ORDER_TYPE["NORMAL"])

        if urgency_cfg["type"] == "MARKET":
            decision = RoutingDecision(
                order_type="MARKET",
                limit_price=None,
                spread_bps=current_spread or 0,
                avg_spread_bps=avg_spread or 0,
                spread_ratio=ratio or 1.0,
                reason=f"{urgency} urgency — MARKET order",
            )
        else:
            # LIMIT order
            limit_price = self._compute_limit_price(mid_price, direction, current_spread or 0)

            # Maker fee savings for Binance
            maker_savings = 0.0
            if broker == "binance":
                maker_savings = TAKER_FEE_BPS - MAKER_FEE_BPS  # Could be > 0

            decision = RoutingDecision(
                order_type="LIMIT",
                limit_price=limit_price,
                patience_seconds=urgency_cfg["patience"],
                fallback="MARKET",
                spread_bps=current_spread or 0,
                avg_spread_bps=avg_spread or 0,
                spread_ratio=ratio or 1.0,
                maker_fee_savings_bps=maker_savings,
                reason=f"{urgency} urgency — LIMIT at {limit_price:.6f}, patience {urgency_cfg['patience']}s",
            )

        self._log_decision(symbol, direction, notional, decision)
        return decision

    def _compute_limit_price(
        self,
        mid_price: float,
        direction: str,
        current_spread_bps: float,
    ) -> float:
        """Compute limit price slightly inside the spread."""
        if mid_price <= 0:
            return 0.0

        # Place limit at 30% into the spread from our side
        half_spread = mid_price * current_spread_bps / 10_000 / 2
        offset = half_spread * 0.3

        if direction.upper() == "BUY":
            return round(mid_price - offset, 8)
        else:
            return round(mid_price + offset, 8)

    def _log_decision(
        self,
        symbol: str,
        direction: str,
        notional: float,
        decision: RoutingDecision,
    ):
        """Log routing decision for analysis."""
        self._order_log.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "notional": notional,
            **decision.to_dict(),
        })
        logger.info(
            "Router: %s %s $%.0f -> %s (spread %.1f bps, ratio %.1fx)",
            direction, symbol, notional,
            decision.order_type, decision.spread_bps, decision.spread_ratio,
        )

    def get_order_log(self) -> list[dict]:
        return list(self._order_log)

    def save_order_log(self, path: Path | None = None):
        """Save order log for post-analysis."""
        path = path or (DATA_DIR / "routing_log.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            for entry in self._order_log:
                f.write(json.dumps(entry) + "\n")
        self._order_log.clear()
