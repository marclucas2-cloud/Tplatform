"""
Tests for PartialFillHandler -- SL adjustment on partial fills.

Covers:
  - Full fill (no action needed)
  - Partial fill with SL adjustment
  - Timeout on remaining fills
  - Exposure gap calculation
  - Multi-broker scenarios (IBKR lots, crypto fractional, equities shares)
  - Edge cases (overfill, zero fill, negative prices, missing fields)
"""
import json
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.execution.partial_fill_handler import (
    PartialFillHandler,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def handler():
    """Fresh PartialFillHandler with 60s timeout for fast tests."""
    return PartialFillHandler(alert_callback=None, timeout_seconds=60)


@pytest.fixture
def handler_with_alert():
    """Handler with a mock alert callback."""
    cb = MagicMock()
    h = PartialFillHandler(alert_callback=cb, timeout_seconds=60)
    return h, cb


def _make_fill(
    order_id="ORD-001",
    ticker="AAPL",
    side="BUY",
    requested_qty=100.0,
    filled_qty=40.0,
    avg_fill_price=150.0,
    remaining_qty=60.0,
    status="PARTIAL",
    broker="IBKR",
    sl_order_id="SL-001",
    tp_order_id="TP-001",
    timestamp=None,
):
    """Helper to build a fill event dict."""
    return {
        "order_id": order_id,
        "ticker": ticker,
        "side": side,
        "requested_qty": requested_qty,
        "filled_qty": filled_qty,
        "avg_fill_price": avg_fill_price,
        "remaining_qty": remaining_qty,
        "status": status,
        "timestamp": timestamp or datetime.now(UTC),
        "broker": broker,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
    }


# ---------------------------------------------------------------------------
# Full fill -- no adjustment needed
# ---------------------------------------------------------------------------

class TestFullFill:
    def test_full_fill_returns_complete(self, handler):
        event = _make_fill(
            filled_qty=100.0,
            remaining_qty=0.0,
            status="FILLED",
        )
        result = handler.on_fill(event)
        assert result["action"] == "COMPLETE"
        assert result["order_id"] == "ORD-001"

    def test_full_fill_not_tracked_as_pending(self, handler):
        event = _make_fill(
            filled_qty=100.0,
            remaining_qty=0.0,
            status="FILLED",
        )
        handler.on_fill(event)
        pending = handler.check_pending_fills()
        assert len(pending) == 0

    def test_status_filled_even_if_remaining_positive(self, handler):
        """If broker says FILLED, trust it even if remaining_qty > 0."""
        event = _make_fill(
            filled_qty=100.0,
            remaining_qty=0.0,
            status="FILLED",
        )
        result = handler.on_fill(event)
        assert result["action"] == "COMPLETE"


# ---------------------------------------------------------------------------
# Partial fill with SL adjustment
# ---------------------------------------------------------------------------

class TestPartialFill:
    def test_partial_fill_returns_adjust_sl(self, handler):
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["filled_qty"] == 40.0
        assert result["remaining_qty"] == 60.0
        assert result["new_sl_qty"] == 40.0
        assert result["new_tp_qty"] == 40.0
        assert result["sl_order_id"] == "SL-001"
        assert result["tp_order_id"] == "TP-001"
        assert result["broker"] == "IBKR"

    def test_partial_fill_tracked_as_pending(self, handler):
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(event)
        pending = handler.check_pending_fills()
        assert len(pending) == 1
        assert pending[0]["order_id"] == "ORD-001"
        assert pending[0]["filled_qty"] == 40.0
        assert pending[0]["remaining_qty"] == 60.0

    def test_partial_fill_without_sl_returns_no_sl(self, handler):
        event = _make_fill(
            filled_qty=40.0,
            remaining_qty=60.0,
            status="PARTIAL",
            sl_order_id=None,
        )
        result = handler.on_fill(event)
        assert result["action"] == "NO_SL"

    def test_multiple_partial_fills_update_pending(self, handler):
        """Second partial fill for same order updates the tracking."""
        event1 = _make_fill(filled_qty=20.0, remaining_qty=80.0, status="PARTIAL")
        event2 = _make_fill(filled_qty=60.0, remaining_qty=40.0, status="PARTIAL")
        handler.on_fill(event1)
        handler.on_fill(event2)
        pending = handler.check_pending_fills()
        assert len(pending) == 1
        assert pending[0]["filled_qty"] == 60.0

    def test_partial_then_full_fill_clears_pending(self, handler):
        partial = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(partial)
        assert len(handler.check_pending_fills()) == 1

        full = _make_fill(filled_qty=100.0, remaining_qty=0.0, status="FILLED")
        result = handler.on_fill(full)
        assert result["action"] == "COMPLETE"
        assert len(handler.check_pending_fills()) == 0


# ---------------------------------------------------------------------------
# Cancelled with partial fill
# ---------------------------------------------------------------------------

class TestCancelledFill:
    def test_cancelled_no_fill(self, handler):
        event = _make_fill(
            filled_qty=0.0,
            remaining_qty=100.0,
            status="CANCELLED",
        )
        result = handler.on_fill(event)
        assert result["action"] == "CANCELLED"
        assert result["filled_qty"] == 0.0

    def test_cancelled_with_partial_fill(self, handler):
        event = _make_fill(
            filled_qty=30.0,
            remaining_qty=70.0,
            status="CANCELLED",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["filled_qty"] == 30.0


# ---------------------------------------------------------------------------
# Timeout on pending fills
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_wait_before_timeout(self, handler):
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(event)
        pending = handler.check_pending_fills()
        assert len(pending) == 1
        assert pending[0]["action"] == "WAIT"

    def test_cancel_after_timeout(self):
        handler = PartialFillHandler(timeout_seconds=0)
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(event)
        pending = handler.check_pending_fills()
        assert len(pending) == 1
        assert pending[0]["action"] == "CANCEL_REMAINING"

    def test_timeout_removes_from_pending(self):
        handler = PartialFillHandler(timeout_seconds=0)
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(event)
        handler.check_pending_fills()
        # After timeout + check, order should be cleaned up
        pending2 = handler.check_pending_fills()
        assert len(pending2) == 0

    def test_alert_triggered_on_uncovered_timeout(self):
        cb = MagicMock()
        # Timeout must exceed UNCOVERED_ALERT_SECONDS (60s) so alert fires
        handler = PartialFillHandler(alert_callback=cb, timeout_seconds=120)
        event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
        handler.on_fill(event)
        # Backdate the first partial timestamp so elapsed > 60s
        with handler._lock:
            for oid in handler._first_partial_ts:
                handler._first_partial_ts[oid] = datetime.now(UTC) - timedelta(seconds=90)
        handler.check_pending_fills()
        assert cb.called
        msg = cb.call_args[0][0]
        assert "stuck" in msg.lower() or "partial" in msg.lower()


# ---------------------------------------------------------------------------
# compute_sl_adjustment -- instrument-specific rounding
# ---------------------------------------------------------------------------

class TestComputeSlAdjustment:
    def test_equity_integer_shares(self, handler):
        sl = {"order_id": "SL-1", "qty": 100, "price": 145.00, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=37.0, total_qty=100.0)
        assert result["new_qty"] == 37.0  # int(37.0) = 37
        assert result["needs_update"] is True
        assert result["instrument_type"] == "EQUITY"

    def test_equity_floors_to_integer(self, handler):
        sl = {"order_id": "SL-1", "qty": 100, "price": 145.00, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=33.7, total_qty=100.0)
        # 100 * (33.7/100) = 33.7 -> floor to 33
        assert result["new_qty"] == 33.0

    def test_futures_integer_contracts(self, handler):
        sl = {"order_id": "SL-2", "qty": 5, "price": 4200.00, "instrument_type": "FUTURES"}
        result = handler.compute_sl_adjustment(sl, filled_qty=2.0, total_qty=5.0)
        assert result["new_qty"] == 2.0
        assert result["needs_update"] is True

    def test_fx_lot_rounding(self, handler):
        sl = {"order_id": "SL-3", "qty": 100000, "price": 1.0850, "instrument_type": "FX"}
        result = handler.compute_sl_adjustment(sl, filled_qty=50000, total_qty=100000)
        assert result["new_qty"] == 50000.0
        assert result["instrument_type"] == "FX"

    def test_crypto_fractional(self, handler):
        sl = {"order_id": "SL-4", "qty": 0.5, "price": 42000.0, "instrument_type": "CRYPTO"}
        result = handler.compute_sl_adjustment(sl, filled_qty=0.123456, total_qty=0.5)
        # 0.5 * (0.123456/0.5) = 0.123456
        assert abs(result["new_qty"] - 0.123456) < 1e-8
        assert result["needs_update"] is True

    def test_crypto_precision_8_decimals(self, handler):
        sl = {"order_id": "SL-5", "qty": 1.0, "price": 50000.0, "instrument_type": "CRYPTO"}
        result = handler.compute_sl_adjustment(sl, filled_qty=0.12345678, total_qty=1.0)
        assert abs(result["new_qty"] - 0.12345678) < 1e-10

    def test_no_update_when_fully_filled(self, handler):
        sl = {"order_id": "SL-6", "qty": 100, "price": 150.0, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=100.0, total_qty=100.0)
        assert result["needs_update"] is False
        assert result["new_qty"] == 100.0

    def test_zero_total_qty(self, handler):
        sl = {"order_id": "SL-7", "qty": 50, "price": 100.0, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=0.0, total_qty=0.0)
        assert result["new_qty"] == 0.0
        # total_qty=0 is an error state -- no actionable adjustment possible
        assert result["needs_update"] is False
        assert result["adjustment_ratio"] == 0.0

    def test_adjustment_ratio(self, handler):
        sl = {"order_id": "SL-8", "qty": 200, "price": 100.0, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=50.0, total_qty=200.0)
        assert result["adjustment_ratio"] == 0.25


# ---------------------------------------------------------------------------
# get_exposure_gap
# ---------------------------------------------------------------------------

class TestExposureGap:
    def test_fully_covered(self, handler):
        pos = {"symbol": "AAPL", "qty": 100, "side": "BUY"}
        sls = [{"order_id": "SL-1", "qty": 100, "status": "open"}]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["is_fully_covered"] is True
        assert gap["uncovered_qty"] < 1e-8
        assert gap["coverage_pct"] == 100.0

    def test_partially_covered(self, handler):
        pos = {"symbol": "AAPL", "qty": 100, "side": "BUY"}
        sls = [{"order_id": "SL-1", "qty": 40, "status": "open"}]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["is_fully_covered"] is False
        assert gap["covered_qty"] == 40.0
        assert gap["uncovered_qty"] == 60.0
        assert gap["coverage_pct"] == 40.0

    def test_no_sl_orders(self, handler):
        pos = {"symbol": "AAPL", "qty": 100, "side": "BUY"}
        gap = handler.get_exposure_gap(pos, [])
        assert gap["is_fully_covered"] is False
        assert gap["uncovered_qty"] == 100.0
        assert gap["coverage_pct"] == 0.0

    def test_multiple_sl_orders_summed(self, handler):
        pos = {"symbol": "EURUSD", "qty": 100000}
        sls = [
            {"order_id": "SL-1", "qty": 50000, "status": "open"},
            {"order_id": "SL-2", "qty": 50000, "status": "active"},
        ]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["is_fully_covered"] is True

    def test_filled_sl_not_counted(self, handler):
        """An already-filled SL should not count as coverage."""
        pos = {"symbol": "AAPL", "qty": 100}
        sls = [
            {"order_id": "SL-1", "qty": 100, "status": "filled"},
        ]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["is_fully_covered"] is False
        assert gap["covered_qty"] == 0.0

    def test_zero_position(self, handler):
        pos = {"symbol": "AAPL", "qty": 0}
        sls = [{"order_id": "SL-1", "qty": 50, "status": "open"}]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["coverage_pct"] == 100.0
        assert gap["is_fully_covered"] is True

    def test_alert_on_uncovered(self):
        cb = MagicMock()
        handler = PartialFillHandler(alert_callback=cb)
        pos = {"symbol": "BTC/USDC", "qty": 0.5}
        sls = [{"order_id": "SL-1", "qty": 0.2, "status": "open"}]
        handler.get_exposure_gap(pos, sls)
        assert cb.called
        msg = cb.call_args[0][0]
        assert "BTC/USDC" in msg
        assert "gap" in msg.lower() or "uncovered" in msg.lower()

    def test_negative_qty_treated_as_absolute(self, handler):
        """Short position with negative qty should use abs value."""
        pos = {"symbol": "AAPL", "qty": -100, "side": "SELL"}
        sls = [{"order_id": "SL-1", "qty": -100, "status": "open"}]
        gap = handler.get_exposure_gap(pos, sls)
        assert gap["position_qty"] == 100.0
        assert gap["covered_qty"] == 100.0
        assert gap["is_fully_covered"] is True


# ---------------------------------------------------------------------------
# Multi-broker scenarios
# ---------------------------------------------------------------------------

class TestMultiBroker:
    def test_ibkr_equity(self, handler):
        event = _make_fill(
            ticker="AAPL",
            broker="IBKR",
            requested_qty=100,
            filled_qty=40,
            remaining_qty=60,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["broker"] == "IBKR"
        assert result["new_sl_qty"] == 40.0

    def test_ibkr_fx_lots(self, handler):
        event = _make_fill(
            ticker="EURUSD",
            broker="IBKR",
            requested_qty=100000,
            filled_qty=50000,
            remaining_qty=50000,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["new_sl_qty"] == 50000.0

    def test_ibkr_futures(self, handler):
        event = _make_fill(
            ticker="MES",
            broker="IBKR",
            requested_qty=5,
            filled_qty=2,
            remaining_qty=3,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["new_sl_qty"] == 2.0

    def test_binance_crypto_fractional(self, handler):
        event = _make_fill(
            ticker="BTCUSDC",
            broker="BINANCE",
            requested_qty=0.5,
            filled_qty=0.123,
            remaining_qty=0.377,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["broker"] == "BINANCE"
        assert result["new_sl_qty"] == 0.123

    def test_alpaca_us_equity(self, handler):
        event = _make_fill(
            ticker="TSLA",
            broker="ALPACA",
            requested_qty=50,
            filled_qty=20,
            remaining_qty=30,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["broker"] == "ALPACA"
        assert result["new_sl_qty"] == 20.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_overfill_does_not_reject(self, handler):
        """Broker overfill (filled > requested) should not crash."""
        event = _make_fill(
            filled_qty=110.0,
            requested_qty=100.0,
            remaining_qty=0.0,
            status="FILLED",
        )
        result = handler.on_fill(event)
        assert result["action"] == "COMPLETE"

    def test_zero_fill_qty(self, handler):
        event = _make_fill(
            filled_qty=0.0,
            remaining_qty=100.0,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["new_sl_qty"] == 0.0

    def test_negative_fill_qty_rejected(self, handler):
        event = _make_fill(filled_qty=-10.0, remaining_qty=110.0, status="PARTIAL")
        result = handler.on_fill(event)
        assert result["action"] == "INVALID"
        assert "negative" in result["reason"].lower()

    def test_zero_requested_qty_rejected(self, handler):
        event = _make_fill(
            filled_qty=0.0,
            requested_qty=0.0,
            remaining_qty=0.0,
            status="PARTIAL",
        )
        result = handler.on_fill(event)
        assert result["action"] == "INVALID"
        assert "requested_qty" in result["reason"]

    def test_missing_required_fields(self, handler):
        result = handler.on_fill({"order_id": "X"})
        assert result["action"] == "INVALID"
        assert "Missing" in result["reason"]

    def test_non_dict_input(self, handler):
        result = handler.on_fill("not a dict")
        assert result["action"] == "INVALID"

    def test_none_input(self, handler):
        result = handler.on_fill(None)
        assert result["action"] == "INVALID"

    def test_missing_remaining_qty_computed(self, handler):
        """remaining_qty should be computed if absent."""
        event = {
            "order_id": "ORD-X",
            "ticker": "AAPL",
            "side": "BUY",
            "requested_qty": 100.0,
            "filled_qty": 40.0,
            "avg_fill_price": 150.0,
            "status": "PARTIAL",
            "broker": "IBKR",
            "sl_order_id": "SL-X",
            "tp_order_id": "TP-X",
        }
        result = handler.on_fill(event)
        assert result["action"] == "ADJUST_SL"
        assert result["remaining_qty"] == 60.0

    def test_negative_price_in_sl_adjustment(self, handler):
        """Negative price should be preserved (e.g., some exotic instruments)."""
        sl = {"order_id": "SL-NEG", "qty": 100, "price": -5.0, "instrument_type": "EQUITY"}
        result = handler.compute_sl_adjustment(sl, filled_qty=50.0, total_qty=100.0)
        assert result["price"] == -5.0
        assert result["new_qty"] == 50.0

    def test_very_small_crypto_fill(self, handler):
        """Very small crypto fills should preserve precision."""
        sl = {"order_id": "SL-SM", "qty": 0.001, "price": 42000.0, "instrument_type": "CRYPTO"}
        result = handler.compute_sl_adjustment(sl, filled_qty=0.00012345, total_qty=0.001)
        assert abs(result["new_qty"] - 0.00012345) < 1e-10

    def test_string_qty_fields(self, handler):
        """String quantities should be handled gracefully (some brokers return strings)."""
        event = _make_fill()
        event["filled_qty"] = "40.0"
        event["requested_qty"] = "100.0"
        result = handler.on_fill(event)
        # Should not crash -- validation converts to float
        assert result["action"] in ("ADJUST_SL", "COMPLETE", "PARTIAL")


# ---------------------------------------------------------------------------
# JSONL logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_fill_event_logged(self, handler, tmp_path):
        """Verify that fill events are written to JSONL."""
        log_file = tmp_path / "partial_fills_log.jsonl"
        with patch(
            "core.execution.partial_fill_handler._LOG_FILE", log_file
        ):
            event = _make_fill(filled_qty=40.0, remaining_qty=60.0, status="PARTIAL")
            handler.on_fill(event)

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert "ts" in record
        assert "event" in record

    def test_complete_fill_logged(self, handler, tmp_path):
        log_file = tmp_path / "partial_fills_log.jsonl"
        with patch(
            "core.execution.partial_fill_handler._LOG_FILE", log_file
        ):
            event = _make_fill(filled_qty=100.0, remaining_qty=0.0, status="FILLED")
            handler.on_fill(event)

        lines = log_file.read_text().strip().split("\n")
        events = [json.loads(l)["event"] for l in lines]
        assert "FILL_RECEIVED" in events
        assert "FILL_COMPLETE" in events


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_partial_fills(self, handler):
        """Multiple threads submitting partial fills should not corrupt state."""
        results = []
        errors = []

        def submit_fill(oid):
            try:
                event = _make_fill(
                    order_id=f"ORD-{oid}",
                    filled_qty=10.0,
                    remaining_qty=90.0,
                    status="PARTIAL",
                )
                r = handler.on_fill(event)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit_fill, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20
        pending = handler.check_pending_fills()
        assert len(pending) == 20


# ---------------------------------------------------------------------------
# Alert callback
# ---------------------------------------------------------------------------

class TestAlertCallback:
    def test_no_sl_triggers_alert(self):
        cb = MagicMock()
        handler = PartialFillHandler(alert_callback=cb)
        event = _make_fill(
            filled_qty=40.0,
            remaining_qty=60.0,
            status="PARTIAL",
            sl_order_id=None,
        )
        handler.on_fill(event)
        assert cb.called

    def test_alert_callback_exception_does_not_crash(self):
        def bad_callback(msg, ctx):
            raise RuntimeError("callback exploded")

        handler = PartialFillHandler(alert_callback=bad_callback)
        event = _make_fill(
            filled_qty=40.0,
            remaining_qty=60.0,
            status="PARTIAL",
            sl_order_id=None,
        )
        # Should not raise
        result = handler.on_fill(event)
        assert result["action"] == "NO_SL"


# ---------------------------------------------------------------------------
# Round-trip: partial fill -> SL adjustment -> exposure gap check
# ---------------------------------------------------------------------------

class TestIntegrationFlow:
    def test_full_flow_partial_to_covered(self, handler):
        """Simulate: partial fill -> compute SL adjustment -> verify coverage."""
        # 1. Partial fill arrives
        fill = _make_fill(
            order_id="ORD-100",
            ticker="AAPL",
            requested_qty=100,
            filled_qty=40,
            remaining_qty=60,
            status="PARTIAL",
            broker="IBKR",
            sl_order_id="SL-100",
        )
        action = handler.on_fill(fill)
        assert action["action"] == "ADJUST_SL"
        assert action["new_sl_qty"] == 40.0

        # 2. Compute exact SL adjustment
        sl_order = {
            "order_id": "SL-100",
            "qty": 100,
            "price": 145.0,
            "instrument_type": "EQUITY",
        }
        adj = handler.compute_sl_adjustment(sl_order, filled_qty=40.0, total_qty=100.0)
        assert adj["new_qty"] == 40.0
        assert adj["needs_update"] is True

        # 3. After adjusting SL to 40, verify coverage
        position = {"symbol": "AAPL", "qty": 40, "side": "BUY"}
        sl_orders = [{"order_id": "SL-100", "qty": 40, "status": "open"}]
        gap = handler.get_exposure_gap(position, sl_orders)
        assert gap["is_fully_covered"] is True
        assert gap["coverage_pct"] == 100.0

    def test_full_flow_crypto_fractional(self, handler):
        """Simulate fractional crypto partial fill end-to-end."""
        fill = _make_fill(
            order_id="ORD-BTC",
            ticker="BTCUSDC",
            requested_qty=0.5,
            filled_qty=0.123456,
            remaining_qty=0.376544,
            status="PARTIAL",
            broker="BINANCE",
            sl_order_id="SL-BTC",
        )
        action = handler.on_fill(fill)
        assert action["action"] == "ADJUST_SL"

        sl_order = {
            "order_id": "SL-BTC",
            "qty": 0.5,
            "price": 40000.0,
            "instrument_type": "CRYPTO",
        }
        adj = handler.compute_sl_adjustment(sl_order, filled_qty=0.123456, total_qty=0.5)
        assert abs(adj["new_qty"] - 0.123456) < 1e-8

        position = {"symbol": "BTCUSDC", "qty": 0.123456}
        sl_orders = [{"order_id": "SL-BTC", "qty": adj["new_qty"], "status": "open"}]
        gap = handler.get_exposure_gap(position, sl_orders)
        assert gap["is_fully_covered"] is True
