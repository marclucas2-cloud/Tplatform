"""Tests E2 plan 9.0 (2026-04-19) — per-strategy scoped kill switch.

Audit CRO flagged: "Kill switch per-portfolio ferme TOUT. CAM peut stopper
sans tuer GOR". Test that disable_strategy isolates one strategy without
activating the global kill switch.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.kill_switch_live import LiveKillSwitch


@pytest.fixture
def ks(tmp_path):
    alerts = []
    k = LiveKillSwitch(
        state_path=tmp_path / "kill_switch.json",
        alert_callback=lambda msg, lvl: alerts.append((lvl, msg)),
    )
    k.alerts = alerts  # expose for tests
    return k


class TestDisableStrategy:
    def test_disable_adds_to_set(self, ks):
        result = ks.disable_strategy("cam", reason="WF grade dropped")
        assert result["strategy_id"] == "cam"
        assert "disabled_at" in result
        assert ks.is_strategy_disabled("cam")
        assert ks.get_disabled_strategies() == ["cam"]

    def test_disable_does_not_activate_global_kill_switch(self, ks):
        assert not ks.is_active
        ks.disable_strategy("cam", reason="test")
        assert not ks.is_active, "Global kill switch MUST stay inactive"
        assert ks.is_armed

    def test_disable_persists_across_instances(self, ks, tmp_path):
        ks.disable_strategy("cam", reason="test")
        ks2 = LiveKillSwitch(state_path=tmp_path / "kill_switch.json")
        assert ks2.is_strategy_disabled("cam")

    def test_disable_idempotent(self, ks):
        r1 = ks.disable_strategy("cam", reason="loss")
        r2 = ks.disable_strategy("cam", reason="repeat")
        assert r1["was_already_disabled"] is False
        assert r2["was_already_disabled"] is True
        assert ks.get_disabled_strategies() == ["cam"]

    def test_disable_emits_alert_once(self, ks):
        ks.disable_strategy("cam", reason="test")
        # idempotent call should NOT spam alert
        ks.disable_strategy("cam", reason="test2")
        assert len(ks.alerts) == 1, "Alert must fire only on first disable"

    def test_disable_multiple_strategies_isolated(self, ks):
        ks.disable_strategy("cam", reason="x")
        ks.disable_strategy("gor", reason="y")
        assert ks.is_strategy_disabled("cam")
        assert ks.is_strategy_disabled("gor")
        assert not ks.is_strategy_disabled("other")

    def test_disable_records_history(self, ks):
        ks.disable_strategy("cam", reason="WF rejected", trigger_type="WF_REJECT")
        hist = ks.get_history()
        assert any(h["action"] == "DISABLE_STRATEGY" for h in hist)
        event = next(h for h in hist if h["action"] == "DISABLE_STRATEGY")
        assert event["strategy_id"] == "cam"
        assert event["trigger_type"] == "WF_REJECT"

    def test_disable_empty_strategy_id_rejected(self, ks):
        result = ks.disable_strategy("", reason="test")
        assert "error" in result


class TestEnableStrategy:
    def test_enable_removes_from_set(self, ks):
        ks.disable_strategy("cam", reason="test")
        result = ks.enable_strategy("cam", signer="marc")
        assert result["was_disabled"] is True
        assert not ks.is_strategy_disabled("cam")

    def test_enable_not_in_disabled_set_is_noop(self, ks):
        result = ks.enable_strategy("unknown", signer="marc")
        assert result["was_disabled"] is False

    def test_enable_records_signer_in_history(self, ks):
        ks.disable_strategy("cam", reason="test")
        ks.enable_strategy("cam", signer="marc")
        hist = ks.get_history()
        event = next(h for h in hist if h["action"] == "ENABLE_STRATEGY")
        assert event["signer"] == "marc"


class TestPreOrderGuardIntegration:
    def test_pre_order_guard_blocks_scoped_disabled_strategy(self, tmp_path, monkeypatch):
        """pre_order_guard must refuse new orders for a scoped-disabled strat."""
        from core.governance.pre_order_guard import pre_order_guard, GuardError
        from unittest.mock import patch, MagicMock

        # Pre-disable cross_asset_momentum on the shared state file
        state_file = Path(__file__).resolve().parent.parent / "data" / "kill_switch_state.json"
        # Create a temporary LiveKillSwitch pointing to its canonical state
        # to simulate the "production" disabled state
        ks = LiveKillSwitch(state_path=state_file)
        was_already = ks.is_strategy_disabled("cross_asset_momentum")
        ks.disable_strategy("cross_asset_momentum", reason="E2 test", trigger_type="TEST")
        try:
            mock_health = MagicMock()
            mock_health.status.value = "GREEN"
            with patch("core.governance.book_health.get_book_health",
                       return_value=mock_health):
                with pytest.raises(GuardError) as exc:
                    pre_order_guard(
                        book="ibkr_futures",
                        strategy_id="cross_asset_momentum",
                        paper_mode=False,
                    )
            assert "scoped-disabled" in str(exc.value) or "E2" in str(exc.value)
        finally:
            # Cleanup
            if not was_already:
                ks.enable_strategy("cross_asset_momentum", signer="test_cleanup")

    def test_pre_order_guard_paper_mode_ignores_disable(self, tmp_path):
        """Paper mode must not consult live kill switch (different scope)."""
        from core.governance.pre_order_guard import pre_order_guard
        state_file = Path(__file__).resolve().parent.parent / "data" / "kill_switch_state.json"
        ks = LiveKillSwitch(state_path=state_file)
        was_already = ks.is_strategy_disabled("cross_asset_momentum")
        ks.disable_strategy("cross_asset_momentum", reason="E2 test", trigger_type="TEST")
        try:
            # Paper mode should NOT raise (live kill switch not consulted)
            pre_order_guard(
                book="ibkr_eu", strategy_id="eu_gap_open",
                paper_mode=True,
            )
        finally:
            if not was_already:
                ks.enable_strategy("cross_asset_momentum", signer="test_cleanup")
