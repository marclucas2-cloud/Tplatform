"""Tests for OrphanDetector — orphan order detection and cleanup.

Covers:
  - Clean state (no orphans)
  - Orphan SL after manual close
  - Orphan TP after SL hit
  - Stale bracket state
  - EOD cleanup
  - Safety: never cancel order with matching position
  - Concurrent positions on same ticker (different strategies)
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from core.execution.orphan_detector import OrphanDetector

# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def detector():
    """Basic OrphanDetector without alert callback."""
    return OrphanDetector()


@pytest.fixture
def detector_with_alert():
    """OrphanDetector with a mock alert callback."""
    cb = MagicMock()
    return OrphanDetector(alert_callback=cb), cb


@pytest.fixture
def mock_broker():
    """Mock broker with cancel_order, get_open_orders, get_positions."""
    broker = MagicMock()
    broker.cancel_order = MagicMock()
    broker.get_open_orders = MagicMock(return_value=[])
    broker.get_positions = MagicMock(return_value=[])
    return broker


@pytest.fixture
def brackets_file(tmp_path):
    """Create a temporary active_brackets.json file."""
    path = tmp_path / "active_brackets.json"
    return path


# ======================================================================
# Helpers
# ======================================================================

def _order(
    order_id="ORD-1",
    ticker="AAPL",
    side="SELL",
    order_type="STP",
    qty=10,
    price=145.0,
    oca_group=None,
    parent_order_id=None,
    timestamp=None,
):
    """Create an order dict."""
    o = {
        "order_id": order_id,
        "ticker": ticker,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "price": price,
    }
    if oca_group is not None:
        o["oca_group"] = oca_group
    if parent_order_id is not None:
        o["parent_order_id"] = parent_order_id
    if timestamp is not None:
        o["timestamp"] = timestamp
    return o


def _position(ticker="AAPL", qty=10, side="LONG", strategy=None):
    """Create a position dict."""
    p = {"ticker": ticker, "qty": qty, "side": side}
    if strategy:
        p["strategy"] = strategy
    return p


def _write_brackets(path, brackets_dict):
    """Write brackets JSON to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(brackets_dict, f)


# ======================================================================
# Test: Clean state (no orphans)
# ======================================================================

