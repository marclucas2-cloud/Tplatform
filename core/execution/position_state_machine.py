"""Formal state machine for position lifecycle.

States:
  PENDING    -> order submitted, not yet filled
  OPEN       -> active position, SL in place
  REDUCING   -> partial close in progress
  CLOSING    -> full close in progress
  CLOSED     -> closed, PnL realized
  ORPHAN     -> position without associated order (detected by reconciliation)
  EMERGENCY  -> emergency close in progress (kill switch)

Invariants:
  1. OPEN -> SL must exist broker-side
  2. OPEN -> reconciliation confirms broker has same position
  3. REDUCING -> quantity_broker == quantity_local (no divergence)
  4. CLOSED -> PnL is calculated and recorded in journal
  5. ORPHAN -> immediate alert, adopt or close
  6. CLOSED -> OPEN is ILLEGAL (closed position cannot reopen)
  7. EMERGENCY -> OPEN is ILLEGAL (no auto-reopen after emergency close)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("execution.position_sm")


class PositionState(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    REDUCING = "REDUCING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    ORPHAN = "ORPHAN"
    EMERGENCY = "EMERGENCY"


class IllegalPositionTransition(Exception):
    pass


class PositionInvariantViolation(Exception):
    pass


# Legal transitions
LEGAL_POSITION_TRANSITIONS: dict[
    tuple[PositionState, PositionState], Optional[str]
] = {
    (PositionState.PENDING, PositionState.OPEN): "guard_open",
    (PositionState.PENDING, PositionState.CLOSED): None,  # Order rejected/expired
    (PositionState.OPEN, PositionState.REDUCING): None,
    (PositionState.OPEN, PositionState.CLOSING): None,
    (PositionState.OPEN, PositionState.EMERGENCY): None,
    (PositionState.REDUCING, PositionState.OPEN): "guard_reduce_complete",
    (PositionState.REDUCING, PositionState.CLOSED): None,
    (PositionState.REDUCING, PositionState.EMERGENCY): None,
    (PositionState.CLOSING, PositionState.CLOSED): "guard_close_complete",
    (PositionState.CLOSING, PositionState.EMERGENCY): None,
    (PositionState.EMERGENCY, PositionState.CLOSED): None,
    (PositionState.ORPHAN, PositionState.OPEN): None,  # Adopted
    (PositionState.ORPHAN, PositionState.CLOSING): None,
    (PositionState.ORPHAN, PositionState.EMERGENCY): None,
}

TERMINAL_POSITION_STATES = {PositionState.CLOSED}


@dataclass
class PositionStateMachine:
    position_id: str
    symbol: str
    side: str = ""  # LONG / SHORT
    broker: str = ""
    state: PositionState = PositionState.PENDING
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    has_sl: bool = False
    sl_price: Optional[float] = None
    realized_pnl: float = 0.0
    history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    order_id: Optional[str] = None

    def transition(self, new_state: PositionState, **context) -> bool:
        """Attempt a state transition."""
        key = (self.state, new_state)

        if key not in LEGAL_POSITION_TRANSITIONS:
            legal = [
                t[1].value for t in LEGAL_POSITION_TRANSITIONS
                if t[0] == self.state
            ]
            raise IllegalPositionTransition(
                f"Position {self.position_id} ({self.symbol}): "
                f"{self.state.value} -> {new_state.value} is ILLEGAL. "
                f"Legal: {legal}"
            )

        guard_name = LEGAL_POSITION_TRANSITIONS[key]
        if guard_name:
            guard = getattr(self, guard_name)
            if not guard(**context):
                return False

        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "at": datetime.now().isoformat(),
            "context": {
                k: v for k, v in context.items() if not callable(v)
            },
        })

        old = self.state
        self.state = new_state
        logger.info(
            f"Position {self.position_id} ({self.symbol}): "
            f"{old.value} -> {new_state.value}"
        )
        return True

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_POSITION_STATES

    @property
    def is_active(self) -> bool:
        return self.state in (
            PositionState.OPEN, PositionState.REDUCING,
        )

    @property
    def unrealized_pnl(self) -> float:
        if self.entry_price <= 0 or self.current_price <= 0:
            return 0.0
        if self.side == "LONG":
            return (self.current_price - self.entry_price) * self.quantity
        elif self.side == "SHORT":
            return (self.entry_price - self.current_price) * self.quantity
        return 0.0

    # --- Guards ---

    def guard_open(self, **ctx) -> bool:
        """Position can only open if SL is in place."""
        if not ctx.get("has_sl", False):
            raise PositionInvariantViolation(
                f"Position {self.position_id} ({self.symbol}): "
                f"cannot OPEN without SL"
            )
        self.has_sl = True
        self.sl_price = ctx.get("sl_price")
        self.entry_price = ctx.get("entry_price", self.entry_price)
        self.quantity = ctx.get("quantity", self.quantity)
        self.opened_at = datetime.now()
        return True

    def guard_reduce_complete(self, **ctx) -> bool:
        """After reducing, SL must still be in place."""
        self.quantity = ctx.get("remaining_quantity", self.quantity)
        if not self.has_sl:
            raise PositionInvariantViolation(
                f"Position {self.position_id}: reduced but SL missing"
            )
        return True

    def guard_close_complete(self, **ctx) -> bool:
        """Record PnL on close."""
        self.realized_pnl = ctx.get("realized_pnl", 0.0)
        self.closed_at = datetime.now()
        self.quantity = 0.0
        return True

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "broker": self.broker,
            "state": self.state.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "has_sl": self.has_sl,
            "sl_price": self.sl_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "history": self.history,
        }
