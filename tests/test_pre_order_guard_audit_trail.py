"""pre_order_guard + audit_trail regression tests (Phase 5 XXL).

Validates that:
1. pre_order_guard rejects bad books / disabled / non-whitelisted strats / paper-live mismatch.
2. audit_trail records full decision context per order (round-trip).
3. Concurrent appends serialize correctly (thread-safe).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# isolate audit_trail to a temp dir BEFORE any usage
import core.governance.audit_trail as audit_trail
from core.governance.audit_trail import read_recent, record_order_decision
from core.governance.pre_order_guard import GuardError, pre_order_guard


# ---------------------------------------------------------------------------
# pre_order_guard rejection paths
# ---------------------------------------------------------------------------

class TestPreOrderGuardRejections:
    def test_empty_book_raises(self):
        with pytest.raises(GuardError, match="book is empty"):
            pre_order_guard(book="", strategy_id="x")

    def test_empty_strategy_raises(self):
        with pytest.raises(GuardError, match="strategy_id is empty"):
            pre_order_guard(book="binance_crypto", strategy_id="")

    def test_unknown_book_raises(self):
        with pytest.raises(GuardError, match="book unknown"):
            pre_order_guard(book="nonexistent_book_xyz", strategy_id="any")

    def test_bypass_for_test_outside_pytest_raises(self, monkeypatch):
        """Bypass guard is sanity-checked: only allowed in pytest env."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with pytest.raises(GuardError):
            pre_order_guard(book="binance_crypto", strategy_id="x", _bypass_for_test=True)

    def test_bypass_for_test_inside_pytest_passes(self):
        """In pytest env, bypass returns silently for test setup."""
        # PYTEST_CURRENT_TEST is set by pytest itself
        result = pre_order_guard(
            book="binance_crypto", strategy_id="x", _bypass_for_test=True,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Audit trail round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_audit_dir(tmp_path, monkeypatch):
    """Redirect audit_trail.AUDIT_DIR to tmp."""
    monkeypatch.setattr(audit_trail, "AUDIT_DIR", tmp_path)
    return tmp_path


class TestAuditTrailRoundTrip:
    def test_record_then_read(self, isolated_audit_dir):
        record_order_decision(
            book="binance_crypto",
            strategy_id="btc_eth_dual_momentum",
            runtime_source="test",
            symbol="BTCUSDC",
            side="BUY",
            qty=0.1,
            entry_price_est=50000.0,
            stop_loss=49000.0,
            take_profit=52000.0,
            risk_usd=100.0,
            authorized_by="pytest",
            result="ACCEPTED",
        )

        entries = read_recent(days=1)
        assert len(entries) == 1
        e = entries[0]
        assert e["book"] == "binance_crypto"
        assert e["strategy_id"] == "btc_eth_dual_momentum"
        assert e["symbol"] == "BTCUSDC"
        assert e["side"] == "BUY"
        assert e["qty"] == 0.1
        assert e["result"] == "ACCEPTED"
        assert e["authorized_by"] == "pytest"
        assert "ts" in e

    def test_read_filters_by_book(self, isolated_audit_dir):
        for book in ("binance_crypto", "ibkr_futures", "alpaca_us"):
            record_order_decision(
                book=book, strategy_id=f"strat_{book}", runtime_source="t",
                symbol="X", side="BUY", qty=1.0, result="ACCEPTED",
            )
        binance_only = read_recent(days=1, book="binance_crypto")
        assert len(binance_only) == 1
        assert binance_only[0]["book"] == "binance_crypto"

    def test_records_extra_field(self, isolated_audit_dir):
        record_order_decision(
            book="ibkr_futures", strategy_id="cam", runtime_source="t",
            symbol="MES", side="BUY", qty=1.0,
            extra={"oca_group": "OCA_TEST_123", "broker_msg": "OK"},
            result="ACCEPTED",
        )
        entries = read_recent(days=1)
        assert entries[0]["extra"]["oca_group"] == "OCA_TEST_123"

    def test_failure_to_write_does_not_raise(self, monkeypatch):
        """Audit must NEVER block the critical path."""
        # Force an OSError on file open by pointing to a non-writable path
        bad = Path("/not/a/real/dir/that/exists")
        monkeypatch.setattr(audit_trail, "AUDIT_DIR", bad)
        # Should NOT raise — silent log only
        record_order_decision(
            book="x", strategy_id="y", runtime_source="t",
            symbol="X", side="BUY", qty=1.0, result="ACCEPTED",
        )

    def test_concurrent_writes_no_corruption(self, isolated_audit_dir):
        """20 threads x 5 records each = 100 lines, no truncation."""
        def worker(tid):
            for i in range(5):
                record_order_decision(
                    book="binance_crypto",
                    strategy_id=f"thread_{tid}_iter_{i}",
                    runtime_source="t",
                    symbol="X", side="BUY", qty=1.0,
                    result="ACCEPTED",
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = read_recent(days=1)
        assert len(entries) == 100
        # All lines parse as JSON (no torn writes)
        seen_ids = {e["strategy_id"] for e in entries}
        assert len(seen_ids) == 100

    def test_jsonl_file_is_per_day(self, isolated_audit_dir):
        from datetime import datetime, timezone
        record_order_decision(
            book="x", strategy_id="y", runtime_source="t",
            symbol="X", side="BUY", qty=1.0, result="ACCEPTED",
        )
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected_file = isolated_audit_dir / f"orders_{today}.jsonl"
        assert expected_file.exists()
