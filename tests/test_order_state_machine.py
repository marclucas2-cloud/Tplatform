"""Tests for Order State Machine (R4-01)."""

import pytest

from core.execution.order_state_machine import (
    IllegalTransitionError,
    InvariantViolation,
    OrderState,
    OrderStateMachine,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
)


class TestOrderStateCreation:
    def test_default_state_is_draft(self):
        osm = OrderStateMachine(order_id="test-001")
        assert osm.state == OrderState.DRAFT

    def test_order_fields(self):
        osm = OrderStateMachine(
            order_id="test-001",
            symbol="BTCUSDC",
            side="BUY",
            total_quantity=0.1,
        )
        assert osm.symbol == "BTCUSDC"
        assert osm.side == "BUY"
        assert osm.total_quantity == 0.1
        assert osm.filled_quantity == 0.0
        assert not osm.has_sl


class TestLegalTransitions:
    def test_draft_to_validated(self):
        osm = OrderStateMachine(order_id="t1")
        result = osm.transition(OrderState.VALIDATED, risk_approved=True)
        assert result is True
        assert osm.state == OrderState.VALIDATED
        assert osm.validated_at is not None

    def test_draft_to_validated_rejected(self):
        osm = OrderStateMachine(order_id="t1")
        result = osm.transition(OrderState.VALIDATED, risk_approved=False)
        assert result is False
        assert osm.state == OrderState.DRAFT  # Unchanged

    def test_draft_to_rejected(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.REJECTED)
        assert osm.state == OrderState.REJECTED
        assert osm.is_terminal

    def test_validated_to_submitted(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        result = osm.transition(
            OrderState.SUBMITTED,
            broker_order_id="BRK-123",
        )
        assert result is True
        assert osm.state == OrderState.SUBMITTED
        assert osm.broker_order_id == "BRK-123"
        assert osm.submitted_at is not None

    def test_validated_to_submitted_no_broker_id(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        result = osm.transition(OrderState.SUBMITTED)
        assert result is False
        assert osm.state == OrderState.VALIDATED

    def test_submitted_to_filled_with_sl(self):
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        result = osm.transition(
            OrderState.FILLED,
            has_sl=True,
            sl_order_id="SL-1",
        )
        assert result is True
        assert osm.state == OrderState.FILLED
        assert osm.has_sl
        assert osm.sl_order_id == "SL-1"
        assert osm.filled_at is not None

    def test_submitted_to_cancelled(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.CANCELLED)
        assert osm.state == OrderState.CANCELLED
        assert osm.is_terminal

    def test_submitted_to_expired(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.EXPIRED)
        assert osm.state == OrderState.EXPIRED

    def test_submitted_to_error(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.ERROR)
        assert osm.state == OrderState.ERROR


class TestPartialFills:
    def test_partial_fill(self):
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        result = osm.transition(
            OrderState.PARTIAL,
            filled_quantity=30,
            sl_adjusted=True,
        )
        assert result is True
        assert osm.state == OrderState.PARTIAL
        assert osm.filled_quantity == 30

    def test_partial_then_filled(self):
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.PARTIAL, filled_quantity=30, sl_adjusted=True)
        osm.transition(OrderState.FILLED, has_sl=True)
        assert osm.state == OrderState.FILLED
        assert osm.filled_quantity == 100

    def test_partial_then_cancel(self):
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.PARTIAL, filled_quantity=30, sl_adjusted=True)
        osm.transition(OrderState.CANCELLED)
        assert osm.state == OrderState.CANCELLED

    def test_partial_fill_zero_qty(self):
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        result = osm.transition(
            OrderState.PARTIAL,
            filled_quantity=0,
            sl_adjusted=True,
        )
        assert result is False


class TestInvariantViolations:
    def test_filled_without_sl_raises(self):
        """INVARIANT: A FILLED order MUST have an SL."""
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        with pytest.raises(InvariantViolation, match="FILLED without SL"):
            osm.transition(OrderState.FILLED)

    def test_partial_without_sl_adjustment_raises(self):
        """INVARIANT: A PARTIAL fill must have SL adjusted."""
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        with pytest.raises(InvariantViolation, match="partial fill WITHOUT SL"):
            osm.transition(OrderState.PARTIAL, filled_quantity=30, sl_adjusted=False)

    def test_partial_cancel_without_sl_raises(self):
        """INVARIANT: Cancelling remainder without SL on partial."""
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        # Force partial state without SL (bypassing guard for test)
        osm.state = OrderState.PARTIAL
        osm.filled_quantity = 30
        osm.has_sl = False
        with pytest.raises(InvariantViolation):
            osm.transition(OrderState.CANCELLED)


