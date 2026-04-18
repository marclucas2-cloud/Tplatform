"""Global test fixtures — isolate state files from production paths.

Audit 2026-04-17 Bloc 12: tests MUST NOT write to real state files.
This conftest patches CryptoKillSwitch._STATE_PATH to a tmp dir
for ALL tests, preventing pollution of data/crypto_kill_switch_state.json.

Audit 2026-04-18 P1.2: tests MUST NOT inherit BINANCE_TESTNET=false from
the production env (which would trigger the LIVE_CONFIRMED fail-closed).
Force testnet=true unless test explicitly overrides.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _isolate_kill_switch_state(tmp_path):
    """Redirect CryptoKillSwitch state to tmp_path for every test."""
    tmp_state = tmp_path / "crypto_kill_switch_state.json"
    try:
        from core.crypto.risk_manager_crypto import CryptoKillSwitch
        with patch.object(CryptoKillSwitch, "_STATE_PATH", tmp_state):
            yield
    except ImportError:
        yield


@pytest.fixture(autouse=True)
def _force_safe_broker_env(monkeypatch):
    """Force broker test env to safe defaults.

    Without this, tests inherit BINANCE_TESTNET=false / PAPER_TRADING=false
    from the worker production env, triggering fail-closed checks added in
    audit P1.2/P1.3. Tests that need live mode explicitly should use
    monkeypatch.setenv to override.
    """
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("PAPER_TRADING", "true")
    yield
