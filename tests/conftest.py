"""Global test fixtures — isolate state files from production paths.

Audit 2026-04-17 Bloc 12: tests MUST NOT write to real state files.
This conftest patches CryptoKillSwitch._STATE_PATH to a tmp dir
for ALL tests, preventing pollution of data/crypto_kill_switch_state.json.
"""
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
