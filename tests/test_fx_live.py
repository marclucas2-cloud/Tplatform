"""
Tests for FXLiveAdapter -- FX live pipeline module.

Covers:
  - Position sizing (Sharpe-weighted, IBKR lots, margin limits)
  - Spread filter (accept, reject, boundary)
  - Order preparation (LONG, SHORT, spread rejection, pair conversion)
  - Execution (broker call, slippage recording, journal recording)
  - IBKR pair conversion (both directions)
  - Edge cases (zero capital, unknown pair)
  - Sizing report (human-readable output)
  - P&L aggregation
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.fx_live_adapter import (
    AVERAGE_SPREADS_BPS,
    IBKR_MIN_LOT,
    STRATEGY_SHARPES,
    FXLiveAdapter,
)

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def adapter():
    """Standard adapter with $25K capital, 40% FX allocation, 20x FX leverage.

    $25K * 40% = $10K FX margin. At 20x, top strategy gets ~$74K notional = 2 lots.
    All 4 strategies get at least 1 lot (25K units).
    """
    return FXLiveAdapter(
        capital=25_000,
        fx_allocation_pct=0.40,
        max_leverage=20.0,
    )


@pytest.fixture
def mock_broker():
    """Mock broker returning standard responses."""
    broker = MagicMock()
    broker.create_position.return_value = {
        "orderId": "12345",
        "symbol": "EUR.USD",
        "side": "BUY",
        "status": "Filled",
        "qty": "25000",
        "filled_qty": 25000,
        "filled_price": 1.08505,
        "stop_loss": 1.07500,
        "take_profit": 1.10000,
        "bracket": True,
        "paper": True,
        "authorized_by": "fx_live_adapter",
    }
    broker.get_positions.return_value = [
        {
            "symbol": "EUR.USD",
            "qty": 25000,
            "side": "long",
            "avg_entry": 1.08500,
            "market_val": 27125.0,
            "unrealized_pl": 12.50,
        },
        {
            "symbol": "EUR.JPY",
            "qty": 25000,
            "side": "long",
            "avg_entry": 162.500,
            "market_val": 25200.0,
            "unrealized_pl": -5.00,
        },
        {
            "symbol": "AAPL",
            "qty": 10,
            "side": "long",
            "avg_entry": 175.00,
            "market_val": 1800.0,
            "unrealized_pl": 50.0,
        },
    ]
    return broker


@pytest.fixture
def mock_journal():
    """Mock trade journal."""
    return MagicMock()


@pytest.fixture
def mock_slippage():
    """Mock slippage tracker."""
    return MagicMock()


@pytest.fixture
def mock_alert():
    """Mock alert callback."""
    return MagicMock()


@pytest.fixture
def full_adapter(mock_broker, mock_journal, mock_slippage, mock_alert):
    """Adapter with all dependencies mocked."""
    return FXLiveAdapter(
        capital=25_000,
        fx_allocation_pct=0.40,
        max_leverage=20.0,
        broker=mock_broker,
        trade_journal=mock_journal,
        slippage_tracker=mock_slippage,
        alert_callback=mock_alert,
    )


# =============================================================================
# TESTS -- Sizing
# =============================================================================

class TestSizing:
    """Position sizing tests."""

    def test_sizing_returns_all_strategies(self, adapter):
        """calculate_sizing returns an entry for each strategy."""
        sizing = adapter.calculate_sizing()
        assert set(sizing.keys()) == set(STRATEGY_SHARPES.keys())

    def test_sizing_sharpe_weighted(self, adapter):
        """Higher Sharpe strategy gets larger weight."""
        sizing = adapter.calculate_sizing()
        # EURUSD (Sharpe 4.62) should have higher weight than AUDJPY (1.58)
        assert sizing["fx_eurusd_trend"]["weight"] > sizing["fx_audjpy_carry"]["weight"]

    def test_sizing_weights_sum_to_one(self, adapter):
        """Weights should sum to approximately 1.0."""
        sizing = adapter.calculate_sizing()
        total_weight = sum(s["weight"] for s in sizing.values())
        assert abs(total_weight - 1.0) < 0.01

    def test_sizing_units_multiples_of_min_lot(self, adapter):
        """Units must be multiples of IBKR minimum lot (25,000)."""
        sizing = adapter.calculate_sizing()
        for strategy, s in sizing.items():
            assert s["units"] % IBKR_MIN_LOT == 0, (
                f"{strategy}: units={s['units']} not multiple of {IBKR_MIN_LOT}"
            )

    def test_sizing_total_margin_under_35_pct(self, adapter):
        """Total margin should not exceed 35% of capital."""
        sizing = adapter.calculate_sizing()
        total_margin = sum(s["margin_allocated"] for s in sizing.values())
        assert total_margin <= adapter.capital * 0.35 + 1  # +1 for rounding

    def test_sizing_lots_equal_units_div_min_lot(self, adapter):
        """lots * IBKR_MIN_LOT == units for every strategy."""
        sizing = adapter.calculate_sizing()
        for strategy, s in sizing.items():
            assert s["lots"] * IBKR_MIN_LOT == s["units"]

    def test_sizing_zero_capital(self):
        """Zero capital -> all sizing is zero."""
        adapter = FXLiveAdapter(capital=0.0)
        sizing = adapter.calculate_sizing()
        for strategy, s in sizing.items():
            assert s["units"] == 0
            assert s["lots"] == 0
            assert s["margin_allocated"] == 0.0


# =============================================================================
# TESTS -- Spread filter
# =============================================================================

class TestSpreadFilter:
    """Spread filter tests."""

    def test_normal_spread_accepted(self, adapter):
        """Spread within 2x average -> acceptable."""
        result = adapter.check_spread("EURUSD", 1.0)
        assert result["acceptable"] is True
        assert result["current"] == 1.0
        assert result["average"] == AVERAGE_SPREADS_BPS["EURUSD"]

    def test_high_spread_rejected(self, adapter):
        """Spread > 2x average -> rejected."""
        avg = AVERAGE_SPREADS_BPS["EURUSD"]  # 0.8
        result = adapter.check_spread("EURUSD", avg * 2.5)
        assert result["acceptable"] is False
        assert result["ratio"] > 2.0

    def test_spread_at_exactly_2x_accepted(self, adapter):
        """Spread at exactly 2x average -> accepted (boundary)."""
        avg = AVERAGE_SPREADS_BPS["EURUSD"]  # 0.8
        result = adapter.check_spread("EURUSD", avg * 2.0)
        assert result["acceptable"] is True
        assert abs(result["ratio"] - 2.0) < 0.01

    def test_unknown_pair_rejected_fail_closed(self, adapter):
        """Unknown pair with no average data -> rejected (fail-closed)."""
        result = adapter.check_spread("XYZABC", 5.0)
        assert result["acceptable"] is False
        assert result["average"] == 0.0
        assert "No spread data" in result.get("reason", "")


# =============================================================================
# TESTS -- Order preparation
# =============================================================================

class TestOrderPreparation:
    """Order preparation tests."""

    def test_prepare_long_order_valid_spread(self, adapter):
        """Prepare LONG order with valid spread -> ready."""
        order = adapter.prepare_order(
            strategy="fx_eurusd_trend",
            pair="EURUSD",
            direction="BUY",
            signal_price=1.08500,
            stop_loss=1.07500,
            take_profit=1.10000,
            current_spread_bps=0.5,
        )
        assert order["ready"] is True
        assert order["ibkr_pair"] == "EUR.USD"
        assert order["direction"] == "BUY"
        assert order["units"] > 0
        assert order["units"] % IBKR_MIN_LOT == 0
        assert order["order_type"] == "LIMIT"
        assert order["limit_price"] == 1.08500
        assert order["stop_loss"] == 1.07500
        assert order["take_profit"] == 1.10000

    def test_prepare_short_order(self, adapter):
        """Prepare SHORT order -> correct direction."""
        order = adapter.prepare_order(
            strategy="fx_eurgbp_mr",
            pair="EURGBP",
            direction="SELL",
            signal_price=0.85600,
            stop_loss=0.86000,
            take_profit=0.85000,
        )
        assert order["ready"] is True
        assert order["direction"] == "SELL"
        assert order["ibkr_pair"] == "EUR.GBP"

    def test_prepare_order_high_spread_rejected(self, adapter):
        """High spread -> order rejected with reason."""
        avg = AVERAGE_SPREADS_BPS["EURJPY"]  # 1.5
        order = adapter.prepare_order(
            strategy="fx_eurjpy_carry",
            pair="EURJPY",
            direction="BUY",
            signal_price=162.500,
            stop_loss=161.500,
            take_profit=164.000,
            current_spread_bps=avg * 3.0,  # 3x average
        )
        assert order["ready"] is False
        assert "Spread too wide" in order["reason_if_rejected"]
        assert order["units"] == 0

    def test_prepare_order_ibkr_pair_conversion(self, adapter):
        """IBKR pair format correct for all 4 strategies."""
        pairs = {"EURUSD": "EUR.USD", "EURGBP": "EUR.GBP",
                 "EURJPY": "EUR.JPY", "AUDJPY": "AUD.JPY"}
        for pair, expected_ibkr in pairs.items():
            order = adapter.prepare_order(
                strategy=f"fx_{pair.lower()[:6]}_trend",
                pair=pair,
                direction="BUY",
                signal_price=1.0,
                stop_loss=0.99,
                take_profit=1.01,
            )
            assert order["ibkr_pair"] == expected_ibkr

    def test_prepare_order_no_spread_check_when_none(self, adapter):
        """No spread data -> skip spread check, order proceeds."""
        order = adapter.prepare_order(
            strategy="fx_eurusd_trend",
            pair="EURUSD",
            direction="BUY",
            signal_price=1.08500,
            stop_loss=1.07500,
            take_profit=1.10000,
            current_spread_bps=None,
        )
        assert order["ready"] is True


# =============================================================================
# TESTS -- Execution
# =============================================================================

class TestExecution:
    """Order execution tests."""

    def test_execute_order_calls_broker(self, full_adapter, mock_broker):
        """execute_order calls broker.create_position with correct args."""
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }

        result = full_adapter.execute_order(prepared)
        assert result["success"] is True
        assert result["trade_id"].startswith("FX-")
        assert result["fill_price"] > 0

        mock_broker.create_position.assert_called_once_with(
            symbol="EUR.USD",
            direction="BUY",
            qty=25000,
            stop_loss=1.07500,
            take_profit=1.10000,
            _authorized_by="fx_live_adapter",
        )

    def test_execute_records_slippage(self, full_adapter, mock_slippage):
        """execute_order records fill in slippage tracker."""
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }

        full_adapter.execute_order(prepared)
        mock_slippage.record_fill.assert_called_once()

        call_kwargs = mock_slippage.record_fill.call_args
        assert call_kwargs[1]["instrument_type"] == "FX"
        assert call_kwargs[1]["instrument"] == "EUR.USD"

    def test_execute_records_in_journal(self, full_adapter, mock_journal):
        """execute_order records trade in journal."""
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.GBP",
            "direction": "SELL",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 0.85600,
            "stop_loss": 0.86000,
            "take_profit": 0.85000,
            "margin_required": 750.0,
            "reason_if_rejected": "",
        }

        full_adapter.execute_order(prepared)
        mock_journal.record_trade_open.assert_called_once()

        call_kwargs = mock_journal.record_trade_open.call_args
        assert call_kwargs[1]["direction"] == "SHORT"
        assert call_kwargs[1]["instrument"] == "EUR.GBP"
        assert call_kwargs[1]["instrument_type"] == "FX"

    def test_execute_not_ready_returns_failure(self, full_adapter):
        """execute_order with not-ready order returns failure."""
        prepared = {
            "ready": False,
            "reason_if_rejected": "Spread too wide",
        }
        result = full_adapter.execute_order(prepared)
        assert result["success"] is False
        assert "Spread too wide" in result["error"]

    def test_execute_no_broker_returns_failure(self):
        """execute_order without broker returns failure."""
        adapter = FXLiveAdapter(capital=10_000, broker=None)
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }
        result = adapter.execute_order(prepared)
        assert result["success"] is False
        assert "No broker" in result["error"]

    def test_execute_broker_exception_handled(self, full_adapter, mock_broker):
        """Broker exception is caught and returned as error."""
        mock_broker.create_position.side_effect = Exception("Connection lost")
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }
        result = full_adapter.execute_order(prepared)
        assert result["success"] is False
        assert "Connection lost" in result["error"]


# =============================================================================
# TESTS -- Pair conversion
# =============================================================================

class TestPairConversion:
    """IBKR pair format conversion tests."""

    def test_to_ibkr_pair_eurusd(self):
        assert FXLiveAdapter.to_ibkr_pair("EURUSD") == "EUR.USD"

    def test_to_ibkr_pair_eurgbp(self):
        assert FXLiveAdapter.to_ibkr_pair("EURGBP") == "EUR.GBP"

    def test_to_ibkr_pair_eurjpy(self):
        assert FXLiveAdapter.to_ibkr_pair("EURJPY") == "EUR.JPY"

    def test_to_ibkr_pair_audjpy(self):
        assert FXLiveAdapter.to_ibkr_pair("AUDJPY") == "AUD.JPY"

    def test_from_ibkr_pair_eurusd(self):
        assert FXLiveAdapter.from_ibkr_pair("EUR.USD") == "EURUSD"

    def test_from_ibkr_pair_eurgbp(self):
        assert FXLiveAdapter.from_ibkr_pair("EUR.GBP") == "EURGBP"

    def test_to_ibkr_pair_unknown_passthrough(self):
        """Unknown pair passes through unchanged."""
        assert FXLiveAdapter.to_ibkr_pair("XYZABC") == "XYZABC"

    def test_from_ibkr_pair_unknown_removes_dots(self):
        """Unknown IBKR pair removes dots."""
        assert FXLiveAdapter.from_ibkr_pair("XYZ.ABC") == "XYZABC"


# =============================================================================
# TESTS -- Positions and P&L
# =============================================================================

class TestPositionsAndPnL:
    """Position and P&L query tests."""

    def test_get_fx_positions_filters_fx_only(self, full_adapter, mock_broker):
        """get_fx_positions returns only FX positions (not AAPL)."""
        positions = full_adapter.get_fx_positions()
        symbols = [p["symbol"] for p in positions]
        assert "EUR.USD" in symbols
        assert "EUR.JPY" in symbols
        assert "AAPL" not in symbols
        assert len(positions) == 2

    def test_get_fx_pnl_aggregates(self, full_adapter):
        """get_fx_pnl returns correct totals."""
        pnl = full_adapter.get_fx_pnl()
        assert pnl["positions"] == 2
        assert pnl["total_unrealized_pl"] == pytest.approx(7.50, abs=0.01)  # 12.50 - 5.00
        assert "EUR.USD" in pnl["by_pair"]
        assert "EUR.JPY" in pnl["by_pair"]

    def test_get_fx_positions_no_broker(self):
        """No broker -> empty list."""
        adapter = FXLiveAdapter(capital=10_000, broker=None)
        assert adapter.get_fx_positions() == []

    def test_get_fx_pnl_no_broker(self):
        """No broker -> zero P&L."""
        adapter = FXLiveAdapter(capital=10_000, broker=None)
        pnl = adapter.get_fx_pnl()
        assert pnl["total_unrealized_pl"] == 0.0
        assert pnl["positions"] == 0


# =============================================================================
# TESTS -- Sizing report
# =============================================================================

class TestSizingReport:
    """Sizing report output tests."""

    def test_sizing_report_is_string(self, adapter):
        """get_sizing_report returns a non-empty string."""
        report = adapter.get_sizing_report()
        assert isinstance(report, str)
        assert len(report) > 100

    def test_sizing_report_contains_strategies(self, adapter):
        """Report mentions all 4 strategies."""
        report = adapter.get_sizing_report()
        for strategy in STRATEGY_SHARPES:
            assert strategy in report

    def test_sizing_report_contains_totals(self, adapter):
        """Report contains TOTAL line."""
        report = adapter.get_sizing_report()
        assert "TOTAL" in report


# =============================================================================
# TESTS -- Edge cases
# =============================================================================

class TestEdgeCases:
    """Edge case tests."""

    def test_zero_allocation_pct(self):
        """Zero FX allocation -> no sizing."""
        adapter = FXLiveAdapter(capital=10_000, fx_allocation_pct=0.0)
        sizing = adapter.calculate_sizing()
        for s in sizing.values():
            assert s["units"] == 0

    def test_very_small_capital_no_lots(self):
        """Capital too small for any lots -> zero units."""
        adapter = FXLiveAdapter(capital=100, fx_allocation_pct=0.40, max_leverage=1.5)
        sizing = adapter.calculate_sizing()
        for s in sizing.values():
            assert s["units"] == 0
            assert s["lots"] == 0

    def test_custom_max_spread_ratio(self):
        """Custom max_spread_ratio is respected."""
        adapter = FXLiveAdapter(capital=10_000, max_spread_ratio=1.5)
        avg = AVERAGE_SPREADS_BPS["EURUSD"]
        # At 1.6x -> rejected with 1.5x limit
        result = adapter.check_spread("EURUSD", avg * 1.6)
        assert result["acceptable"] is False

    def test_alert_callback_on_execution(self, full_adapter, mock_alert):
        """Alert callback is called on successful execution."""
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }
        full_adapter.execute_order(prepared)
        mock_alert.assert_called_once()
        call_args = mock_alert.call_args[0]
        assert "FX trade executed" in call_args[0]

    def test_alert_callback_on_failure(self, mock_journal, mock_slippage, mock_alert):
        """Alert callback is called with critical level on broker failure."""
        broker = MagicMock()
        broker.create_position.side_effect = Exception("Timeout")
        adapter = FXLiveAdapter(
            capital=10_000, broker=broker,
            trade_journal=mock_journal,
            slippage_tracker=mock_slippage,
            alert_callback=mock_alert,
        )
        prepared = {
            "ready": True,
            "ibkr_pair": "EUR.USD",
            "direction": "BUY",
            "units": 25000,
            "order_type": "LIMIT",
            "limit_price": 1.08500,
            "stop_loss": 1.07500,
            "take_profit": 1.10000,
            "margin_required": 900.0,
            "reason_if_rejected": "",
        }
        adapter.execute_order(prepared)
        mock_alert.assert_called_once()
        call_args = mock_alert.call_args[0]
        assert "FAILED" in call_args[0]
        assert call_args[1] == "critical"