class TestCleanState:
    """When all orders match positions, no orphans should be found."""

    def test_no_orders_no_positions(self, detector):
        result = detector.scan_orphans([], [])
        assert result == []

    def test_no_orders_with_positions(self, detector):
        positions = [_position("AAPL", 10, "LONG")]
        result = detector.scan_orphans([], positions)
        assert result == []

    def test_matching_sl_for_long_position(self, detector):
        """A SELL SL order for a LONG position is NOT an orphan."""
        orders = [_order("SL-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = [_position("AAPL", 10, "LONG")]
        result = detector.scan_orphans(orders, positions)
        assert result == []

    def test_matching_tp_for_long_position(self, detector):
        """A SELL LMT order (TP) for a LONG position is NOT an orphan."""
        orders = [_order("TP-1", "AAPL", "SELL", "LMT", 10, 160.0)]
        positions = [_position("AAPL", 10, "LONG")]
        result = detector.scan_orphans(orders, positions)
        assert result == []

    def test_matching_sl_for_short_position(self, detector):
        """A BUY SL order for a SHORT position is NOT an orphan."""
        orders = [_order("SL-1", "AAPL", "BUY", "STP", 10, 160.0)]
        positions = [_position("AAPL", -10, "SHORT")]
        result = detector.scan_orphans(orders, positions)
        assert result == []

    def test_multiple_tickers_all_matched(self, detector):
        """Multiple tickers, all orders have matching positions."""
        orders = [
            _order("SL-AAPL", "AAPL", "SELL", "STP", 10, 145.0),
            _order("SL-MSFT", "MSFT", "SELL", "STP", 5, 380.0),
            _order("TP-AAPL", "AAPL", "SELL", "LMT", 10, 160.0),
        ]
        positions = [
            _position("AAPL", 10, "LONG"),
            _position("MSFT", 5, "LONG"),
        ]
        result = detector.scan_orphans(orders, positions)
        assert result == []


# ======================================================================
# Test: Orphan SL after manual close
# ======================================================================

class TestOrphanSLAfterManualClose:
    """When a position is closed manually, its SL becomes orphan."""

    def test_sl_orphan_no_position(self, detector):
        """SL order for AAPL but no AAPL position -> orphan."""
        orders = [_order("SL-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = []  # Position was closed manually
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        orphan = result[0]
        assert orphan["order_id"] == "SL-1"
        assert orphan["ticker"] == "AAPL"
        assert orphan["reason"] == "NO_MATCHING_POSITION"
        assert orphan["recommended_action"] == "CANCEL"

    def test_sl_orphan_different_ticker(self, detector):
        """SL for AAPL, but only MSFT position exists."""
        orders = [_order("SL-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = [_position("MSFT", 5, "LONG")]
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["reason"] == "NO_MATCHING_POSITION"

    def test_multiple_orphan_sl_tp_after_close(self, detector):
        """Both SL and TP become orphans after position close."""
        orders = [
            _order("SL-1", "AAPL", "SELL", "STP", 10, 145.0),
            _order("TP-1", "AAPL", "SELL", "LMT", 10, 160.0),
        ]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 2
        assert all(o["reason"] == "NO_MATCHING_POSITION" for o in result)
        assert all(o["recommended_action"] == "CANCEL" for o in result)


# ======================================================================
# Test: Orphan TP after SL hit
# ======================================================================

class TestOrphanTPAfterSLHit:
    """When SL fills, OCA should cancel TP. But if OCA fails, TP is orphan."""

    def test_tp_orphan_after_sl_filled_position_closed(self, detector):
        """SL filled and position closed, but TP still active -> orphan."""
        orders = [_order("TP-1", "AAPL", "SELL", "LMT", 10, 160.0)]
        positions = []  # SL filled, position closed
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["order_id"] == "TP-1"
        assert result[0]["reason"] == "NO_MATCHING_POSITION"
        assert result[0]["recommended_action"] == "CANCEL"

    def test_tp_orphan_for_short_after_sl_hit(self, detector):
        """Short position SL hit (BUY), TP (BUY LMT) still active."""
        orders = [_order("TP-SHORT", "TSLA", "BUY", "LMT", 5, 200.0)]
        positions = []  # SL hit, short closed
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["ticker"] == "TSLA"
        assert result[0]["reason"] == "NO_MATCHING_POSITION"


# ======================================================================
# Test: Direction conflict
# ======================================================================

class TestDirectionConflict:
    """Order direction conflicts with all positions on that ticker."""

    def test_sell_order_for_short_position(self, detector):
        """A SELL SL left from a previous long, but position flipped to short."""
        orders = [_order("SL-OLD", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = [_position("AAPL", -5, "SHORT")]
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["reason"] == "CONFLICT"
        assert result[0]["recommended_action"] == "CANCEL"

    def test_buy_order_for_long_position(self, detector):
        """A BUY SL left from a previous short, but position flipped to long."""
        orders = [_order("SL-OLD", "AAPL", "BUY", "STP", 5, 160.0)]
        positions = [_position("AAPL", 10, "LONG")]
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["reason"] == "CONFLICT"
        assert result[0]["recommended_action"] == "CANCEL"


# ======================================================================
# Test: Stale bracket state
# ======================================================================

class TestStaleBracketState:
    """Brackets persisted in JSON but position no longer exists."""

    def test_stale_bracket_no_position(self, detector, brackets_file):
        """Bracket for AAPL exists but no AAPL position."""
        brackets = {
            "BRACKET_AAPL_abc123": {
                "oca_group": "BRACKET_AAPL_abc123",
                "parent_order_id": 100,
                "sl_order_id": 101,
                "tp_order_id": 102,
                "symbol": "AAPL",
                "direction": "BUY",
                "quantity": 10,
                "entry_price": 150.0,
                "stop_loss_price": 145.0,
                "take_profit_price": 160.0,
                "instrument_type": "EQUITY",
                "status": "SUBMITTED",
            }
        }
        _write_brackets(brackets_file, brackets)
        positions = []

        result = detector.scan_bracket_state(brackets_file, positions)

        assert len(result) == 2  # SL + TP
        tickers = {o["ticker"] for o in result}
        assert tickers == {"AAPL"}
        assert all(o["reason"] == "STALE_BRACKET" for o in result)
        assert all(o["recommended_action"] == "CANCEL" for o in result)

        # Verify order IDs match bracket SL/TP
        ids = {o["order_id"] for o in result}
        assert "101" in ids  # SL
        assert "102" in ids  # TP

    def test_stale_bracket_mixed(self, detector, brackets_file):
        """One bracket is stale, another is valid."""
        brackets = {
            "BRACKET_AAPL_abc": {
                "symbol": "AAPL",
                "direction": "BUY",
                "quantity": 10,
                "sl_order_id": 201,
                "tp_order_id": 202,
                "stop_loss_price": 145.0,
                "take_profit_price": 160.0,
                "status": "SUBMITTED",
            },
            "BRACKET_MSFT_def": {
                "symbol": "MSFT",
                "direction": "BUY",
                "quantity": 5,
                "sl_order_id": 301,
                "tp_order_id": 302,
                "stop_loss_price": 380.0,
                "take_profit_price": 400.0,
                "status": "SUBMITTED",
            },
        }
        _write_brackets(brackets_file, brackets)
        positions = [_position("MSFT", 5, "LONG")]  # Only MSFT exists

        result = detector.scan_bracket_state(brackets_file, positions)

        # Only AAPL bracket is stale
        assert len(result) == 2
        assert all(o["ticker"] == "AAPL" for o in result)

    def test_filled_bracket_ignored(self, detector, brackets_file):
        """Brackets with status FILLED or CANCELLED are ignored."""
        brackets = {
            "BRACKET_AAPL_old": {
                "symbol": "AAPL",
                "direction": "BUY",
                "quantity": 10,
                "sl_order_id": 201,
                "tp_order_id": 202,
                "stop_loss_price": 145.0,
                "take_profit_price": 160.0,
                "status": "FILLED",
            },
            "BRACKET_MSFT_old": {
                "symbol": "MSFT",
                "direction": "SELL",
                "quantity": 5,
                "sl_order_id": 301,
                "tp_order_id": 302,
                "stop_loss_price": 400.0,
                "take_profit_price": 380.0,
                "status": "CANCELLED",
            },
        }
        _write_brackets(brackets_file, brackets)
        positions = []

        result = detector.scan_bracket_state(brackets_file, positions)
        assert result == []

    def test_missing_brackets_file(self, detector, tmp_path):
        """Missing brackets file returns empty list."""
        missing = tmp_path / "nonexistent.json"
        result = detector.scan_bracket_state(missing, [])
        assert result == []

    def test_corrupted_brackets_file(self, detector, tmp_path):
        """Corrupted JSON returns empty list without crashing."""
        bad_file = tmp_path / "bad_brackets.json"
        bad_file.write_text("not valid json {{{", encoding="utf-8")
        result = detector.scan_bracket_state(bad_file, [])
        assert result == []


# ======================================================================
# Test: EOD cleanup
# ======================================================================

class TestEODCleanup:
    """End-of-day cleanup integrates scan + cancel."""

    def test_eod_no_orphans(self, detector, mock_broker):
        """No open orders, no positions -> no orphans."""
        mock_broker.get_open_orders.return_value = []
        mock_broker.get_positions.return_value = []

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["orphans_found"] == 0
        assert result["cancelled"] == 0
        assert result["positions_open"] == 0

    def test_eod_cancels_orphans(self, detector, mock_broker):
        """EOD finds and cancels orphan orders."""
        mock_broker.get_open_orders.return_value = [
            _order("SL-1", "AAPL", "SELL", "STP", 10, 145.0),
            _order("TP-1", "AAPL", "SELL", "LMT", 10, 160.0),
        ]
        mock_broker.get_positions.return_value = []  # Position closed

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["orphans_found"] == 2
        assert result["cancelled"] == 2
        assert result["failed"] == 0

        # Verify cancel_order was called
        assert mock_broker.cancel_order.call_count == 2

    def test_eod_keeps_valid_orders(self, detector, mock_broker):
        """EOD does not cancel orders that have matching positions."""
        mock_broker.get_open_orders.return_value = [
            _order("SL-1", "AAPL", "SELL", "STP", 10, 145.0),
        ]
        mock_broker.get_positions.return_value = [
            _position("AAPL", 10, "LONG"),
        ]

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["orphans_found"] == 0
        assert result["cancelled"] == 0
        mock_broker.cancel_order.assert_not_called()

    def test_eod_reports_positions_open(self, detector, mock_broker):
        """EOD correctly reports number of open positions."""
        mock_broker.get_open_orders.return_value = []
        mock_broker.get_positions.return_value = [
            _position("AAPL", 10, "LONG"),
            _position("MSFT", 5, "LONG"),
        ]

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["positions_open"] == 2

    def test_eod_handles_broker_error_open_orders(self, detector, mock_broker):
        """EOD gracefully handles broker error on get_open_orders."""
        mock_broker.get_open_orders.side_effect = Exception("Connection lost")

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["orphans_found"] == 0
        assert len(result["errors"]) == 1
        assert "get_open_orders" in result["errors"][0]

    def test_eod_handles_broker_error_positions(self, detector, mock_broker):
        """EOD gracefully handles broker error on get_positions."""
        mock_broker.get_open_orders.return_value = []
        mock_broker.get_positions.side_effect = Exception("Timeout")

        close_time = datetime(2025, 3, 28, 16, 0, tzinfo=UTC)
        result = detector.run_eod_cleanup(mock_broker, close_time)

        assert result["orphans_found"] == 0
        assert len(result["errors"]) == 1
        assert "get_positions" in result["errors"][0]


# ======================================================================
# Test: Safety — never cancel order with matching position
# ======================================================================

class TestSafety:
    """Critical safety: orders with matching positions must NOT be cancelled."""

    def test_never_cancel_matching_position_long(self, detector, mock_broker):
        """SL for a long position must NOT be cancelled even with size mismatch."""
        orders = [_order("SL-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = [_position("AAPL", 10, "LONG")]

        orphans = detector.scan_orphans(orders, positions)
        assert len(orphans) == 0

        # Even if we pass an empty list to cleanup, nothing should cancel
        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 0
        mock_broker.cancel_order.assert_not_called()

    def test_never_cancel_matching_position_short(self, detector, mock_broker):
        """BUY SL for a short position must NOT be cancelled."""
        orders = [_order("SL-1", "AAPL", "BUY", "STP", 5, 160.0)]
        positions = [_position("AAPL", -5, "SHORT")]

        orphans = detector.scan_orphans(orders, positions)
        assert len(orphans) == 0

    def test_size_mismatch_flags_review_not_cancel(self, detector):
        """When qty mismatch exists but position is open, flag REVIEW not CANCEL."""
        # Parent order gone but position still exists with different size
        orders = [
            _order(
                "SL-1", "AAPL", "SELL", "STP", 10, 145.0,
                parent_order_id="PARENT-GONE",
            ),
        ]
        positions = [_position("AAPL", 5, "LONG")]  # Size mismatch: 10 vs 5

        orphans = detector.scan_orphans(orders, positions)

        assert len(orphans) == 1
        assert orphans[0]["recommended_action"] == "REVIEW"
        assert orphans[0]["reason"] == "STALE_BRACKET"

    def test_review_orders_not_cancelled(self, detector, mock_broker):
        """Orders with REVIEW action are skipped during cleanup."""
        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "STALE_BRACKET",
                "recommended_action": "REVIEW",
                "age_seconds": 100.0,
            }
        ]

        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 0
        assert result["skipped"] == 1
        mock_broker.cancel_order.assert_not_called()

    def test_keep_orders_not_cancelled(self, detector, mock_broker):
        """Orders with KEEP action are skipped during cleanup."""
        orphans = [
            {
                "order_id": "ORD-1",
                "ticker": "AAPL",
                "side": "BUY",
                "order_type": "LMT",
                "qty": 10,
                "price": 140.0,
                "reason": "STALE_BRACKET",
                "recommended_action": "KEEP",
                "age_seconds": 50.0,
            }
        ]

        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 0
        assert result["skipped"] == 1
        mock_broker.cancel_order.assert_not_called()


# ======================================================================
# Test: Concurrent positions on same ticker (different strategies)
# ======================================================================

class TestConcurrentPositions:
    """Multiple strategies may hold positions on the same ticker."""

    def test_two_strategies_same_ticker_no_orphan(self, detector):
        """Two long positions on AAPL from different strategies, SL is valid."""
        orders = [
            _order("SL-1", "AAPL", "SELL", "STP", 10, 145.0),
            _order("SL-2", "AAPL", "SELL", "STP", 5, 148.0),
        ]
        positions = [
            _position("AAPL", 10, "LONG", strategy="momentum"),
            _position("AAPL", 5, "LONG", strategy="mean_reversion"),
        ]

        result = detector.scan_orphans(orders, positions)
        assert result == []

    def test_one_strategy_closed_same_ticker(self, detector):
        """One strategy closed, but another still holds. SL is valid if
        there is at least one matching position."""
        orders = [
            _order("SL-1", "AAPL", "SELL", "STP", 10, 145.0),
            _order("SL-2", "AAPL", "SELL", "STP", 5, 148.0),
        ]
        # Only one strategy still has a position
        positions = [
            _position("AAPL", 5, "LONG", strategy="mean_reversion"),
        ]

        # Both orders have matching ticker with a LONG position
        # so they should not be flagged as orphans (even though qty differs)
        result = detector.scan_orphans(orders, positions)
        assert result == []

    def test_mixed_long_short_same_ticker(self, detector):
        """Long and short on same ticker from different strategies.
        A SELL order should not be flagged because there is a LONG position."""
        orders = [
            _order("SL-LONG", "AAPL", "SELL", "STP", 10, 145.0),
            _order("SL-SHORT", "AAPL", "BUY", "STP", 5, 160.0),
        ]
        positions = [
            _position("AAPL", 10, "LONG", strategy="momentum"),
            _position("AAPL", -5, "SHORT", strategy="mean_reversion"),
        ]

        result = detector.scan_orphans(orders, positions)
        assert result == []


# ======================================================================
# Test: Cleanup mechanics
# ======================================================================

class TestCleanupMechanics:
    """Test the cleanup_orphans method directly."""

    def test_cancel_success(self, detector, mock_broker):
        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "NO_MATCHING_POSITION",
                "recommended_action": "CANCEL",
                "age_seconds": 600.0,
            }
        ]

        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 1
        assert result["failed"] == 0
        mock_broker.cancel_order.assert_called_once_with("SL-1")

    def test_cancel_failure(self, detector, mock_broker):
        mock_broker.cancel_order.side_effect = Exception("API error")
        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "NO_MATCHING_POSITION",
                "recommended_action": "CANCEL",
                "age_seconds": 600.0,
            }
        ]

        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 0
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "API error" in result["errors"][0]

    def test_mixed_cancel_and_review(self, detector, mock_broker):
        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "NO_MATCHING_POSITION",
                "recommended_action": "CANCEL",
                "age_seconds": 600.0,
            },
            {
                "order_id": "SL-2",
                "ticker": "MSFT",
                "side": "SELL",
                "order_type": "STP",
                "qty": 5,
                "price": 380.0,
                "reason": "STALE_BRACKET",
                "recommended_action": "REVIEW",
                "age_seconds": 300.0,
            },
        ]

        result = detector.cleanup_orphans(orphans, mock_broker)
        assert result["cancelled"] == 1
        assert result["skipped"] == 1
        mock_broker.cancel_order.assert_called_once_with("SL-1")


# ======================================================================
# Test: Alert callback
# ======================================================================

class TestAlertCallback:
    """Alert callback is invoked when orphans are cleaned up."""

    def test_alert_on_cleanup(self, detector_with_alert, mock_broker):
        detector, alert_cb = detector_with_alert

        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "NO_MATCHING_POSITION",
                "recommended_action": "CANCEL",
                "age_seconds": 600.0,
            }
        ]

        detector.cleanup_orphans(orphans, mock_broker)
        alert_cb.assert_called_once()
        msg = alert_cb.call_args[0][0]
        assert "1 cancelled" in msg

    def test_no_alert_when_no_orphans(self, detector_with_alert, mock_broker):
        detector, alert_cb = detector_with_alert
        detector.cleanup_orphans([], mock_broker)
        alert_cb.assert_not_called()


