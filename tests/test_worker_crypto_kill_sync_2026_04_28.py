"""Source-level guards for crypto kill switch sync with live resets.

Regression we want to prevent:
- a false live kill arms the crypto kill switch as collateral damage
- live gets manually reset
- crypto stays permanently blocked even though its reason was inherited
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_crypto_kill_auto_resets_when_reason_is_derived_from_live_and_live_is_inactive():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert 'kill_reason.startswith("live_kill_")' in src
    assert 'kill_reason.startswith("emergency_LEVEL_")' in src
    assert "_live_ks_inactive = not LiveKillSwitch().is_active" in src
    assert 'risk_mgr.kill_switch.reset(_authorized_by="live_kill_reset_sync")' in src


def test_crypto_kill_24h_auto_reset_uses_guarded_reset_api():
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert 'risk_mgr.kill_switch.reset(_authorized_by="age_gt_24h_auto_reset")' in src
    assert "risk_mgr.kill_switch._active = False" not in src
