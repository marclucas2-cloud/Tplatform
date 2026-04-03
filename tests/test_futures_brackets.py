"""
Tests for HARDEN-004: FuturesBracketHandler — Futures-specific bracket logic.

Validates:
  - MCL bracket with tick size 0.01, buffer 0.02
  - MES bracket with tick size 0.25, buffer 1.00
  - Maintenance margin check: alert if cash < maintenance * 1.2
  - Futures bracket TIF GTC

All ib_insync interactions are mocked — no real IBKR connection needed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.broker.ibkr_bracket import (
    FUTURES_MAINTENANCE_MARGIN,
    FUTURES_SL_BUFFERS,
    FUTURES_TICK_SIZES,
    BracketOrderError,
    BracketOrderManager,
    FuturesBracketHandler,
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

    _next_order_id = [3000]

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
    """FuturesBracketHandler with mocked IB connection."""
    bm = BracketOrderManager(ib_connection=mock_ib)
    return FuturesBracketHandler(bm)


@pytest.fixture
def handler_dry():
    """FuturesBracketHandler without IB connection (dry-run mode)."""
    bm = BracketOrderManager(ib_connection=None)
    return FuturesBracketHandler(bm)


# =============================================================================
# TEST 1: MCL bracket with tick size 0.01, buffer 0.02
# =============================================================================

class TestMCLBracket:
    """Verify MCL bracket uses correct tick size and buffer."""

    def test_mcl_tick_size(self, handler_dry):
        """MCL tick size must be 0.01."""
        assert handler_dry.get_tick_size("MCL") == 0.01

    def test_mcl_buffer(self, handler_dry):
        """MCL buffer must be 0.02 (2 ticks)."""
        assert handler_dry.get_buffer("MCL") == 0.02

    def test_mcl_buy_stop_limit_includes_buffer(self, handler_dry):
        """MCL BUY: stop_limit = stop_price - 0.02."""
        sl_price = 68.50
        result = handler_dry.create_futures_bracket_v2(
            symbol="MCL",
            direction="BUY",
            contracts=1,
            entry_price=70.00,
            stop_loss_price=sl_price,
            take_profit_price=72.00,
        )

        expected_limit = round(sl_price - FUTURES_SL_BUFFERS["MCL"], 2)
        assert result["stop_limit_price"] == expected_limit
        assert result["tick_size"] == 0.01
        assert result["buffer"] == 0.02
        assert result["order_type_sl"] == "STP_LMT"

    def test_mcl_sell_stop_limit_includes_buffer(self, handler_dry):
        """MCL SELL: stop_limit = stop_price + 0.02."""
        sl_price = 72.00
        result = handler_dry.create_futures_bracket_v2(
            symbol="MCL",
            direction="SELL",
            contracts=1,
            entry_price=70.00,
            stop_loss_price=sl_price,
            take_profit_price=68.00,
        )

        expected_limit = round(sl_price + FUTURES_SL_BUFFERS["MCL"], 2)
        assert result["stop_limit_price"] == expected_limit


# =============================================================================
# TEST 2: MES bracket with tick size 0.25, buffer 1.00
# =============================================================================

class TestMESBracket:
    """Verify MES bracket uses correct tick size and buffer."""

    def test_mes_tick_size(self, handler_dry):
        """MES tick size must be 0.25."""
        assert handler_dry.get_tick_size("MES") == 0.25

    def test_mes_buffer(self, handler_dry):
        """MES buffer must be 1.00 (4 points)."""
        assert handler_dry.get_buffer("MES") == 1.00

    def test_mes_buy_stop_limit_includes_buffer(self, handler_dry):
        """MES BUY: stop_limit = stop_price - 1.00."""
        sl_price = 5180.00
        result = handler_dry.create_futures_bracket_v2(
            symbol="MES",
            direction="BUY",
            contracts=2,
            entry_price=5200.00,
            stop_loss_price=sl_price,
            take_profit_price=5240.00,
        )

        expected_limit = round(sl_price - FUTURES_SL_BUFFERS["MES"], 2)
        assert result["stop_limit_price"] == expected_limit
        assert result["tick_size"] == 0.25
        assert result["buffer"] == 1.00
        assert result["quantity"] == 2

    def test_mes_sell_stop_limit_includes_buffer(self, handler_dry):
        """MES SELL: stop_limit = stop_price + 1.00."""
        sl_price = 5220.00
        result = handler_dry.create_futures_bracket_v2(
            symbol="MES",
            direction="SELL",
            contracts=1,
            entry_price=5200.00,
            stop_loss_price=sl_price,
            take_profit_price=5180.00,
        )

        expected_limit = round(sl_price + FUTURES_SL_BUFFERS["MES"], 2)
        assert result["stop_limit_price"] == expected_limit


# =============================================================================
# TEST 3: Maintenance margin check
# =============================================================================

class TestMaintenanceMarginCheck:
    """Verify maintenance margin check before overnight session."""

    def test_alert_when_cash_below_maintenance_threshold(self, handler_dry):
        """Alert if available cash < maintenance_margin * qty * 1.2."""
        # Create an MES bracket (maint = 1260)
        handler_dry.create_futures_bracket_v2(
            symbol="MES",
            direction="BUY",
            contracts=2,
            entry_price=5200.00,
            stop_loss_price=5180.00,
            take_profit_price=5240.00,
        )

        # Required: 1260 * 2 * 1.2 = 3024
        # Provide only 2000 — should trigger warning
        alert_messages = []
        result = handler_dry.pre_maintenance_check(
            available_cash=2000.0,
            alert_callback=lambda msg: alert_messages.append(msg),
        )

        assert result["all_covered"] is False
        assert len(result["warnings"]) >= 1
        assert "MES" in result["warnings"][0]
        assert len(alert_messages) == 1

    def test_no_alert_when_cash_sufficient(self, handler_dry):
        """No warning if available cash > maintenance * qty * 1.2."""
        handler_dry.create_futures_bracket_v2(
            symbol="MES",
            direction="BUY",
            contracts=1,
            entry_price=5200.00,
            stop_loss_price=5180.00,
            take_profit_price=5240.00,
        )

        # Required: 1260 * 1 * 1.2 = 1512
        # Provide 5000 — should be fine
        result = handler_dry.pre_maintenance_check(available_cash=5000.0)

        assert result["all_covered"] is True
        assert result["warnings"] == []
        assert len(result["details"]) == 1
        assert result["details"][0]["covered"] is True

    def test_margin_check_details_include_symbol_and_amounts(self, handler_dry):
        """Margin check details must include symbol, margin, and required amounts."""
        handler_dry.create_futures_bracket_v2(
            symbol="MCL",
            direction="SELL",
            contracts=3,
            entry_price=70.00,
            stop_loss_price=72.00,
            take_profit_price=68.00,
        )

        # Required: 540 * 3 * 1.2 = 1944
        result = handler_dry.pre_maintenance_check(available_cash=1000.0)

        detail = result["details"][0]
        assert detail["symbol"] == "MCL"
        assert detail["quantity"] == 3
        assert detail["maintenance_margin"] == FUTURES_MAINTENANCE_MARGIN["MCL"]
        assert detail["required_with_buffer"] == 540 * 3 * 1.2
        assert detail["covered"] is False


# =============================================================================
# TEST 4: Futures bracket TIF GTC
# =============================================================================

class TestFuturesBracketTifGtc:
    """Verify futures brackets use GTC time-in-force for overnight survival."""

    def test_futures_bracket_tif_is_gtc(self, handler_dry):
        """Futures bracket TIF must be GTC."""
        result = handler_dry.create_futures_bracket_v2(
            symbol="MNQ",
            direction="BUY",
            contracts=1,
            entry_price=18500.00,
            stop_loss_price=18450.00,
            take_profit_price=18600.00,
        )

        assert result["tif"] == "GTC"

    def test_futures_bracket_live_orders_have_gtc(self, handler_live, mock_ib):
        """With IB connection, all 3 futures orders must have TIF=GTC."""
        handler_live.create_futures_bracket_v2(
            symbol="MES",
            direction="BUY",
            contracts=1,
            entry_price=5200.00,
            stop_loss_price=5180.00,
            take_profit_price=5240.00,
        )

        assert mock_ib.placeOrder.call_count == 3
        for call_args in mock_ib.placeOrder.call_args_list:
            order = call_args[0][1]
            assert order.tif == "GTC"


# =============================================================================
# TEST 5 (bonus): Unknown futures symbol rejected
# =============================================================================

class TestFuturesUnknownSymbol:
    """Verify that unknown futures symbols are rejected."""

    def test_unknown_symbol_rejected(self, handler_dry):
        """Unknown futures symbol must raise BracketOrderError."""
        with pytest.raises(BracketOrderError, match="Unknown futures symbol"):
            handler_dry.create_futures_bracket_v2(
                symbol="UNKNOWN",
                direction="BUY",
                contracts=1,
                entry_price=100.0,
                stop_loss_price=95.0,
                take_profit_price=110.0,
            )


# =============================================================================
# TEST 6 (bonus): Futures bracket tracked in active brackets
# =============================================================================

class TestFuturesBracketTracked:
    """Verify futures brackets are tracked in the manager."""

    def test_futures_bracket_in_active_brackets(self, handler_dry):
        """Futures bracket must appear in manager's _active_brackets."""
        result = handler_dry.create_futures_bracket_v2(
            symbol="MGC",
            direction="BUY",
            contracts=1,
            entry_price=2300.00,
            stop_loss_price=2290.00,
            take_profit_price=2320.00,
        )

        oca = result["oca_group"]
        assert oca in handler_dry._bm._active_brackets
        stored = handler_dry._bm._active_brackets[oca]
        assert stored["instrument_type"] == "FUTURES"
        assert stored["tick_size"] == FUTURES_TICK_SIZES["MGC"]
        assert stored["buffer"] == FUTURES_SL_BUFFERS["MGC"]
