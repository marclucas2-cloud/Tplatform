"""Order Tracker — bridges OrderStateMachine with broker execution.

Tracks all orders across brokers via their state machines.
Provides audit trail, invariant enforcement, and order queries.
"""

import logging
import threading
import uuid
from datetime import datetime
from typing import Optional

from core.execution.order_state_machine import (
    IllegalTransitionError,
    InvariantViolation,
    OrderState,
    OrderStateMachine,
)

logger = logging.getLogger("execution.order_tracker")


class OrderTracker:
    """Thread-safe registry of all orders and their state machines."""

    def __init__(self, alert_callback=None):
        self._orders: dict[str, OrderStateMachine] = {}
        self._lock = threading.Lock()
        self._alert_cb = alert_callback

    def create_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        broker: str = "",
        strategy: str = "",
    ) -> OrderStateMachine:
        """Create a new order in DRAFT state."""
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        osm = OrderStateMachine(
            order_id=order_id,
            symbol=symbol,
            side=side,
            total_quantity=quantity,
        )
        with self._lock:
            self._orders[order_id] = osm
        logger.info(
            f"Order created: {order_id} {side} {quantity} {symbol} "
            f"(broker={broker}, strat={strategy})"
        )
        return osm

    def validate(self, order_id: str, risk_approved: bool) -> bool:
        """Transition DRAFT -> VALIDATED (or REJECTED)."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            if risk_approved:
                return osm.transition(OrderState.VALIDATED, risk_approved=True)
            else:
                osm.transition(OrderState.REJECTED)
                return False
        except (IllegalTransitionError, InvariantViolation) as e:
            self._alert(str(e))
            return False

    def submit(self, order_id: str, broker_order_id: str) -> bool:
        """Transition VALIDATED -> SUBMITTED."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(
                OrderState.SUBMITTED, broker_order_id=broker_order_id
            )
        except (IllegalTransitionError, InvariantViolation) as e:
            self._alert(str(e))
            return False

    def fill(
        self,
        order_id: str,
        has_sl: bool = False,
        sl_order_id: Optional[str] = None,
    ) -> bool:
        """Transition SUBMITTED -> FILLED."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(
                OrderState.FILLED,
                has_sl=has_sl,
                sl_order_id=sl_order_id,
            )
        except InvariantViolation as e:
            self._alert(f"INVARIANT VIOLATION: {e}")
            return False
        except IllegalTransitionError as e:
            self._alert(str(e))
            return False

    def partial_fill(
        self,
        order_id: str,
        filled_quantity: float,
        sl_adjusted: bool = False,
        sl_order_id: Optional[str] = None,
    ) -> bool:
        """Transition SUBMITTED -> PARTIAL."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(
                OrderState.PARTIAL,
                filled_quantity=filled_quantity,
                sl_adjusted=sl_adjusted,
                sl_order_id=sl_order_id,
            )
        except InvariantViolation as e:
            self._alert(f"INVARIANT VIOLATION: {e}")
            return False

    def cancel(self, order_id: str) -> bool:
        """Cancel an order."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(OrderState.CANCELLED)
        except IllegalTransitionError:
            return False

    def reject(self, order_id: str) -> bool:
        """Reject an order."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(OrderState.REJECTED)
        except IllegalTransitionError:
            return False

    def error(self, order_id: str) -> bool:
        """Mark order as ERROR."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            return osm.transition(OrderState.ERROR)
        except IllegalTransitionError:
            return False

    def get(self, order_id: str) -> Optional[OrderStateMachine]:
        return self._get(order_id)

    def get_active_orders(self) -> list[OrderStateMachine]:
        with self._lock:
            return [o for o in self._orders.values() if o.is_active]

    def get_orders_by_symbol(self, symbol: str) -> list[OrderStateMachine]:
        with self._lock:
            return [
                o for o in self._orders.values() if o.symbol == symbol
            ]

    def get_recent_orders(self, n: int = 50) -> list[OrderStateMachine]:
        with self._lock:
            return sorted(
                self._orders.values(),
                key=lambda o: o.created_at,
                reverse=True,
            )[:n]

    def cleanup_terminal(self, max_age_hours: int = 24) -> int:
        """Remove terminal orders older than max_age_hours."""
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        to_remove = []
        with self._lock:
            for oid, osm in self._orders.items():
                if osm.is_terminal and osm.created_at.timestamp() < cutoff:
                    to_remove.append(oid)
            for oid in to_remove:
                del self._orders[oid]
        return len(to_remove)

    def _get(self, order_id: str) -> Optional[OrderStateMachine]:
        with self._lock:
            return self._orders.get(order_id)

    def _alert(self, message: str) -> None:
        logger.error(message)
        if self._alert_cb:
            try:
                self._alert_cb(message)
            except Exception:
                pass
