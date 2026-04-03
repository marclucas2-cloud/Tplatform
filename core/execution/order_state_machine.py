"""Formal state machine for order lifecycle.

States:
  DRAFT      -> created in memory, not yet validated
  VALIDATED  -> passed validate_order() (risk checks OK)
  SUBMITTED  -> sent to broker
  PARTIAL    -> partially filled
  FILLED     -> fully filled
  REJECTED   -> rejected by risk manager or broker
  CANCELLED  -> cancelled (by trader or timeout)
  EXPIRED    -> expired (end of day, GTC timeout)
  ERROR      -> unexpected error

Invariants verified at each transition:
  1. A FILLED order MUST have an associated SL
  2. A PARTIAL order MUST have SL adjusted to partial quantity
  3. An order can NEVER return to a previous state
  4. A VALIDATED order has a validation timestamp
  5. A SUBMITTED order has a broker_order_id
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("execution.order_sm")


class OrderState(Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class IllegalTransitionError(Exception):
    """Transition impossible in the state machine."""
    pass


class InvariantViolation(Exception):
    """A safety invariant is violated."""
    pass


# Legal transitions: (from_state, to_state) -> guard function name or None
LEGAL_TRANSITIONS: dict[tuple[OrderState, OrderState], Optional[str]] = {
    (OrderState.DRAFT, OrderState.VALIDATED): "guard_validation",
    (OrderState.DRAFT, OrderState.REJECTED): None,
    (OrderState.VALIDATED, OrderState.SUBMITTED): "guard_submission",
    (OrderState.VALIDATED, OrderState.REJECTED): None,
    (OrderState.VALIDATED, OrderState.ERROR): None,  # Broker unreachable after validation
    (OrderState.SUBMITTED, OrderState.PARTIAL): "guard_partial_fill",
    (OrderState.SUBMITTED, OrderState.FILLED): "guard_full_fill",
    (OrderState.SUBMITTED, OrderState.REJECTED): None,
    (OrderState.SUBMITTED, OrderState.CANCELLED): None,
    (OrderState.SUBMITTED, OrderState.EXPIRED): None,
    (OrderState.SUBMITTED, OrderState.ERROR): None,
    (OrderState.PARTIAL, OrderState.FILLED): "guard_full_fill",
    (OrderState.PARTIAL, OrderState.CANCELLED): "guard_partial_cancel",
    (OrderState.PARTIAL, OrderState.ERROR): None,
}

TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
    OrderState.ERROR,
}


@dataclass
class OrderStateMachine:
    order_id: str
    symbol: str = ""
    side: str = ""  # BUY / SELL
    state: OrderState = OrderState.DRAFT
    history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    broker_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    total_quantity: float = 0.0
    has_sl: bool = False
    sl_order_id: Optional[str] = None
    validated_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    def transition(self, new_state: OrderState, **context) -> bool:
        """Attempt a state transition. Returns True if successful.

        Raises:
            IllegalTransitionError: if the transition is not legal
            InvariantViolation: if a safety invariant would be violated
        """
        key = (self.state, new_state)

        if key not in LEGAL_TRANSITIONS:
            legal = [
                t[1].value for t in LEGAL_TRANSITIONS if t[0] == self.state
            ]
            raise IllegalTransitionError(
                f"Order {self.order_id}: transition "
                f"{self.state.value} -> {new_state.value} is ILLEGAL. "
                f"Legal from {self.state.value}: {legal}"
            )

        guard_name = LEGAL_TRANSITIONS[key]
        if guard_name:
            guard = getattr(self, guard_name)
            if not guard(**context):
                return False

        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "at": datetime.now().isoformat(),
            "context": {
                k: v for k, v in context.items()
                if not callable(v)
            },
        })

        old_state = self.state
        self.state = new_state

        logger.info(
            f"Order {self.order_id} ({self.symbol}): "
            f"{old_state.value} -> {new_state.value}"
        )

        return True

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.state in (
            OrderState.SUBMITTED, OrderState.PARTIAL
        )

    # --- Guards ---

    def guard_validation(self, **ctx) -> bool:
        """Order passed validate_order()."""
        if not ctx.get("risk_approved", False):
            return False
        self.validated_at = datetime.now()
        return True

    def guard_submission(self, **ctx) -> bool:
        """Order has a broker_order_id."""
        broker_id = ctx.get("broker_order_id")
        if not broker_id:
            return False
        self.broker_order_id = broker_id
        self.submitted_at = datetime.now()
        return True

    def guard_partial_fill(self, **ctx) -> bool:
        """Partial fill: SL must be adjusted."""
        qty = ctx.get("filled_quantity", 0)
        if qty <= 0:
            return False
        self.filled_quantity += qty

        if not ctx.get("sl_adjusted", False):
            raise InvariantViolation(
                f"Order {self.order_id} ({self.symbol}): "
                f"partial fill WITHOUT SL adjustment. "
                f"BLOCKING transition."
            )
        self.has_sl = True
        sl_id = ctx.get("sl_order_id")
        if sl_id:
            self.sl_order_id = sl_id
        return True

    def guard_full_fill(self, **ctx) -> bool:
        """Full fill: SL must exist."""
        self.filled_quantity = self.total_quantity
        self.filled_at = datetime.now()

        if not ctx.get("has_sl", False) and not self.has_sl:
            raise InvariantViolation(
                f"Order {self.order_id} ({self.symbol}): "
                f"FILLED without SL. BLOCKING transition."
            )
        self.has_sl = True
        sl_id = ctx.get("sl_order_id")
        if sl_id:
            self.sl_order_id = sl_id
        return True

    def guard_partial_cancel(self, **ctx) -> bool:
        """Cancel remainder: partial position must stay protected."""
        if not self.has_sl:
            raise InvariantViolation(
                f"Order {self.order_id} ({self.symbol}): "
                f"cancelling remainder without SL on partial fill."
            )
        return True

    def to_dict(self) -> dict:
        """Serialize for persistence / API."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "state": self.state.value,
            "history": self.history,
            "created_at": self.created_at.isoformat(),
            "broker_order_id": self.broker_order_id,
            "filled_quantity": self.filled_quantity,
            "total_quantity": self.total_quantity,
            "has_sl": self.has_sl,
            "sl_order_id": self.sl_order_id,
        }