# ======================================================================
# Test: JSONL logging
# ======================================================================

class TestJSONLLogging:
    """Cleanup events are logged to JSONL file."""

    def test_cleanup_writes_jsonl(self, mock_broker, tmp_path, monkeypatch):
        """Verify that cleanup writes entries to the JSONL log."""
        log_path = tmp_path / "orphan_cleanup_log.jsonl"
        monkeypatch.setattr(
            "core.execution.orphan_detector._CLEANUP_LOG_PATH", log_path,
        )

        detector = OrphanDetector()
        orphans = [
            {
                "order_id": "SL-1",
                "ticker": "AAPL",
                "side": "SELL",
                "order_type": "STP",
                "qty": 10,
                "price": 145.0,
                "reason": "NO_MATCHING_POSITION",
                "recommended_action": "CANCEL",
                "age_seconds": 600.0,
            }
        ]

        detector.cleanup_orphans(orphans, mock_broker)

        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["order_id"] == "SL-1"
        assert entry["ticker"] == "AAPL"
        assert entry["success"] is True
        assert "timestamp" in entry

    def test_failed_cleanup_logged_with_error(self, mock_broker, tmp_path, monkeypatch):
        """Failed cancellation includes error in JSONL log."""
        log_path = tmp_path / "orphan_cleanup_log.jsonl"
        monkeypatch.setattr(
            "core.execution.orphan_detector._CLEANUP_LOG_PATH", log_path,
        )

        mock_broker.cancel_order.side_effect = Exception("Connection refused")

        detector = OrphanDetector()
        orphans = [
            {
                "order_id": "TP-1",
                "ticker": "MSFT",
                "side": "SELL",
                "order_type": "LMT",
                "qty": 5,
                "price": 400.0,
                "reason": "STALE_BRACKET",
                "recommended_action": "CANCEL",
                "age_seconds": 300.0,
            }
        ]

        detector.cleanup_orphans(orphans, mock_broker)

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["success"] is False
        assert "Connection refused" in entry["error"]


