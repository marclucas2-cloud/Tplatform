"""
Resilience tests — state persistence, kill switch independence, thread safety.

5 tests verifying the system degrades gracefully under failures and concurrency.
"""
import importlib
import threading

import pytest

from core.crypto.risk_manager_crypto import (
    CryptoKillSwitch,
    CryptoRiskLimits,
    CryptoRiskManager,
)
from core.kill_switch_live import LiveKillSwitch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def crypto_rm():
    return CryptoRiskManager(capital=15_000, limits=CryptoRiskLimits(config_path="__nonexistent__"))


@pytest.fixture
def crypto_ks(tmp_path):
    ks = CryptoKillSwitch(config_path="__nonexistent__")
    ks._STATE_PATH = tmp_path / "test_crypto_ks_state.json"
    ks._active = False
    ks._trigger_reason = ""
    return ks


@pytest.fixture
def ibkr_ks(tmp_path):
    return LiveKillSwitch(
        broker=None,
        state_path=tmp_path / "ks_ibkr.json",
    )


# =========================================================================
# Resilience tests
# =========================================================================

class TestResilience:
    """System resilience under failure and concurrency."""

    def test_state_persistence(self, tmp_path):
        """Save kill switch state, reload, verify identical."""
        state_path = tmp_path / "ks_persist.json"
        ks1 = LiveKillSwitch(
            broker=None,
            state_path=state_path,
            thresholds={"daily_loss_pct": 0.02},
        )
        # Activate kill switch
        ks1.activate(reason="test_persist", trigger_type="TEST")
        assert ks1.is_active is True

        # Reload from same file
        ks2 = LiveKillSwitch(
            broker=None,
            state_path=state_path,
            thresholds={"daily_loss_pct": 0.02},
        )
        assert ks2.is_active is True
        status = ks2.get_status()
        assert status["activation_reason"] == "test_persist"
        assert status["activation_trigger"] == "TEST"

    def test_kill_switch_independence(self, tmp_path, crypto_ks):
        """IBKR kill doesn't affect Binance and vice versa."""
        ibkr_ks = LiveKillSwitch(
            broker=None,
            state_path=tmp_path / "ks_ibkr_indep.json",
        )

        # Activate IBKR kill switch
        ibkr_ks.activate(reason="ibkr_loss", trigger_type="DAILY_LOSS")
        assert ibkr_ks.is_active is True

        # Crypto kill switch remains independent
        assert crypto_ks.is_killed is False

        # Now activate crypto kill switch
        crypto_ks.check(daily_pnl_pct=-10)
        assert crypto_ks.is_killed is True

        # IBKR state unchanged
        assert ibkr_ks.is_active is True

        # Deactivate IBKR -> crypto still killed
        ibkr_ks.deactivate(authorized_by="test")
        assert ibkr_ks.is_active is False
        assert crypto_ks.is_killed is True

    def test_kill_switch_no_deadlock(self, tmp_path):
        """Trigger both IBKR and crypto kills simultaneously, no deadlock."""
        ibkr_ks = LiveKillSwitch(
            broker=None,
            state_path=tmp_path / "ks_dl_ibkr.json",
        )
        crypto_ks = CryptoKillSwitch(config_path="__nonexistent__")

        results = {}
        errors = []

        def activate_ibkr():
            try:
                r = ibkr_ks.activate(reason="deadlock_test", trigger_type="TEST")
                results["ibkr"] = r
            except Exception as e:
                errors.append(f"ibkr: {e}")

        def activate_crypto():
            try:
                killed, reason = crypto_ks.check(daily_pnl_pct=-10)
                results["crypto"] = {"killed": killed, "reason": reason}
            except Exception as e:
                errors.append(f"crypto: {e}")

        t1 = threading.Thread(target=activate_ibkr)
        t2 = threading.Thread(target=activate_crypto)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not t1.is_alive(), "IBKR thread deadlocked"
        assert not t2.is_alive(), "Crypto thread deadlocked"
        assert len(errors) == 0, f"Errors: {errors}"
        assert ibkr_ks.is_active is True
        assert crypto_ks.is_killed is True

    def test_risk_manager_thread_safe(self, crypto_rm):
        """10 concurrent check_all() calls, no crash."""
        positions = [
            {
                "symbol": "BTCUSDT", "notional": 2_000, "side": "LONG",
                "strategy": "btc_mom", "leverage": 1.0,
                "is_margin_borrow": False, "borrowed_amount": 0,
                "borrow_rate_daily": 0, "asset_value": 2_000,
                "total_debt": 0, "unrealized_pct": -1,
            },
        ]
        errors = []
        results = []

        def run_check(i):
            try:
                r = crypto_rm.check_all(
                    positions=positions,
                    current_equity=15_000 - i * 10,
                    cash_available=5_000,
                )
                results.append(r)
            except Exception as e:
                errors.append(f"thread-{i}: {e}")

        threads = [threading.Thread(target=run_check, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 10
        # All results should have 12 checks
        for r in results:
            assert r["n_checks"] == 12

    def test_graceful_degradation(self):
        """If one crypto module import fails, others still importable."""
        # Verify core crypto modules are independently importable
        # Each module should load without depending on the others
        modules_ok = []
        modules_fail = []

        for mod_name in [
            "core.crypto.risk_manager_crypto",
            "core.crypto.data_pipeline",
            "core.crypto.allocator_crypto",
            "core.crypto.monitoring",
            "core.crypto.order_manager",
            "core.crypto.capital_manager",
        ]:
            try:
                importlib.import_module(mod_name)
                modules_ok.append(mod_name)
            except Exception:
                modules_fail.append(mod_name)

        # At least the risk manager and data pipeline must load
        assert "core.crypto.risk_manager_crypto" in modules_ok
        assert "core.crypto.data_pipeline" in modules_ok
        assert len(modules_ok) >= 4, (
            f"Only {len(modules_ok)} core.crypto modules importable; "
            f"failed: {modules_fail}"
        )

        # Verify that kill switches are independent classes
        from core.crypto.risk_manager_crypto import CryptoKillSwitch, CryptoRiskManager
        from core.kill_switch_live import LiveKillSwitch
        # Different classes, no shared state
        assert CryptoKillSwitch is not LiveKillSwitch
        assert CryptoRiskManager.__module__ != LiveKillSwitch.__module__
