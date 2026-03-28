"""
Tests for HARDEN-004: FXBracketHandler — FX-specific bracket logic.

Validates:
  - STP LMT order type (not STP MKT) for weekend gap protection
  - TIF GTC for weekend survival
  - OCA group correctly set
  - Pre-weekend check: unprotected positions detected
  - Pre-weekend check: all positions protected
  - BUY stop_limit = stop_price - 0.0005
  - SELL stop_limit = stop_price + 0.0005
  - Lot size < 25000 rejected

All ib_insync interactions are mocked — no real IBKR connection needed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.broker.ibkr_bracket import (
    BracketOrderError,
    BracketOrderManager,
    FX_MIN_LOT,
    FX_SL_PIP_OFFSET,
    FXBracketHandler,
)


@pytest.fixture(autouse=True)
def _no_persist(monkeypatch):
    """Disable bracket persistence during tests to avoid filesystem side effects."""
    monkeypatch.setattr(BracketOrderManager, "_save_brackets", lambda self: None)
    monkeypatch.setattr(BracketOrderManager, "_load_brackets", lambda self: None)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_ib():
    """Create a fully mocked ib_insync.IB instance."""
    ib = MagicMock()
    ib.qualifyContracts.return_value = True
    ib.sleep.return_value = None
    ib.positions.return_value = []
    ib.openTrades.return_value = []

    _next_order_id = [2000]

    def _place_order(contract, order):
        if not hasattr(order, "orderId") or order.orderId in (None, 0):
            order.orderId = _next_order_id[0]
            _next_order_id[0] += 1
        trade = MagicMock()
        trade.order = order
        trade.contract = contract
        return trade

    ib.placeOrder.side_effect = _place_order
    return ib


@pytest.fixture
def handler_live(mock_ib):
    """FXBracketHandler with mocked IB connection."""
    bm = BracketOrderManager(ib_connection=mock_ib)
    return FXBracketHandler(bm)


@pytest.fixture
def handler_dry():
    """FXBracketHandler without IB connection (dry-run mode)."""
    bm = BracketOrderManager(ib_connection=None)
    return FXBracketHandler(bm)


# =============================================================================
# TEST 1: FX bracket created with STP LMT (not STP MKT)
# =============================================================================

class TestFXBracketStpLmt:
    """Verify that FX brackets use STP LMT order type."""

    def test_fx_bracket_has_stp_lmt_order_type(self, handler_dry):
        """Bracket info must indicate STP_LMT for stop loss, not STP_MKT."""
        result = handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        assert result["order_type_sl"] == "STP_LMT"
        assert "stop_limit_price" in result
        assert result["stop_limit_price"] is not None


# =============================================================================
# TEST 2: FX bracket TIF GTC
# =============================================================================

class TestFXBracketTifGtc:
    """Verify that FX brackets use GTC time-in-force."""

    def test_fx_bracket_tif_is_gtc(self, handler_dry):
        """FX bracket must use GTC to survive weekends."""
        result = handler_dry.create_fx_bracket_v2(
            pair="GBPUSD",
            direction="SELL",
            lot_size=50_000,
            entry_price=1.2700,
            stop_loss_price=1.2750,
            take_profit_price=1.2600,
        )

        assert result["tif"] == "GTC"

    def test_fx_bracket_live_orders_have_gtc(self, handler_live, mock_ib):
        """With IB connection, all 3 orders must have TIF=GTC."""
        handler_live.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        # 3 orders placed: parent + SL + TP
        assert mock_ib.placeOrder.call_count == 3
        for call_args in mock_ib.placeOrder.call_args_list:
            order = call_args[0][1]
            assert order.tif == "GTC"


# =============================================================================
# TEST 3: OCA group correctly set
# =============================================================================

class TestFXBracketOcaGroup:
    """Verify OCA group is correctly assigned."""

    def test_fx_bracket_oca_group_starts_with_bracket_prefix(self, handler_dry):
        """OCA group must start with BRACKET_{pair}_ prefix."""
        result = handler_dry.create_fx_bracket_v2(
            pair="USDJPY",
            direction="BUY",
            lot_size=25_000,
            entry_price=155.00,
            stop_loss_price=154.50,
            take_profit_price=156.00,
        )

        assert result["oca_group"].startswith("BRACKET_USDJPY_")
        assert len(result["oca_group"]) > len("BRACKET_USDJPY_")

    def test_fx_bracket_tracked_in_active_brackets(self, handler_dry):
        """FX bracket must be tracked in the manager's _active_brackets."""
        result = handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        oca = result["oca_group"]
        assert oca in handler_dry._bm._active_brackets
        assert handler_dry._bm._active_brackets[oca]["instrument_type"] == "FX"


# =============================================================================
# TEST 4: Pre-weekend check — unprotected position triggers alert
# =============================================================================