class TestIllegalTransitions:
    def test_draft_to_filled_illegal(self):
        osm = OrderStateMachine(order_id="t1")
        with pytest.raises(IllegalTransitionError):
            osm.transition(OrderState.FILLED)

    def test_draft_to_submitted_illegal(self):
        osm = OrderStateMachine(order_id="t1")
        with pytest.raises(IllegalTransitionError):
            osm.transition(OrderState.SUBMITTED)

    def test_filled_to_submitted_illegal(self):
        """Orders can NEVER go back to a previous state."""
        osm = OrderStateMachine(order_id="t1", total_quantity=100)
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(OrderState.FILLED, has_sl=True)
        with pytest.raises(IllegalTransitionError):
            osm.transition(OrderState.SUBMITTED)

    def test_cancelled_to_validated_illegal(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.REJECTED)
        with pytest.raises(IllegalTransitionError):
            osm.transition(OrderState.VALIDATED)

    def test_validated_to_partial_illegal(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        with pytest.raises(IllegalTransitionError):
            osm.transition(OrderState.PARTIAL)


class TestHistory:
    def test_history_recorded(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        assert len(osm.history) == 2
        assert osm.history[0]["from"] == "DRAFT"
        assert osm.history[0]["to"] == "VALIDATED"
        assert osm.history[1]["from"] == "VALIDATED"
        assert osm.history[1]["to"] == "SUBMITTED"

    def test_history_has_timestamps(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        assert "at" in osm.history[0]

    def test_history_has_context(self):
        osm = OrderStateMachine(order_id="t1")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        assert osm.history[0]["context"]["risk_approved"] is True


class TestTerminalStates:
    def test_terminal_states(self):
        assert OrderState.FILLED in TERMINAL_STATES
        assert OrderState.REJECTED in TERMINAL_STATES
        assert OrderState.CANCELLED in TERMINAL_STATES
        assert OrderState.EXPIRED in TERMINAL_STATES
        assert OrderState.ERROR in TERMINAL_STATES
        assert OrderState.DRAFT not in TERMINAL_STATES
        assert OrderState.SUBMITTED not in TERMINAL_STATES

    def test_is_terminal(self):
        osm = OrderStateMachine(order_id="t1")
        assert not osm.is_terminal
        osm.transition(OrderState.REJECTED)
        assert osm.is_terminal

    def test_is_active(self):
        osm = OrderStateMachine(order_id="t1")
        assert not osm.is_active
        osm = OrderStateMachine(order_id="t2")
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        assert osm.is_active


class TestSerialization:
    def test_to_dict(self):
        osm = OrderStateMachine(
            order_id="t1", symbol="BTCUSDC", side="BUY", total_quantity=0.1
        )
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        d = osm.to_dict()
        assert d["order_id"] == "t1"
        assert d["symbol"] == "BTCUSDC"
        assert d["state"] == "VALIDATED"
        assert d["total_quantity"] == 0.1
        assert isinstance(d["history"], list)


class TestFullLifecycleScenarios:
    def test_happy_path(self):
        """BUY -> validate -> submit -> fill with SL."""
        osm = OrderStateMachine(
            order_id="ord-1",
            symbol="ETHUSDC",
            side="BUY",
            total_quantity=1.0,
        )
        assert osm.state == OrderState.DRAFT
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        assert osm.state == OrderState.VALIDATED
        osm.transition(OrderState.SUBMITTED, broker_order_id="BIN-456")
        assert osm.state == OrderState.SUBMITTED
        osm.transition(OrderState.FILLED, has_sl=True, sl_order_id="SL-789")
        assert osm.state == OrderState.FILLED
        assert osm.is_terminal
        assert len(osm.history) == 3

    def test_rejection_path(self):
        """Risk manager rejects the order."""
        osm = OrderStateMachine(order_id="ord-2", symbol="BTCUSDC")
        result = osm.transition(OrderState.VALIDATED, risk_approved=False)
        assert result is False
        osm.transition(OrderState.REJECTED)
        assert osm.is_terminal

    def test_partial_fill_then_cancel(self):
        """Partial fill, then cancel remainder."""
        osm = OrderStateMachine(
            order_id="ord-3", symbol="SOLUSDC", total_quantity=50
        )
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        osm.transition(
            OrderState.PARTIAL,
            filled_quantity=20,
            sl_adjusted=True,
            sl_order_id="SL-1",
        )
        assert osm.filled_quantity == 20
        osm.transition(OrderState.CANCELLED)
        assert osm.is_terminal
        assert osm.filled_quantity == 20  # Partial position remains

    def test_bug_30_mars_impossible(self):
        """The 30/03 bug (FILLED without SL) is now impossible."""
        osm = OrderStateMachine(
            order_id="ord-4", symbol="BTCUSDC", total_quantity=0.1
        )
        osm.transition(OrderState.VALIDATED, risk_approved=True)
        osm.transition(OrderState.SUBMITTED, broker_order_id="B1")
        # Try to fill without SL — MUST raise
        with pytest.raises(InvariantViolation):
            osm.transition(OrderState.FILLED)
        # Order stays in SUBMITTED — not corrupted
        assert osm.state == OrderState.SUBMITTED
