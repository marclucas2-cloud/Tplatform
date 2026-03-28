"""
Tests for LiveReconciliation — compare internal model vs broker positions.

Covers:
  - Perfect match -> all good
  - Quantity mismatch within tolerance (+/-1) -> OK
  - Quantity mismatch beyond tolerance -> divergence
  - Direction mismatch -> critical divergence
  - Orphan position -> alert, don't close
  - Phantom position -> remove from model
  - Cash mismatch within tolerance (+/-$10) -> OK
  - Cash mismatch beyond tolerance -> alert
  - Margin mismatch
  - Empty positions (both sides) -> OK
  - Multiple divergences at once
  - Auto-resolve phantom positions
  - Auto-resolve does NOT close orphans (safety)
  - History tracking
  - Stats calculation
  - Entry price tolerance check
  - Broker API error handling
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.reconciliation_live import LiveReconciliation


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_broker():
    """Mock broker with standard positions."""
    broker = MagicMock()
    broker.get_positions.return_value = [
        {"symbol": "AAPL", "qty": 10, "side": "long",
         "avg_entry": 180.0, "market_val": 1800.0, "unrealized_pl": 0.0},
        {"symbol": "NVDA", "qty": -5, "side": "short",
         "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
        {"symbol": "SPY", "qty": 20, "side": "long",
         "avg_entry": 450.0, "market_val": 9000.0, "unrealized_pl": 0.0},
    ]
    broker.get_account_info.return_value = {
        "equity": 50000.0,
        "cash": 30000.0,
        "margin_used": 5000.0,
    }
    return broker


@pytest.fixture
def alert_log():
    """Capture alerts as a list."""
    log = []

    def callback(message, level):
        log.append((message, level))

    return log, callback


@pytest.fixture
def history_file(tmp_path):
    """Temporary history file path."""
    return tmp_path / "recon_history.json"


@pytest.fixture
def internal_positions():
    """Standard internal position model matching mock_broker."""
    return [
        {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
        {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
        {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
    ]


@pytest.fixture
def recon(mock_broker, alert_log, history_file):
    """Standard LiveReconciliation instance."""
    _, callback = alert_log
    return LiveReconciliation(
        broker=mock_broker,
        alert_callback=callback,
        history_path=history_file,
    )


# =============================================================================
# TEST 1: Perfect match
# =============================================================================

class TestPerfectMatch:
    def test_perfect_match(self, recon, internal_positions):
        """When model matches broker exactly, all checks pass."""
        result = recon.reconcile(internal_positions)

        assert result["matched"] is True
        assert len(result["divergences"]) == 0
        assert len(result["orphan_positions"]) == 0
        assert len(result["phantom_positions"]) == 0

    def test_perfect_match_returns_position_checks(self, recon, internal_positions):
        """Perfect match should still return position check details."""
        result = recon.reconcile(internal_positions)

        # 3 positions x 3 fields (side, qty, avg_entry) = 9 checks
        assert len(result["position_checks"]) == 9
        assert all(c["matched"] for c in result["position_checks"])


# =============================================================================
# TEST 2: Quantity mismatch within tolerance
# =============================================================================

class TestQtyWithinTolerance:
    def test_qty_mismatch_within_tolerance_is_ok(self, recon):
        """Qty difference of 1 (default tolerance) should pass."""
        internal = [
            {"symbol": "AAPL", "qty": 11, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        result = recon.reconcile(internal)

        # AAPL qty 11 vs 10 = diff 1 = within tolerance
        qty_checks = [c for c in result["position_checks"]
                      if c["field"] == "qty" and c["symbol"] == "AAPL"]
        assert len(qty_checks) == 1
        assert qty_checks[0]["matched"] is True


# =============================================================================
# TEST 3: Quantity mismatch beyond tolerance
# =============================================================================

class TestQtyBeyondTolerance:
    def test_qty_mismatch_beyond_tolerance_diverges(self, recon):
        """Qty difference > 1 should be flagged as divergence."""
        internal = [
            {"symbol": "AAPL", "qty": 15, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        qty_divergences = [d for d in result["divergences"] if "QTY MISMATCH" in d]
        assert len(qty_divergences) == 1
        assert "AAPL" in qty_divergences[0]


# =============================================================================
# TEST 4: Direction mismatch (critical)
# =============================================================================

class TestDirectionMismatch:
    def test_direction_mismatch_is_critical(self, recon):
        """Direction mismatch should be flagged."""
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "short", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        direction_divs = [d for d in result["divergences"]
                          if "DIRECTION MISMATCH" in d]
        assert len(direction_divs) == 1
        assert "AAPL" in direction_divs[0]


# =============================================================================
# TEST 5: Orphan position (at broker, not in model) -> alert, don't close
# =============================================================================

class TestOrphanPosition:
    def test_orphan_detected(self, recon):
        """Position at broker but not in model should be flagged as orphan."""
        # Internal only has AAPL and NVDA, missing SPY
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        assert len(result["orphan_positions"]) == 1
        assert result["orphan_positions"][0]["symbol"] == "SPY"

    def test_orphan_triggers_alert(self, recon, alert_log):
        """Orphan position should trigger an alert."""
        log, _ = alert_log
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
        ]
        recon.reconcile(internal)

        assert len(log) >= 1
        # Orphan should trigger critical alert
        levels = [level for _, level in log]
        assert "critical" in levels


# =============================================================================
# TEST 6: Phantom position (in model, not at broker) -> remove from model
# =============================================================================

class TestPhantomPosition:
    def test_phantom_detected(self, recon):
        """Position in model but not at broker should be flagged as phantom."""
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
            {"symbol": "TSLA", "qty": 5, "side": "long", "avg_entry": 200.0},
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        assert len(result["phantom_positions"]) == 1
        assert result["phantom_positions"][0]["symbol"] == "TSLA"


# =============================================================================
# TEST 7: Cash mismatch within tolerance
# =============================================================================

class TestCashWithinTolerance:
    def test_cash_within_tolerance_ok(self, recon, internal_positions):
        """Cash difference within $10 should pass."""
        result = recon.reconcile(
            internal_positions,
            internal_cash=30005.0,  # $5 diff from broker's $30000
        )
        assert result["cash_matched"] is True

    def test_cash_exact_match(self, recon, internal_positions):
        """Exact cash match should pass."""
        result = recon.reconcile(
            internal_positions,
            internal_cash=30000.0,
        )
        assert result["cash_matched"] is True


# =============================================================================
# TEST 8: Cash mismatch beyond tolerance
# =============================================================================

class TestCashBeyondTolerance:
    def test_cash_beyond_tolerance_diverges(self, recon, internal_positions):
        """Cash difference > $50 should be flagged."""
        result = recon.reconcile(
            internal_positions,
            internal_cash=30100.0,  # $100 diff (> $50 tolerance)
        )
        assert result["cash_matched"] is False
        cash_divs = [d for d in result["divergences"] if "CASH MISMATCH" in d]
        assert len(cash_divs) == 1


# =============================================================================
# TEST 9: Margin mismatch
# =============================================================================

class TestMarginMismatch:
    def test_margin_within_tolerance(self, recon, internal_positions):
        """Margin difference within $50 should pass."""
        result = recon.reconcile(
            internal_positions,
            internal_margin=5030.0,  # $30 diff from broker's $5000
        )
        assert result["margin_matched"] is True

    def test_margin_beyond_tolerance(self, recon, internal_positions):
        """Margin difference > $50 should be flagged."""
        result = recon.reconcile(
            internal_positions,
            internal_margin=5100.0,  # $100 diff
        )
        assert result["margin_matched"] is False
        margin_divs = [d for d in result["divergences"] if "MARGIN MISMATCH" in d]
        assert len(margin_divs) == 1


# =============================================================================
# TEST 10: Empty positions (both sides)
# =============================================================================

class TestEmptyPositions:
    def test_empty_both_sides_is_ok(self, alert_log, history_file):
        """No positions on either side should be a clean match."""
        _, callback = alert_log
        broker = MagicMock()
        broker.get_positions.return_value = []
        broker.get_account_info.return_value = {"cash": 50000.0, "margin_used": 0.0}

        recon = LiveReconciliation(
            broker=broker, alert_callback=callback, history_path=history_file
        )
        result = recon.reconcile([])

        assert result["matched"] is True
        assert len(result["position_checks"]) == 0
        assert len(result["orphan_positions"]) == 0
        assert len(result["phantom_positions"]) == 0


# =============================================================================
# TEST 11: Multiple divergences at once
# =============================================================================

class TestMultipleDivergences:
    def test_multiple_divergences(self, recon):
        """Multiple issues should all be reported."""
        internal = [
            # Direction mismatch on AAPL
            {"symbol": "AAPL", "qty": 10, "side": "short", "avg_entry": 180.0},
            # Qty mismatch on NVDA
            {"symbol": "NVDA", "qty": -50, "side": "short", "avg_entry": 500.0},
            # Phantom position
            {"symbol": "TSLA", "qty": 5, "side": "long", "avg_entry": 200.0},
            # Missing SPY (will be orphan from broker side)
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        # At least 3 divergences: direction + qty + phantom + orphan
        assert len(result["divergences"]) >= 3


# =============================================================================
# TEST 12: Auto-resolve phantom positions
# =============================================================================

class TestAutoResolvePhantom:
    def test_auto_resolve_removes_phantom(self, recon):
        """Auto-resolve should mark phantom for removal from model."""
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
            {"symbol": "TSLA", "qty": 5, "side": "long", "avg_entry": 200.0},
        ]
        recon_result = recon.reconcile(internal)
        resolve_result = recon.auto_resolve(recon_result)

        assert len(resolve_result["resolved"]) >= 1
        phantom_resolved = [r for r in resolve_result["resolved"] if "PHANTOM" in r]
        assert len(phantom_resolved) == 1
        assert "TSLA" in phantom_resolved[0]

        # Action should be REMOVE
        remove_actions = [a for a in resolve_result["actions_taken"]
                          if "REMOVE" in a]
        assert len(remove_actions) == 1


# =============================================================================
# TEST 13: Auto-resolve does NOT close orphans (safety)
# =============================================================================

class TestAutoResolveDoesNotCloseOrphans:
    def test_auto_resolve_does_not_close_orphans(self, recon):
        """Auto-resolve should NOT close orphan positions — safety first."""
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
            # Missing NVDA and SPY → they are orphans at broker
        ]
        recon_result = recon.reconcile(internal)
        resolve_result = recon.auto_resolve(recon_result)

        # Orphans should be UNRESOLVED
        unresolved_orphans = [u for u in resolve_result["unresolved"]
                              if "ORPHAN" in u]
        assert len(unresolved_orphans) == 2  # NVDA and SPY

        # Actions should be ALERT, not CLOSE
        close_actions = [a for a in resolve_result["actions_taken"]
                         if "CLOSE" in a]
        assert len(close_actions) == 0

        alert_actions = [a for a in resolve_result["actions_taken"]
                         if "ALERT" in a and "orphan" in a]
        assert len(alert_actions) == 2


# =============================================================================
# TEST 14: History tracking
# =============================================================================

class TestHistoryTracking:
    def test_history_records_each_run(self, recon, internal_positions):
        """Each reconciliation run should be recorded in history."""
        recon.reconcile(internal_positions)
        recon.reconcile(internal_positions)
        recon.reconcile(internal_positions)

        history = recon.get_history()
        assert len(history) == 3

    def test_history_most_recent_first(self, recon, internal_positions):
        """get_history() should return most recent first."""
        recon.reconcile(internal_positions)
        recon.reconcile([])  # Will have divergences

        history = recon.get_history()
        assert len(history) == 2
        # Most recent (empty, divergent) should be first
        assert history[0]["matched"] is False
        assert history[1]["matched"] is True

    def test_history_persisted_to_disk(self, mock_broker, alert_log, history_file,
                                       internal_positions):
        """History should survive instance restart."""
        _, callback = alert_log
        recon1 = LiveReconciliation(
            broker=mock_broker, alert_callback=callback,
            history_path=history_file,
        )
        recon1.reconcile(internal_positions)

        recon2 = LiveReconciliation(
            broker=mock_broker, alert_callback=callback,
            history_path=history_file,
        )
        assert len(recon2.get_history()) == 1

    def test_history_limited_to_n(self, recon, internal_positions):
        """get_history(n) should return at most n entries."""
        for _ in range(5):
            recon.reconcile(internal_positions)

        assert len(recon.get_history(n=3)) == 3
        assert len(recon.get_history(n=100)) == 5


# =============================================================================
# TEST 15: Stats calculation
# =============================================================================

class TestStatsCalculation:
    def test_stats_empty(self, recon):
        """Stats with no runs should return zeros."""
        stats = recon.get_stats()
        assert stats["total_runs"] == 0
        assert stats["divergence_rate"] == 0.0

    def test_stats_after_runs(self, recon, internal_positions):
        """Stats should reflect actual run results."""
        # 2 clean runs
        recon.reconcile(internal_positions)
        recon.reconcile(internal_positions)

        # 1 divergent run
        recon.reconcile([{"symbol": "TSLA", "qty": 5, "side": "long",
                          "avg_entry": 200.0}])

        stats = recon.get_stats()
        assert stats["total_runs"] == 3
        assert stats["total_divergences"] == 1
        assert stats["divergence_rate"] == pytest.approx(1 / 3)
        assert stats["last_run"] is not None
        assert stats["last_divergence"] is not None

    def test_stats_counts_orphans_and_phantoms(self, recon, internal_positions):
        """Stats should count orphan and phantom occurrences."""
        # Run with phantom
        positions_with_phantom = internal_positions + [
            {"symbol": "TSLA", "qty": 5, "side": "long", "avg_entry": 200.0},
        ]
        recon.reconcile(positions_with_phantom)

        # Run with orphan (missing SPY from internal)
        recon.reconcile([
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
        ])

        stats = recon.get_stats()
        assert stats["phantom_count"] == 1
        assert stats["orphan_count"] == 2  # NVDA and SPY are orphans


# =============================================================================
# TEST 16: Entry price tolerance
# =============================================================================

class TestEntryPriceTolerance:
    def test_price_within_tolerance(self, recon):
        """Entry price within 0.1% should pass."""
        internal = [
            # 180.0 * 0.001 = 0.18 → 180.10 is within tolerance
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.10},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        result = recon.reconcile(internal)

        price_checks = [c for c in result["position_checks"]
                        if c["field"] == "avg_entry" and c["symbol"] == "AAPL"]
        assert len(price_checks) == 1
        assert price_checks[0]["matched"] is True

    def test_price_beyond_tolerance(self, recon):
        """Entry price beyond 0.1% should diverge."""
        internal = [
            # 180.0 * 0.001 = 0.18 → 181.0 (0.55%) is beyond tolerance
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 181.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        result = recon.reconcile(internal)

        assert result["matched"] is False
        price_divs = [d for d in result["divergences"] if "PRICE MISMATCH" in d]
        assert len(price_divs) == 1
        assert "AAPL" in price_divs[0]


# =============================================================================
# TEST 17: Broker API error handling
# =============================================================================

class TestBrokerAPIError:
    def test_broker_positions_error(self, alert_log, history_file):
        """Broker API failure should return error divergence, not crash."""
        _, callback = alert_log
        broker = MagicMock()
        broker.get_positions.side_effect = ConnectionError("API timeout")

        recon = LiveReconciliation(
            broker=broker, alert_callback=callback, history_path=history_file
        )
        result = recon.reconcile([
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
        ])

        assert result["matched"] is False
        assert any("Broker API error" in d for d in result["divergences"])

    def test_no_broker_configured(self, alert_log, history_file):
        """No broker should reconcile against empty positions."""
        _, callback = alert_log
        recon = LiveReconciliation(
            broker=None, alert_callback=callback, history_path=history_file
        )
        result = recon.reconcile([
            {"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry": 180.0},
        ])

        # AAPL is phantom (in model, not at broker)
        assert len(result["phantom_positions"]) == 1


# =============================================================================
# TEST 18: Auto-resolve qty mismatch aligns to broker
# =============================================================================

class TestAutoResolveQtyMismatch:
    def test_auto_resolve_aligns_qty_to_broker(self, recon):
        """Auto-resolve should align qty to broker (broker is truth)."""
        internal = [
            {"symbol": "AAPL", "qty": 15, "side": "long", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        recon_result = recon.reconcile(internal)
        resolve_result = recon.auto_resolve(recon_result)

        align_actions = [a for a in resolve_result["actions_taken"]
                         if "ALIGN" in a and "qty" in a]
        assert len(align_actions) == 1
        assert "15" in align_actions[0] and "10" in align_actions[0]

    def test_auto_resolve_direction_mismatch_unresolved(self, recon):
        """Auto-resolve should NOT auto-fix direction mismatch."""
        internal = [
            {"symbol": "AAPL", "qty": 10, "side": "short", "avg_entry": 180.0},
            {"symbol": "NVDA", "qty": -5, "side": "short", "avg_entry": 500.0},
            {"symbol": "SPY", "qty": 20, "side": "long", "avg_entry": 450.0},
        ]
        recon_result = recon.reconcile(internal)
        resolve_result = recon.auto_resolve(recon_result)

        direction_unresolved = [u for u in resolve_result["unresolved"]
                                if "DIRECTION" in u]
        assert len(direction_unresolved) == 1

    def test_auto_resolve_cash_refresh(self, recon, internal_positions):
        """Auto-resolve should refresh cash from broker when mismatched."""
        recon_result = recon.reconcile(
            internal_positions, internal_cash=35000.0  # $5000 off
        )
        resolve_result = recon.auto_resolve(recon_result)

        cash_actions = [a for a in resolve_result["actions_taken"]
                        if "REFRESH" in a and "cash" in a]
        assert len(cash_actions) == 1
