"""Tests promotion_gate.can_go_live_micro() + pre_order_guard live_micro."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.governance.live_micro_sizing import LiveMicroViolation
from core.governance.pre_order_guard import GuardError, pre_order_guard

UTC = timezone.utc


# =========================================================================
# pre_order_guard live_micro enforcement tests
# =========================================================================


class TestPreOrderGuardLiveMicro:
    """Tests that pre_order_guard correctly enforces live_micro sizing caps.

    These tests use _bypass_for_test=False to exercise the real code path,
    so we need to set up the config so that normal checks pass first.
    Since full integration tests are heavy, we unit-test the live_micro
    branch directly via a monkeypatched registry."""

    def test_live_micro_status_requires_grade_and_notional(self, monkeypatch):
        """If status=live_micro but grade/notional missing, raise GuardError."""
        # Shortcut: we patch book registry to make the pre-checks pass,
        # then assert the live_micro branch rejects missing kwargs.
        import core.governance.pre_order_guard as guard_mod

        monkeypatch.setattr(guard_mod, "_books_cache", {
            "test_book": {"book_id": "test_book", "mode_authorized": "live_micro_allowed"},
        })
        monkeypatch.setattr(guard_mod, "_books_cache_mtime", 1.0)

        # Patch BOOKS_REGISTRY_PATH stat so mtime check doesn't kick cache invalidation.
        class _FakePath:
            def exists(self):
                return True

            def stat(self):
                class _S:
                    st_mtime = 1.0
                return _S()

        monkeypatch.setattr(guard_mod, "BOOKS_REGISTRY_PATH", _FakePath())

        # Also need is_strategy_live_allowed to return True.
        def fake_live_allowed(strat, book):
            return True

        import core.governance.live_whitelist as lw
        monkeypatch.setattr(lw, "is_strategy_live_allowed", fake_live_allowed)

        # safety_mode_flag / kill_switches / book_health should be no-ops in test.
        def fake_safety():
            return (False, {})

        import core.governance.safety_mode_flag as sm
        monkeypatch.setattr(sm, "is_safety_mode_active", fake_safety)

        import core.governance.kill_switches_scoped as ks
        monkeypatch.setattr(ks, "is_killed", lambda book_id, strategy_id: (False, ""))

        # Mock book_health to return OK health (passes live path).
        import core.governance.book_health as bh
        class _FakeHealth:
            class status:
                value = "OK"
            checks = []

        monkeypatch.setattr(bh, "get_book_health", lambda book: _FakeHealth())

        with pytest.raises(GuardError) as exc:
            pre_order_guard(
                book="test_book",
                strategy_id="test_strat",
                paper_mode=False,
                strategy_status="live_micro",
                # notional_usd and strategy_grade missing -> reject
            )
        assert "live_micro requires notional_usd" in str(exc.value)

    def test_live_micro_sizing_cap_rejects(self, monkeypatch):
        """Order exceeding grade cap -> GuardError with sizing violation detail."""
        _setup_passthrough_mocks(monkeypatch)

        with pytest.raises(GuardError) as exc:
            pre_order_guard(
                book="test_book",
                strategy_id="test_strat",
                paper_mode=False,
                strategy_status="live_micro",
                strategy_grade="B",
                notional_usd=250.0,  # > B cap $200
            )
        assert "live_micro sizing violation" in str(exc.value)
        assert "notional_cap_exceeded" in str(exc.value)

    def test_live_micro_within_cap_passes(self, monkeypatch):
        _setup_passthrough_mocks(monkeypatch)
        # Should not raise
        pre_order_guard(
            book="test_book",
            strategy_id="test_strat",
            paper_mode=False,
            strategy_status="live_micro",
            strategy_grade="B",
            notional_usd=199.0,
            risk_usd=19.0,
            live_start_at="2026-04-22",
            open_positions_count=0,
        )

    def test_live_micro_pyramid_blocked_j5(self, monkeypatch):
        """Open position + J+5 -> block new entry (no pyramid)."""
        _setup_passthrough_mocks(monkeypatch)
        with pytest.raises(GuardError) as exc:
            pre_order_guard(
                book="test_book",
                strategy_id="test_strat",
                paper_mode=False,
                strategy_status="live_micro",
                strategy_grade="A",
                notional_usd=200.0,
                live_start_at=(datetime.now(UTC).date() - timedelta(days=5)).isoformat(),
                open_positions_count=1,
            )
        assert "live_micro pyramiding blocked" in str(exc.value)

    def test_paper_mode_skips_live_micro_enforcement(self, monkeypatch):
        """Paper mode should ignore all live_micro-specific checks."""
        _setup_passthrough_mocks(monkeypatch)
        # Huge notional + no grade, but paper_mode=True -> should not raise.
        pre_order_guard(
            book="test_book",
            strategy_id="test_strat",
            paper_mode=True,
            strategy_status="live_micro",
            notional_usd=100000.0,
        )


def _setup_passthrough_mocks(monkeypatch):
    """Mock dependencies so only the live_micro branch is exercised."""
    import core.governance.pre_order_guard as guard_mod

    monkeypatch.setattr(guard_mod, "_books_cache", {
        "test_book": {"book_id": "test_book", "mode_authorized": "live_micro_allowed"},
    })
    monkeypatch.setattr(guard_mod, "_books_cache_mtime", 1.0)

    class _FakePath:
        def exists(self):
            return True

        def stat(self):
            class _S:
                st_mtime = 1.0
            return _S()

    monkeypatch.setattr(guard_mod, "BOOKS_REGISTRY_PATH", _FakePath())

    import core.governance.live_whitelist as lw
    monkeypatch.setattr(lw, "is_strategy_live_allowed", lambda s, b: True)

    import core.governance.safety_mode_flag as sm
    monkeypatch.setattr(sm, "is_safety_mode_active", lambda: (False, {}))

    import core.governance.kill_switches_scoped as ks
    monkeypatch.setattr(ks, "is_killed", lambda book_id, strategy_id: (False, ""))

    import core.governance.book_health as bh
    class _FakeHealth:
        class status:
            value = "OK"
        checks = []
    monkeypatch.setattr(bh, "get_book_health", lambda book: _FakeHealth())


# =========================================================================
# can_go_live_micro() gate tests
# =========================================================================


class TestCanGoLiveMicro:
    """Tests for can_go_live_micro() promotion gate."""

    def test_unknown_strategy_fails(self):
        from core.governance.promotion_gate import can_go_live_micro
        result = can_go_live_micro("nonexistent_strategy_xyz")
        assert result.is_pass() is False
        check = next(c for c in result.checks if c.name == "whitelist_lookup")
        assert check.passed is False

    def test_current_status_check_label(self):
        """Check the current_status check reports expected vs actual."""
        from core.governance.promotion_gate import can_go_live_micro

        # Real strategy in registry: gold_trend_mgc (paper_only in current registry)
        result = can_go_live_micro("gold_trend_mgc")
        status_check = next(
            (c for c in result.checks if c.name == "current_status"), None,
        )
        assert status_check is not None
        # This test just checks the gate produces a diagnosis, not that PASS
        # (real gate depends on paper age + incidents)
        assert "current_status=" in status_check.message
