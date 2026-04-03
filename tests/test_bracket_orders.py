"""
Tests for IBKR Bracket Order Manager.

All ib_insync interactions are mocked — no real IBKR connection needed.
Tests cover:
  - Price validation (LONG/SHORT, invalid combos)
  - Bracket creation (equity, FX, futures)
  - Modification (SL, TP)
  - Cancellation
  - Integrity verification
  - Edge cases (zero qty, negative prices, etc.)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.broker.ibkr_bracket import BracketOrderError, BracketOrderManager


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

    # Track placed trades so openTrades() returns them (needed for post-verification)
    _placed_trades = []
    _next_order_id = [1000]

    def _place_order(contract, order):
        if not hasattr(order, 'orderId') or order.orderId in (None, 0):
            order.orderId = _next_order_id[0]
            _next_order_id[0] += 1
        trade = MagicMock()
        trade.order = order
        trade.contract = contract
        trade.orderStatus = MagicMock()
        trade.orderStatus.status = "Submitted"
        _placed_trades.append(trade)
        return trade

    ib.placeOrder.side_effect = _place_order
    ib.openTrades.side_effect = lambda: list(_placed_trades)
    return ib


@pytest.fixture
def manager(mock_ib):
    """Create a BracketOrderManager with mocked IB connection."""
    return BracketOrderManager(ib_connection=mock_ib)


@pytest.fixture
def manager_no_ib():
    """Create a BracketOrderManager without IB connection (for validation tests)."""
    return BracketOrderManager(ib_connection=None)


# =============================================================================
# TEST: Price validation — LONG
# =============================================================================

class TestPriceValidationLong:
    """Validate price constraints for LONG (BUY) brackets."""

    def test_valid_long_prices(self, manager_no_ib):
        """LONG: SL < entry < TP should pass validation."""
        # _validate_prices should not raise
        manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=95.0, take_profit=110.0)

    def test_long_sl_above_entry_rejected(self, manager_no_ib):
        """LONG: SL > entry must be rejected."""
        with pytest.raises(BracketOrderError, match="LONG bracket.*stop_loss.*must be BELOW"):
            manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=105.0, take_profit=110.0)

    def test_long_sl_equal_entry_rejected(self, manager_no_ib):
        """LONG: SL == entry must be rejected."""
        with pytest.raises(BracketOrderError, match="LONG bracket.*stop_loss.*must be BELOW"):
            manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=100.0, take_profit=110.0)

    def test_long_tp_below_entry_rejected(self, manager_no_ib):
        """LONG: TP < entry must be rejected."""
        with pytest.raises(BracketOrderError, match="LONG bracket.*take_profit.*must be ABOVE"):
            manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=95.0, take_profit=90.0)


# =============================================================================
# TEST: Price validation — SHORT
# =============================================================================

class TestPriceValidationShort:
    """Validate price constraints for SHORT (SELL) brackets."""

    def test_valid_short_prices(self, manager_no_ib):
        """SHORT: TP < entry < SL should pass validation."""
        manager_no_ib._validate_prices("SELL", entry=100.0, stop_loss=105.0, take_profit=90.0)

    def test_short_sl_below_entry_rejected(self, manager_no_ib):
        """SHORT: SL < entry must be rejected."""
        with pytest.raises(BracketOrderError, match="SHORT bracket.*stop_loss.*must be ABOVE"):
            manager_no_ib._validate_prices("SELL", entry=100.0, stop_loss=95.0, take_profit=90.0)

    def test_short_tp_above_entry_rejected(self, manager_no_ib):
        """SHORT: TP > entry must be rejected."""
        with pytest.raises(BracketOrderError, match="SHORT bracket.*take_profit.*must be BELOW"):
            manager_no_ib._validate_prices("SELL", entry=100.0, stop_loss=105.0, take_profit=110.0)


# =============================================================================
# TEST: Create equity bracket
# =============================================================================

class TestCreateEquityBracket:
    """Test equity bracket order creation."""

    def test_equity_bracket_returns_3_order_ids_and_oca(self, manager, mock_ib):
        """Equity bracket must return parent, SL, TP order IDs + OCA group."""
        result = manager.create_equity_bracket(
            symbol="AAPL",
            direction="BUY",
            quantity=10,
            entry_price=150.00,
            stop_loss_price=145.00,
            take_profit_price=160.00,
        )

        assert "parent_order_id" in result
        assert "sl_order_id" in result
        assert "tp_order_id" in result
        assert "oca_group" in result
        assert result["oca_group"].startswith("BRACKET_AAPL_")
        assert result["status"] == "SUBMITTED"
        assert result["symbol"] == "AAPL"
        assert result["direction"] == "BUY"
        assert result["quantity"] == 10

        # 3 orders placed: parent + SL + TP
        assert mock_ib.placeOrder.call_count == 3

    def test_equity_bracket_sell_direction(self, manager, mock_ib):
        """Equity SHORT bracket (SELL) must work with correct price ordering."""
        result = manager.create_equity_bracket(
            symbol="TSLA",
            direction="SELL",
            quantity=5,
            entry_price=200.00,
            stop_loss_price=210.00,
            take_profit_price=180.00,
        )

        assert result["direction"] == "SELL"
        assert result["quantity"] == 5
        assert result["symbol"] == "TSLA"
        assert mock_ib.placeOrder.call_count == 3


# =============================================================================
# TEST: Create FX bracket
# =============================================================================

class TestCreateFXBracket:
    """Test FX bracket order creation."""

    def test_fx_bracket_correct_lot_handling(self, manager, mock_ib):
        """FX bracket with valid lot size should succeed."""
        result = manager.create_fx_bracket(
            pair="EURUSD",
            direction="BUY",
            lot_size=25_000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            take_profit_price=1.0950,
        )

        assert result["instrument_type"] == "FX"
        assert result["symbol"] == "EURUSD"
        assert result["quantity"] == 25_000
        assert mock_ib.placeOrder.call_count == 3

    def test_fx_bracket_below_minimum_lot_rejected(self, manager):
        """FX lot size below 25,000 must be rejected."""
        with pytest.raises(BracketOrderError, match="below IBKR minimum"):
            manager.create_fx_bracket(
                pair="EURUSD",
                direction="BUY",
                lot_size=10_000,
                entry_price=1.0850,
                stop_loss_price=1.0800,
                take_profit_price=1.0950,
            )


# =============================================================================
# TEST: Create futures bracket
# =============================================================================

class TestCreateFuturesBracket:
    """Test futures bracket order creation."""

    def test_futures_bracket_correct_contract_type(self, manager, mock_ib):
        """Futures bracket for MES should use FUTURES instrument type."""
        result = manager.create_futures_bracket(
            symbol="MES",
            direction="BUY",
            contracts=1,
            entry_price=5200.00,
            stop_loss_price=5180.00,
            take_profit_price=5240.00,
        )

        assert result["instrument_type"] == "FUTURES"
        assert result["symbol"] == "MES"
        assert result["quantity"] == 1
        assert mock_ib.placeOrder.call_count == 3

    def test_futures_bracket_unknown_symbol_rejected(self, manager):
        """Unknown futures symbol must be rejected."""
        with pytest.raises(BracketOrderError, match="Unknown futures symbol"):
            manager.create_futures_bracket(
                symbol="UNKNOWN",
                direction="BUY",
                contracts=1,
                entry_price=100.0,
                stop_loss_price=95.0,
                take_profit_price=110.0,
            )

    def test_futures_bracket_non_integer_contracts_rejected(self, manager):
        """Futures contracts must be integers."""
        with pytest.raises(BracketOrderError, match="positive integer"):
            manager.create_futures_bracket(
                symbol="MES",
                direction="BUY",
                contracts=1.5,
                entry_price=5200.0,
                stop_loss_price=5180.0,
                take_profit_price=5240.0,
            )


# =============================================================================
# TEST: Modify stop loss
# =============================================================================

class TestModifyStopLoss:
    """Test modifying SL on an existing bracket."""

    def test_modify_sl_on_existing_bracket(self, manager, mock_ib):
        """Modify SL should update the stop price and call placeOrder."""
        # Create a bracket first
        result = manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )
        oca = result["oca_group"]
        sl_order_id = result["sl_order_id"]

        # Setup mock for the SL order in open trades
        sl_trade = MagicMock()
        sl_trade.order.orderId = sl_order_id
        sl_trade.order.auxPrice = 145.0
        sl_trade.contract = MagicMock()
        mock_ib.openTrades.side_effect = lambda: [sl_trade]

        # Reset call count from bracket creation
        mock_ib.placeOrder.reset_mock()

        updated = manager.modify_stop_loss(oca, new_stop_price=143.0)

        assert updated["stop_loss_price"] == 143.0
        assert sl_trade.order.auxPrice == 143.0
        mock_ib.placeOrder.assert_called_once()


# =============================================================================
# TEST: Modify take profit
# =============================================================================

class TestModifyTakeProfit:
    """Test modifying TP on an existing bracket."""

    def test_modify_tp_on_existing_bracket(self, manager, mock_ib):
        """Modify TP should update the limit price and call placeOrder."""
        result = manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )
        oca = result["oca_group"]
        tp_order_id = result["tp_order_id"]

        tp_trade = MagicMock()
        tp_trade.order.orderId = tp_order_id
        tp_trade.order.lmtPrice = 160.0
        tp_trade.contract = MagicMock()
        mock_ib.openTrades.side_effect = lambda: [tp_trade]

        mock_ib.placeOrder.reset_mock()

        updated = manager.modify_take_profit(oca, new_tp_price=165.0)

        assert updated["take_profit_price"] == 165.0
        assert tp_trade.order.lmtPrice == 165.0
        mock_ib.placeOrder.assert_called_once()


# =============================================================================
# TEST: Cancel bracket
# =============================================================================

class TestCancelBracket:
    """Test bracket cancellation."""

    def test_cancel_bracket_cancels_all_3_orders(self, manager, mock_ib):
        """Cancelling a bracket should attempt to cancel parent + SL + TP."""
        result = manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )
        oca = result["oca_group"]

        # Simulate 3 open trades matching the bracket
        trades = []
        for oid in [result["parent_order_id"], result["sl_order_id"], result["tp_order_id"]]:
            t = MagicMock()
            t.order.orderId = oid
            trades.append(t)
        mock_ib.openTrades.side_effect = lambda: list(trades)

        cancel_result = manager.cancel_bracket(oca)

        assert cancel_result["status"] == "CANCELLED"
        assert cancel_result["cancelled_orders"] == 3
        assert mock_ib.cancelOrder.call_count == 3


# =============================================================================
# TEST: Get active brackets
# =============================================================================

class TestGetActiveBrackets:
    """Test listing active brackets."""

    def test_get_active_brackets_list(self, manager, mock_ib):
        """Active brackets should be listed with correct fields."""
        manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )
        manager.create_equity_bracket(
            symbol="MSFT", direction="SELL", quantity=5,
            entry_price=400.0, stop_loss_price=410.0, take_profit_price=380.0,
        )

        active = manager.get_active_brackets()
        assert len(active) == 2

        symbols = {b["symbol"] for b in active}
        assert symbols == {"AAPL", "MSFT"}

        for b in active:
            assert "bracket_id" in b
            assert "direction" in b
            assert "qty" in b
            assert "sl" in b
            assert "tp" in b
            assert b["status"] == "SUBMITTED"


# =============================================================================
# TEST: Verify bracket integrity — all protected
# =============================================================================

class TestBracketIntegrityAllProtected:
    """Test bracket integrity when all positions are protected."""

    def test_all_positions_protected(self, manager, mock_ib):
        """When all positions have brackets, all_protected should be True."""
        # Create bracket for AAPL
        manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )

        # Simulate AAPL position
        pos = MagicMock()
        pos.contract.symbol = "AAPL"
        pos.position = 10
        mock_ib.positions.return_value = [pos]

        result = manager.verify_bracket_integrity()

        assert result["all_protected"] is True
        assert result["unprotected"] == []
        assert result["total_positions"] == 1


# =============================================================================
# TEST: Verify bracket integrity — unprotected position
# =============================================================================

class TestBracketIntegrityUnprotected:
    """Test bracket integrity detection of unprotected positions."""

    def test_detect_unprotected_position(self, manager, mock_ib):
        """Unprotected position should be flagged as a critical risk."""
        # Create bracket for AAPL only
        manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )

        # Simulate positions: AAPL (protected) + TSLA (unprotected)
        pos_aapl = MagicMock()
        pos_aapl.contract.symbol = "AAPL"
        pos_aapl.position = 10

        pos_tsla = MagicMock()
        pos_tsla.contract.symbol = "TSLA"
        pos_tsla.position = 5

        mock_ib.positions.return_value = [pos_aapl, pos_tsla]

        result = manager.verify_bracket_integrity()

        assert result["all_protected"] is False
        assert "TSLA" in result["unprotected"]
        assert "AAPL" not in result["unprotected"]
        assert result["total_positions"] == 2


# =============================================================================
# TEST: Market parent order
# =============================================================================

class TestMarketParentOrder:
    """Test bracket with MARKET parent order type."""

    def test_bracket_with_market_parent(self, manager, mock_ib):
        """Bracket with MARKET parent order should work correctly."""
        result = manager.create_bracket_order(
            symbol="SPY",
            direction="BUY",
            quantity=50,
            entry_price=500.00,
            stop_loss_price=495.00,
            take_profit_price=510.00,
            instrument_type="EQUITY",
            order_type="MARKET",
            tif="DAY",
        )

        assert result["status"] == "SUBMITTED"
        assert result["symbol"] == "SPY"
        assert mock_ib.placeOrder.call_count == 3

        # Verify the parent order was a MarketOrder (first call)
        first_call_order = mock_ib.placeOrder.call_args_list[0][0][1]
        assert first_call_order.tif == "DAY"


# =============================================================================
# TEST: GTC time-in-force
# =============================================================================

class TestGTCTimeInForce:
    """Test bracket with GTC time-in-force."""

    def test_bracket_with_gtc_tif(self, manager, mock_ib):
        """Bracket with GTC TIF should set TIF on all orders."""
        result = manager.create_bracket_order(
            symbol="QQQ",
            direction="BUY",
            quantity=20,
            entry_price=440.0,
            stop_loss_price=435.0,
            take_profit_price=450.0,
            instrument_type="EQUITY",
            order_type="LIMIT",
            tif="GTC",
        )

        assert result["status"] == "SUBMITTED"
        assert result["tif"] == "GTC"

        # All 3 orders should have tif="GTC"
        for call_args in mock_ib.placeOrder.call_args_list:
            order = call_args[0][1]  # Second positional arg is the order
            assert order.tif == "GTC"


# =============================================================================
# TEST: Edge case — zero quantity
# =============================================================================

class TestEdgeZeroQuantity:
    """Test that zero quantity is rejected."""

    def test_zero_quantity_rejected(self, manager_no_ib):
        """Quantity of 0 must be rejected."""
        with pytest.raises(BracketOrderError, match="must be positive"):
            manager_no_ib.create_bracket_order(
                symbol="AAPL",
                direction="BUY",
                quantity=0,
                entry_price=150.0,
                stop_loss_price=145.0,
                take_profit_price=160.0,
            )


# =============================================================================
# TEST: Edge case — negative prices
# =============================================================================

class TestEdgeNegativePrices:
    """Test that negative prices are rejected."""

    def test_negative_entry_price_rejected(self, manager_no_ib):
        """Negative entry price must be rejected."""
        with pytest.raises(BracketOrderError, match="Entry price must be positive"):
            manager_no_ib._validate_prices("BUY", entry=-100.0, stop_loss=95.0, take_profit=110.0)

    def test_negative_stop_loss_rejected(self, manager_no_ib):
        """Negative stop loss must be rejected."""
        with pytest.raises(BracketOrderError, match="Stop loss price must be positive"):
            manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=-5.0, take_profit=110.0)

    def test_negative_take_profit_rejected(self, manager_no_ib):
        """Negative take profit must be rejected."""
        with pytest.raises(BracketOrderError, match="Take profit price must be positive"):
            manager_no_ib._validate_prices("BUY", entry=100.0, stop_loss=95.0, take_profit=-10.0)


# =============================================================================
# TEST: Edge case — no IB connection
# =============================================================================

class TestEdgeNoConnection:
    """Test behavior when no IB connection is available."""

    def test_create_bracket_without_connection_raises(self, manager_no_ib):
        """Creating a bracket without IB connection should raise error."""
        with pytest.raises(BracketOrderError, match="No IB connection"):
            manager_no_ib.create_bracket_order(
                symbol="AAPL",
                direction="BUY",
                quantity=10,
                entry_price=150.0,
                stop_loss_price=145.0,
                take_profit_price=160.0,
            )

    def test_verify_integrity_without_connection_raises(self, manager_no_ib):
        """Integrity check without IB connection should raise error."""
        with pytest.raises(BracketOrderError, match="No IB connection"):
            manager_no_ib.verify_bracket_integrity()


# =============================================================================
# TEST: Edge case — bracket not found
# =============================================================================

class TestEdgeBracketNotFound:
    """Test error handling when bracket ID is not found."""

    def test_modify_sl_unknown_bracket_raises(self, manager, mock_ib):
        """Modifying SL on unknown bracket should raise error."""
        with pytest.raises(BracketOrderError, match="not found"):
            manager.modify_stop_loss("NONEXISTENT_BRACKET", 100.0)

    def test_cancel_unknown_bracket_raises(self, manager, mock_ib):
        """Cancelling unknown bracket should raise error."""
        with pytest.raises(BracketOrderError, match="not found"):
            manager.cancel_bracket("NONEXISTENT_BRACKET")


# =============================================================================
# TEST: Cancelled brackets not in active list
# =============================================================================

class TestCancelledBracketsExcluded:
    """Test that cancelled brackets are excluded from active list."""

    def test_cancelled_bracket_not_in_active_list(self, manager, mock_ib):
        """After cancellation, bracket should not appear in active list."""
        result = manager.create_equity_bracket(
            symbol="AAPL", direction="BUY", quantity=10,
            entry_price=150.0, stop_loss_price=145.0, take_profit_price=160.0,
        )
        oca = result["oca_group"]

        # Cancel it
        mock_ib.openTrades.side_effect = lambda: []
        manager.cancel_bracket(oca)

        active = manager.get_active_brackets()
        assert len(active) == 0


# =============================================================================
# TEST: Invalid instrument type
# =============================================================================

class TestInvalidInstrumentType:
    """Test that invalid instrument types are rejected."""

    def test_invalid_instrument_type_rejected(self, manager_no_ib):
        """Unknown instrument type must be rejected."""
        with pytest.raises(BracketOrderError, match="Invalid instrument_type"):
            manager_no_ib.create_bracket_order(
                symbol="AAPL",
                direction="BUY",
                quantity=10,
                entry_price=150.0,
                stop_loss_price=145.0,
                take_profit_price=160.0,
                instrument_type="CRYPTO",
            )