# ======================================================================
# Test: Order age calculation
# ======================================================================

class TestOrderAge:
    """Age calculation from various timestamp formats."""

    def test_age_from_unix_timestamp(self, detector):
        ts = time.time() - 120  # 2 minutes ago
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0, timestamp=ts)]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["age_seconds"] >= 119  # Allow 1s tolerance

    def test_age_from_iso_string(self, detector):
        ts = datetime.now(UTC).isoformat()
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0, timestamp=ts)]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["age_seconds"] < 5  # Just created

    def test_age_from_datetime_object(self, detector):
        ts = datetime.now(UTC)
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0, timestamp=ts)]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["age_seconds"] < 5

    def test_no_timestamp_defaults_to_zero(self, detector):
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        assert result[0]["age_seconds"] == 0.0


# ======================================================================
# Test: Orphan format
# ======================================================================

class TestOrphanFormat:
    """Verify the orphan dict has all required fields."""

    def test_orphan_has_all_fields(self, detector):
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = []
        result = detector.scan_orphans(orders, positions)

        assert len(result) == 1
        orphan = result[0]

        required_fields = {
            "order_id", "ticker", "side", "order_type",
            "qty", "price", "reason", "recommended_action", "age_seconds",
        }
        assert set(orphan.keys()) == required_fields

    def test_orphan_field_types(self, detector):
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0)]
        positions = []
        result = detector.scan_orphans(orders, positions)
        orphan = result[0]

        assert isinstance(orphan["order_id"], str)
        assert isinstance(orphan["ticker"], str)
        assert isinstance(orphan["side"], str)
        assert isinstance(orphan["order_type"], str)
        assert isinstance(orphan["qty"], float)
        assert isinstance(orphan["price"], float)
        assert isinstance(orphan["reason"], str)
        assert isinstance(orphan["recommended_action"], str)
        assert isinstance(orphan["age_seconds"], float)

    def test_reason_values(self, detector):
        """Reason must be one of the defined values."""
        valid_reasons = {"NO_MATCHING_POSITION", "STALE_BRACKET", "CONFLICT"}

        # No position
        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0)]
        result = detector.scan_orphans(orders, [])
        assert result[0]["reason"] in valid_reasons

    def test_action_values(self, detector):
        """Action must be CANCEL, REVIEW, or KEEP."""
        valid_actions = {"CANCEL", "REVIEW", "KEEP"}

        orders = [_order("O-1", "AAPL", "SELL", "STP", 10, 145.0)]
        result = detector.scan_orphans(orders, [])
        assert result[0]["recommended_action"] in valid_actions


