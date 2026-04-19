"""Order Tracker — bridges OrderStateMachine with broker execution.

Tracks all orders across brokers via their state machines.
Provides audit trail, invariant enforcement, and order queries.

Persistence (added 2026-04-19, Phase 3 XXL):
  - load_state(path) at boot: reload active orders from disk
  - save_state(path) called after every transition: atomic write
  - recovery_summary(): list orders that need broker reconciliation
"""

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.execution.order_state_machine import (
    IllegalTransitionError,
    InvariantViolation,
    OrderState,
    OrderStateMachine,
)

logger = logging.getLogger("execution.order_tracker")

ORDER_TRACKER_SCHEMA_VERSION = 1


class OrderTracker:
    """Thread-safe registry of all orders and their state machines.

    Optional persistence: pass `state_path` to enable atomic save on every
    transition + load on init. Critical for crash recovery — without persistence,
    all in-flight orders are lost on worker restart.
    """

    def __init__(self, alert_callback=None, state_path: Path | None = None):
        self._orders: dict[str, OrderStateMachine] = {}
        self._lock = threading.Lock()
        self._alert_cb = alert_callback
        self._state_path = state_path
        self._recovered_count = 0
        self._recovered_active: list[str] = []
        if state_path is not None:
            self._load_state()

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
        self.save_state()
        return osm

    def validate(self, order_id: str, risk_approved: bool) -> bool:
        """Transition DRAFT -> VALIDATED (or REJECTED)."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            if risk_approved:
                ok = osm.transition(OrderState.VALIDATED, risk_approved=True)
            else:
                osm.transition(OrderState.REJECTED)
                ok = False
        except (IllegalTransitionError, InvariantViolation) as e:
            self._alert(str(e))
            return False
        self.save_state()
        return ok

    def submit(self, order_id: str, broker_order_id: str) -> bool:
        """Transition VALIDATED -> SUBMITTED."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(
                OrderState.SUBMITTED, broker_order_id=broker_order_id
            )
        except (IllegalTransitionError, InvariantViolation) as e:
            self._alert(str(e))
            return False
        self.save_state()
        return ok

    def fill(
        self,
        order_id: str,
        has_sl: bool = False,
        sl_order_id: str | None = None,
    ) -> bool:
        """Transition SUBMITTED -> FILLED."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(
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
        self.save_state()
        return ok

    def partial_fill(
        self,
        order_id: str,
        filled_quantity: float,
        sl_adjusted: bool = False,
        sl_order_id: str | None = None,
    ) -> bool:
        """Transition SUBMITTED -> PARTIAL."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(
                OrderState.PARTIAL,
                filled_quantity=filled_quantity,
                sl_adjusted=sl_adjusted,
                sl_order_id=sl_order_id,
            )
        except InvariantViolation as e:
            self._alert(f"INVARIANT VIOLATION: {e}")
            return False
        self.save_state()
        return ok

    def cancel(self, order_id: str) -> bool:
        """Cancel an order."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(OrderState.CANCELLED)
        except IllegalTransitionError:
            return False
        self.save_state()
        return ok

    def reject(self, order_id: str) -> bool:
        """Reject an order."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(OrderState.REJECTED)
        except IllegalTransitionError:
            return False
        self.save_state()
        return ok

    def error(self, order_id: str) -> bool:
        """Mark order as ERROR."""
        osm = self._get(order_id)
        if not osm:
            return False
        try:
            ok = osm.transition(OrderState.ERROR)
        except IllegalTransitionError:
            return False
        self.save_state()
        return ok

    def get(self, order_id: str) -> OrderStateMachine | None:
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

    def _get(self, order_id: str) -> OrderStateMachine | None:
        with self._lock:
            return self._orders.get(order_id)

    def _alert(self, message: str) -> None:
        logger.error(message)
        if self._alert_cb:
            try:
                self._alert_cb(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Persistence (atomic write on every transition for crash recovery)
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        """Atomic write of all orders to disk. No-op if no state_path set."""
        if self._state_path is None:
            return
        with self._lock:
            payload = {
                "schema_version": ORDER_TRACKER_SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(),
                "orders": {oid: osm.to_dict() for oid, osm in self._orders.items()},
            }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=self._state_path.name + ".",
            suffix=".tmp",
            dir=str(self._state_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._state_path)
        except Exception as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.critical(f"OrderTracker save_state FAILED: {exc}")

    def _load_state(self) -> None:
        """Load orders from disk on init. Classifies recovery state."""
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.critical(
                f"OrderTracker state CORRUPT at {self._state_path}: {exc} -> "
                f"starting empty (orphan orders may exist on broker)"
            )
            self._alert(f"OrderTracker state CORRUPT: {exc}")
            return

        if not isinstance(raw, dict) or raw.get("schema_version") != ORDER_TRACKER_SCHEMA_VERSION:
            logger.critical(
                f"OrderTracker state schema mismatch at {self._state_path}: "
                f"expected v{ORDER_TRACKER_SCHEMA_VERSION}, got {raw.get('schema_version')!r}"
            )
            return

        with self._lock:
            for oid, raw_osm in raw.get("orders", {}).items():
                try:
                    self._orders[oid] = OrderStateMachine.from_dict(raw_osm)
                    self._recovered_count += 1
                    if self._orders[oid].is_active:
                        self._recovered_active.append(oid)
                except (KeyError, ValueError, TypeError) as exc:
                    logger.error(f"OrderTracker: skipping corrupt entry {oid}: {exc}")

        logger.info(
            f"OrderTracker recovered {self._recovered_count} orders from "
            f"{self._state_path} ({len(self._recovered_active)} still active)"
        )

    def recovery_summary(self) -> dict:
        """Return summary of recovered orders for boot-time reconciliation.

        Caller (worker) should iterate `active_order_ids` and reconcile against
        broker state (broker may have filled/cancelled them while worker was down).
        """
        return {
            "total_recovered": self._recovered_count,
            "active_order_ids": list(self._recovered_active),
            "state_path": str(self._state_path) if self._state_path else None,
        }
