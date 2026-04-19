"""Regression tests for C1 plan 9.0 (2026-04-19).

Audit ChatGPT: "distinguer explicitement first_boot, state_restored,
state_stale, state_corrupt dans la logique de decision, pas seulement
dans le state".

Tests verify that check_drawdown() applies a warmup budget proportional
to the BootState, not just a single fresh-init heuristic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.crypto.dd_baseline_state import BootState, DDBaselines


@pytest.fixture
def rm(tmp_path):
    """Build a CryptoRiskManager with ephemeral state files."""
    from core.crypto.risk_manager_crypto import CryptoRiskManager, CryptoRiskLimits

    limits = CryptoRiskLimits()
    # state_path must be writable; point it to tmp
    mgr = CryptoRiskManager(
        capital=10000.0,
        limits=limits,
        ks_state_path=tmp_path / "kill_switch.json",
        dd_state_path=tmp_path / "dd_state.json",
    )
    return mgr


class TestWarmupByBootState:
    def test_first_boot_has_3_cycle_warmup(self, rm):
        """FIRST_BOOT = no persisted state -> 3 cycles warmup."""
        rm._dd_boot_state = BootState.FIRST_BOOT
        rm._baselines_synced = False
        rm._check_count = 0

        # Cycle 1: sync (early return before warmup logic)
        ok, msg = rm.check_drawdown(10000.0)
        assert ok
        assert "synced" in msg.lower()

        # Cycles 2-4: warmup
        for i in range(1, 4):
            ok, msg = rm.check_drawdown(10000.0)
            assert ok
            assert f"warmup {i}/3" in msg
            assert "first_boot" in msg

    def test_state_corrupt_has_3_cycle_warmup(self, rm):
        """STATE_CORRUPT behaves like FIRST_BOOT (state untrustworthy)."""
        rm._dd_boot_state = BootState.STATE_CORRUPT
        rm._baselines_synced = False
        rm._check_count = 0

        ok, msg = rm.check_drawdown(10000.0)  # sync
        ok, msg = rm.check_drawdown(10000.0)  # warmup 1/3
        assert "warmup 1/3" in msg
        assert "state_corrupt" in msg

    def test_state_stale_has_1_cycle_warmup(self, rm):
        """STATE_STALE = state loaded but > 24h old -> 1 cycle warmup (short)."""
        rm._dd_boot_state = BootState.STATE_STALE
        rm._baselines_synced = True  # STATE_STALE means baselines loaded
        rm._peak_equity = 11000.0
        rm._daily_start_equity = 10500.0
        rm._weekly_start_equity = 10500.0
        rm._monthly_start_equity = 10500.0
        rm._baselines = DDBaselines(peak_equity=11000.0)
        rm._check_count = 0

        # Cycle 1: warmup 1/1 (not immediately active)
        ok, msg = rm.check_drawdown(10500.0)
        assert ok
        assert "warmup 1/1" in msg
        assert "state_stale" in msg

    def test_state_restored_has_no_warmup(self, rm):
        """STATE_RESTORED = fresh state < 24h -> immediate DD checks."""
        rm._dd_boot_state = BootState.STATE_RESTORED
        rm._baselines_synced = True
        rm._peak_equity = 10000.0
        rm._daily_start_equity = 10000.0
        rm._weekly_start_equity = 10000.0
        rm._monthly_start_equity = 10000.0
        rm._baselines = DDBaselines(peak_equity=10000.0)
        rm._check_count = 0

        # No warmup — first cycle runs normal DD check
        ok, msg = rm.check_drawdown(10000.0)
        assert ok
        assert "warmup" not in msg.lower()

    def test_state_stale_post_warmup_runs_normal_dd(self, rm):
        """After STATE_STALE warmup, normal DD check applies."""
        rm._dd_boot_state = BootState.STATE_STALE
        rm._baselines_synced = True
        rm._peak_equity = 10000.0
        rm._daily_start_equity = 10000.0
        rm._weekly_start_equity = 10000.0
        rm._monthly_start_equity = 10000.0
        rm._baselines = DDBaselines(peak_equity=10000.0)
        rm._check_count = 0

        # Cycle 1: warmup
        rm.check_drawdown(10000.0)
        # Cycle 2: normal DD path
        ok, msg = rm.check_drawdown(10000.0)
        assert "warmup" not in msg.lower()
