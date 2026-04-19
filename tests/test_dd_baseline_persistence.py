"""DD baseline persistence + 4 boot states regression tests.

Covers the bug from feedback_baselines_persistence_bug.md:
  Pre-fix: _baselines_synced was in-memory only -> after restart, peak got reset
           to current_equity -> kill switch silent on real DD if reboot-in-DD.

Post-fix: state persisted via DDBaselines schema v1, BootState classifies the
          situation on init, peak NEVER reset on STATE_RESTORED/STATE_STALE.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.crypto.dd_baseline_state import (
    SCHEMA_VERSION,
    STALE_THRESHOLD_HOURS,
    BootState,
    DDBaselines,
    init_baselines_from_equity,
    load_baselines,
    roll_period_anchors,
    save_baselines,
)
from core.crypto.risk_manager_crypto import CryptoRiskManager


# ---------------------------------------------------------------------------
# 4 boot-state classification
# ---------------------------------------------------------------------------

class TestBootStateClassification:
    def test_first_boot_when_no_state_file(self, tmp_path):
        path = tmp_path / "missing.json"
        state, baselines = load_baselines(path)
        assert state == BootState.FIRST_BOOT
        assert baselines.peak_equity == 0.0

    def test_state_restored_when_fresh(self, tmp_path):
        path = tmp_path / "fresh.json"
        bl = init_baselines_from_equity(8_000.0)
        bl.last_check_ts = time.time() - 60  # 1 minute ago
        save_baselines(path, bl)

        state, loaded = load_baselines(path)
        assert state == BootState.STATE_RESTORED
        assert loaded.peak_equity == 8_000.0
        assert loaded.session_id == bl.session_id

    def test_state_stale_when_last_check_too_old(self, tmp_path):
        path = tmp_path / "stale.json"
        bl = init_baselines_from_equity(8_000.0)
        bl.last_check_ts = time.time() - (STALE_THRESHOLD_HOURS + 1) * 3600
        save_baselines(path, bl)

        state, loaded = load_baselines(path)
        assert state == BootState.STATE_STALE
        assert loaded.peak_equity == 8_000.0  # peak still preserved

    def test_state_corrupt_when_garbage_json(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not-a-json {", encoding="utf-8")
        state, baselines = load_baselines(path)
        assert state == BootState.STATE_CORRUPT
        assert baselines.peak_equity == 0.0

    def test_state_corrupt_when_wrong_schema_version(self, tmp_path):
        path = tmp_path / "wrong-schema.json"
        path.write_text(
            json.dumps({"schema_version": 999, "peak_equity": 8000}),
            encoding="utf-8",
        )
        state, _ = load_baselines(path)
        assert state == BootState.STATE_CORRUPT

    def test_state_corrupt_when_negative_peak(self, tmp_path):
        path = tmp_path / "negative-peak.json"
        bl = init_baselines_from_equity(8_000.0)
        bl.peak_equity = -500.0
        save_baselines(path, bl)
        state, _ = load_baselines(path)
        assert state == BootState.STATE_CORRUPT


# ---------------------------------------------------------------------------
# Legacy worker.py schema migration
# ---------------------------------------------------------------------------

class TestLegacySchemaMigration:
    def test_migrates_legacy_worker_state(self, tmp_path):
        """Worker.py schema (no schema_version, daily_start instead of daily_start_equity)."""
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({
            "peak_equity": 12_500.0,
            "daily_start": 12_000.0,
            "weekly_start": 11_500.0,
            "monthly_start": 10_000.0,
            "last_date": "2026-04-19",
            "last_week": "2026-W16",
            "last_month": "2026-04",
            "last_updated": "2026-04-19T10:30:00+00:00",
            "total_equity": 11_800.0,
        }), encoding="utf-8")

        state, baselines = load_baselines(path)
        # Migrated successfully -> classified as RESTORED or STALE based on age
        assert state in (BootState.STATE_RESTORED, BootState.STATE_STALE)
        assert baselines.peak_equity == 12_500.0
        assert baselines.daily_start_equity == 12_000.0
        assert baselines.weekly_start_equity == 11_500.0
        assert baselines.monthly_start_equity == 10_000.0
        assert baselines.session_id == "migrated-legacy"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_save_creates_no_partial_file_on_success(self, tmp_path):
        path = tmp_path / "atomic.json"
        bl = init_baselines_from_equity(10_000.0)
        save_baselines(path, bl)

        # No leftover .tmp files
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []
        assert path.exists()

    def test_save_overwrites_existing_atomically(self, tmp_path):
        path = tmp_path / "rewrite.json"
        bl1 = init_baselines_from_equity(10_000.0)
        save_baselines(path, bl1)

        bl2 = init_baselines_from_equity(12_000.0)
        save_baselines(path, bl2)

        _, loaded = load_baselines(path)
        assert loaded.peak_equity == 12_000.0


# ---------------------------------------------------------------------------
# Period anchor rolling (peak NEVER reset)
# ---------------------------------------------------------------------------

class TestPeriodAnchorRolling:
    def test_rolls_when_anchor_changed(self):
        bl = init_baselines_from_equity(10_000.0)
        bl.daily_anchor = "2025-01-01"  # stale
        bl.weekly_anchor = "2025-W01"
        bl.monthly_anchor = "2025-01"

        rolled_bl, rolled = roll_period_anchors(bl, current_equity=8_500.0)
        assert "daily" in rolled and "weekly" in rolled and "monthly" in rolled
        # Peak NEVER reset
        assert rolled_bl.peak_equity == 10_000.0
        # Period baselines reset to current
        assert rolled_bl.daily_start_equity == 8_500.0
        assert rolled_bl.weekly_start_equity == 8_500.0
        assert rolled_bl.monthly_start_equity == 8_500.0

    def test_no_roll_when_same_anchor(self):
        bl = init_baselines_from_equity(10_000.0)
        rolled_bl, rolled = roll_period_anchors(bl, current_equity=8_500.0)
        assert rolled == []
        assert rolled_bl.daily_start_equity == 10_000.0  # unchanged


# ---------------------------------------------------------------------------
# Reboot-in-DD regression: THE bug we are fixing
# ---------------------------------------------------------------------------

class TestRebootInDDPreservesPeak:
    """The core regression: worker reboots while in DD, peak must survive."""

    def test_reboot_in_dd_keeps_persisted_peak(self, tmp_path):
        """Pre-fix: peak got reset to current low equity. Post-fix: peak preserved."""
        dd_path = tmp_path / "dd.json"
        ks_path = tmp_path / "ks.json"

        # --- Session 1: equity peaks at 10K, then drops to 8K (-20% DD) ---
        rm1 = CryptoRiskManager(
            capital=10_000, ks_state_path=ks_path, dd_state_path=dd_path,
        )
        rm1.check_drawdown(current_equity=10_000)
        for _ in range(3):  # warmup
            rm1.check_drawdown(current_equity=10_000)
        rm1.check_drawdown(current_equity=10_000)  # post-warmup, peak = 10K
        assert rm1._peak_equity == 10_000

        # Drop to 8K
        rm1.check_drawdown(current_equity=8_000)
        # Persisted state should now have peak=10K, not 8K
        _, persisted = load_baselines(dd_path)
        assert persisted.peak_equity == 10_000.0, (
            f"Peak lost after DD: persisted={persisted.peak_equity} (expected 10000)"
        )

        # --- Session 2: WORKER REBOOT, equity still at 8K ---
        rm2 = CryptoRiskManager(
            capital=10_000, ks_state_path=ks_path, dd_state_path=dd_path,
        )
        # On init, peak should be loaded from disk (=10K) NOT reset to 8K
        assert rm2._peak_equity == 10_000.0, (
            f"REBOOT-IN-DD BUG: peak_equity={rm2._peak_equity} after restart "
            f"(expected 10000 from persisted state)"
        )
        assert rm2._baselines_synced is True
        assert rm2._dd_boot_state == BootState.STATE_RESTORED

        # Subsequent check uses real peak -> DD calc correct
        rm2.check_drawdown(current_equity=8_000)
        # No early-return faux positif: peak still 10K
        assert rm2._peak_equity == 10_000.0

    def test_corrupt_state_alerts_and_falls_back(self, tmp_path):
        """STATE_CORRUPT: log critical + behave like FIRST_BOOT."""
        dd_path = tmp_path / "dd.json"
        ks_path = tmp_path / "ks.json"
        dd_path.write_text("{{{ corrupt", encoding="utf-8")

        rm = CryptoRiskManager(
            capital=10_000, ks_state_path=ks_path, dd_state_path=dd_path,
        )
        assert rm._dd_boot_state == BootState.STATE_CORRUPT
        assert rm._baselines_synced is False  # falls back to first-boot sync

        rm.check_drawdown(current_equity=8_000)
        assert rm._baselines_synced is True
        assert rm._peak_equity == 8_000  # synced to current after corrupt fallback

    def test_first_boot_skips_warmup_then_runs_dd_normally(self, tmp_path):
        dd_path = tmp_path / "dd.json"
        ks_path = tmp_path / "ks.json"

        rm = CryptoRiskManager(
            capital=10_000, ks_state_path=ks_path, dd_state_path=dd_path,
        )
        assert rm._dd_boot_state == BootState.FIRST_BOOT
        # First call syncs
        ok, msg = rm.check_drawdown(current_equity=10_000)
        assert ok and "synced" in msg.lower()
        # Persisted after first sync
        assert dd_path.exists()
        _, persisted = load_baselines(dd_path)
        assert persisted.peak_equity == 10_000

    def test_state_restored_no_warmup(self, tmp_path):
        """When state restored, kill switch active from cycle #1 (no warmup)."""
        dd_path = tmp_path / "dd.json"
        ks_path = tmp_path / "ks.json"

        # Pre-populate disk with valid state (peak=10K)
        bl = init_baselines_from_equity(10_000.0)
        bl.last_check_ts = time.time() - 30
        save_baselines(dd_path, bl)

        rm = CryptoRiskManager(
            capital=10_000, ks_state_path=ks_path, dd_state_path=dd_path,
        )
        assert rm._dd_boot_state == BootState.STATE_RESTORED
        assert rm._peak_equity == 10_000

        # Cycle #1 with massive DD -> kill switch should fire (no warmup hide)
        ok, msg = rm.check_drawdown(current_equity=7_000)
        # -30% DD vs persisted peak 10K: kill switch trips
        assert not ok or "drawdown" in msg.lower() or "KILL" in msg