# ======================================================================
# Test: Edge cases
# ======================================================================

class TestEdgeCases:
    """Edge cases and robustness."""

    def test_empty_ticker_in_order(self, detector):
        """Order with empty ticker is treated as orphan if no empty-ticker position."""
        orders = [_order("O-1", "", "SELL", "STP", 10, 145.0)]
        positions = [_position("AAPL", 10, "LONG")]
        result = detector.scan_orphans(orders, positions)
        assert len(result) == 1

    def test_zero_qty_position_ignored_in_bracket_scan(self, detector, brackets_file):
        """Position with qty=0 is treated as no position."""
        brackets = {
            "BRACKET_AAPL_abc": {
                "symbol": "AAPL",
                "direction": "BUY",
                "quantity": 10,
                "sl_order_id": 201,
                "tp_order_id": 202,
                "stop_loss_price": 145.0,
                "take_profit_price": 160.0,
                "status": "SUBMITTED",
            },
        }
        _write_brackets(brackets_file, brackets)
        positions = [_position("AAPL", 0, "LONG")]  # Flat

        result = detector.scan_bracket_state(brackets_file, positions)
        assert len(result) == 2  # Bracket is stale

    def test_large_number_of_orders(self, detector):
        """Performance sanity: 1000 orders, 100 positions."""
        orders = [
            _order(f"O-{i}", f"TICK-{i % 100}", "SELL", "STP", 10, 100.0)
            for i in range(1000)
        ]
        positions = [
            _position(f"TICK-{i}", 10, "LONG")
            for i in range(50)  # Only 50 tickers have positions
        ]

        result = detector.scan_orphans(orders, positions)
        # Orders for TICK-50 through TICK-99 should be orphans
        # Each ticker has 10 orders (1000/100)
        orphan_tickers = {o["ticker"] for o in result}
        for i in range(50, 100):
            assert f"TICK-{i}" in orphan_tickers

    def test_position_side_inferred_from_qty(self, detector):
        """When position has no explicit side, infer from qty."""
        orders = [_order("SL-1", "AAPL", "SELL", "STP", 10, 145.0)]
        # Position with positive qty but no explicit side
        positions = [{"ticker": "AAPL", "qty": 10}]

        result = detector.scan_orphans(orders, positions)
        assert result == []  # SELL order valid for inferred LONG