class TestPreWeekendUnprotected:
    """Verify pre-weekend check detects unprotected FX positions."""

    def test_unprotected_fx_position_detected(self, mock_ib):
        """FX position without bracket should be flagged as unprotected."""
        bm = BracketOrderManager(ib_connection=mock_ib)
        handler = FXBracketHandler(bm)

        # Simulate an FX position with no bracket
        pos = MagicMock()
        pos.contract.symbol = "EURUSD"
        pos.contract.secType = "CASH"
        pos.position = 25_000
        mock_ib.positions.return_value = [pos]

        alert_messages = []
        result = handler.pre_weekend_check(
            alert_callback=lambda msg: alert_messages.append(msg)
        )

        assert result["all_protected"] is False
        assert "EURUSD" in result["unprotected_pairs"]
        assert len(alert_messages) == 1
        assert "EURUSD" in alert_messages[0]


# =============================================================================
# TEST 5: Pre-weekend check — all positions protected
# =============================================================================

class TestPreWeekendAllProtected:
    """Verify pre-weekend check passes when all FX positions have brackets."""

    def test_all_fx_positions_protected(self, mock_ib):
        """When all FX positions have active brackets, check must pass."""
        bm = BracketOrderManager(ib_connection=mock_ib)
        handler = FXBracketHandler(bm)

        # Create a bracket for EURUSD
        handler.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        # Simulate matching FX position
        pos = MagicMock()
        pos.contract.symbol = "EURUSD"
        pos.contract.secType = "CASH"
        pos.position = 25_000
        mock_ib.positions.return_value = [pos]

        result = handler.pre_weekend_check()

        assert result["all_protected"] is True
        assert result["unprotected_pairs"] == []
        assert "checked_at" in result


# =============================================================================
# TEST 6: BUY stop_limit = stop_price - 0.0005
# =============================================================================

class TestFXBuyStopLimit:
    """Verify BUY stop limit calculation."""

    def test_buy_stop_limit_below_stop_price(self, handler_dry):
        """BUY: stop_limit_price = stop_loss_price - FX_SL_PIP_OFFSET."""
        sl_price = 1.08000
        result = handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.08500,
            stop_loss_price=sl_price,
            take_profit_price=1.09500,
        )

        expected_limit = round(sl_price - FX_SL_PIP_OFFSET, 5)
        assert result["stop_limit_price"] == expected_limit
        assert result["stop_limit_price"] == round(1.08000 - 0.0005, 5)
        assert result["stop_limit_price"] < result["stop_loss_price"]


# =============================================================================
# TEST 7: SELL stop_limit = stop_price + 0.0005
# =============================================================================

class TestFXSellStopLimit:
    """Verify SELL stop limit calculation."""

    def test_sell_stop_limit_above_stop_price(self, handler_dry):
        """SELL: stop_limit_price = stop_loss_price + FX_SL_PIP_OFFSET."""
        sl_price = 1.09000
        result = handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="SELL",
            lot_size=25_000,
            entry_price=1.08500,
            stop_loss_price=sl_price,
            take_profit_price=1.07500,
        )

        expected_limit = round(sl_price + FX_SL_PIP_OFFSET, 5)
        assert result["stop_limit_price"] == expected_limit
        assert result["stop_limit_price"] == round(1.09000 + 0.0005, 5)
        assert result["stop_limit_price"] > result["stop_loss_price"]


# =============================================================================
# TEST 8: Lot size < 25000 rejected
# =============================================================================

class TestFXMinimumLotSize:
    """Verify that FX lot sizes below IBKR minimum are rejected."""

    def test_lot_size_below_minimum_rejected(self, handler_dry):
        """Lot size < 25,000 must raise BracketOrderError."""
        with pytest.raises(BracketOrderError, match="below IBKR minimum"):
            handler_dry.create_fx_bracket_v2(
                pair="EURUSD",
                direction="BUY",
                lot_size=10_000,
                entry_price=1.0850,
                stop_loss_price=1.0800,
                take_profit_price=1.0950,
            )

    def test_lot_size_exactly_minimum_accepted(self, handler_dry):
        """Lot size == 25,000 must be accepted."""
        result = handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=FX_MIN_LOT,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        assert result["quantity"] == FX_MIN_LOT


# =============================================================================
# TEST 9 (bonus): check_position_bracket helper
# =============================================================================

class TestCheckPositionBracket:
    """Verify the check_position_bracket helper method."""

    def test_pair_with_bracket_returns_true(self, handler_dry):
        """Pair with active bracket should return True."""
        handler_dry.create_fx_bracket_v2(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        assert handler_dry.check_position_bracket("EURUSD") is True

    def test_pair_without_bracket_returns_false(self, handler_dry):
        """Pair without bracket should return False."""
        assert handler_dry.check_position_bracket("GBPUSD") is False
