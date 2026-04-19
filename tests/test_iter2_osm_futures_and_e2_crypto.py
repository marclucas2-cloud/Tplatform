"""Tests iter2 — G4 OSM wire futures + G5 E2 defense-en-profondeur crypto.

Plan 9.5/10 (2026-04-19) verification que:
  G4: futures_runner.py appelle OrderTracker create_order/validate/submit/fill
  G5: run_crypto_cycle skip early si LiveKillSwitch.is_strategy_disabled
"""
from __future__ import annotations

from pathlib import Path

import pytest


class TestG4FuturesRunnerOSMWire:
    """Verify futures_runner.py has OSM tracker wire around IBKR placeOrder."""

    def test_futures_runner_imports_order_tracker_accessor(self):
        """worker.py exposes get_order_tracker() for futures_runner to consume."""
        import worker
        assert hasattr(worker, "get_order_tracker"), (
            "worker.py must expose get_order_tracker() for futures_runner OSM wire"
        )
        assert hasattr(worker, "set_order_tracker"), (
            "worker.py must expose set_order_tracker() for main() assign"
        )

    def test_futures_runner_code_uses_osm_tracker(self):
        """G4: futures_runner.py code contains OSM create_order + validate + submit + fill."""
        path = Path(__file__).resolve().parent.parent / "core" / "worker" / "cycles" / "futures_runner.py"
        src = path.read_text(encoding="utf-8")
        # Must call create_order with broker="ibkr"
        assert 'create_order(' in src, "futures_runner must create OSM order"
        assert 'broker="ibkr"' in src, "OSM create_order must tag broker='ibkr'"
        # Must validate after create
        assert '_tracker.validate(' in src or 'validate(_osm_order.order_id' in src
        # Must submit + fill on success
        assert '_tracker.submit(' in src or 'submit(_osm_order.order_id' in src
        assert '_tracker.fill(' in src or 'fill(' in src
        # Must error on entry not Filled
        assert '_tracker.error(' in src or 'error(_osm_order.order_id' in src

    def test_futures_runner_has_sl_invariant(self):
        """G4: futures fill must satisfy has_sl invariant (standalone OCA SL placed)."""
        path = Path(__file__).resolve().parent.parent / "core" / "worker" / "cycles" / "futures_runner.py"
        src = path.read_text(encoding="utf-8")
        # fill() call must pass has_sl=True (SL OCA is placed before OSM fill transition)
        assert "has_sl=True" in src, (
            "futures OSM fill must assert has_sl=True (OCA SL placed before fill)"
        )


class TestG5E2CryptoCycleDefenseInDepth:
    """Verify run_crypto_cycle has early-skip on scoped-disabled strategy."""

    def test_crypto_cycle_checks_livekillswitch_is_strategy_disabled(self):
        """G5: run_crypto_cycle must check is_strategy_disabled before order creation."""
        path = Path(__file__).resolve().parent.parent / "worker.py"
        src = path.read_text(encoding="utf-8")
        # Must import LiveKillSwitch in the crypto cycle path
        assert "LiveKillSwitch" in src
        # Must call is_strategy_disabled and `continue` on True
        assert "is_strategy_disabled(strat_id)" in src, (
            "run_crypto_cycle must check is_strategy_disabled(strat_id) early"
        )
        # Must reference scoped-disabled / E2 in the crypto cycle comment/message
        assert "scoped-disabled" in src or "E2 per-strategy" in src or "scoped_disable" in src


class TestG4G5IntegrationWithOrderTracker:
    """OrderTracker API compatibility for iter2 integration points."""

    def test_order_tracker_create_order_signature(self, tmp_path):
        from core.execution.order_tracker import OrderTracker
        tracker = OrderTracker(state_path=tmp_path / "ot.json")
        osm = tracker.create_order(
            symbol="MES", side="BUY", quantity=1,
            broker="ibkr", strategy="cross_asset_momentum",
        )
        assert osm is not None
        assert osm.symbol == "MES"
        # Must be able to validate + submit + fill in sequence
        assert tracker.validate(osm.order_id, risk_approved=True)
        assert tracker.submit(osm.order_id, broker_order_id="IBKR-12345")
        assert tracker.fill(osm.order_id, has_sl=True, sl_order_id="IBKR-12346")

    def test_order_tracker_error_transition(self, tmp_path):
        """G4 entry not Filled path: tracker.error() must succeed from SUBMITTED."""
        from core.execution.order_tracker import OrderTracker
        tracker = OrderTracker(state_path=tmp_path / "ot.json")
        osm = tracker.create_order(
            symbol="MES", side="BUY", quantity=1,
            broker="ibkr", strategy="test",
        )
        tracker.validate(osm.order_id, risk_approved=True)
        tracker.submit(osm.order_id, broker_order_id="IBKR-1")
        # Entry not Filled -> error
        assert tracker.error(osm.order_id)

    def test_kill_switch_is_strategy_disabled_api(self, tmp_path):
        """G5 API signature: LiveKillSwitch.is_strategy_disabled(strat_id) -> bool."""
        from core.kill_switch_live import LiveKillSwitch
        ks = LiveKillSwitch(state_path=tmp_path / "ks.json")
        assert ks.is_strategy_disabled("nonexistent") is False
        ks.disable_strategy("test_strat", reason="unit test")
        assert ks.is_strategy_disabled("test_strat") is True
        ks.enable_strategy("test_strat", signer="test")
        assert ks.is_strategy_disabled("test_strat") is False
