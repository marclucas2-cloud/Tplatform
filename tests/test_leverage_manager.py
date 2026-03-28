"""
Tests unitaires — LeverageManager.

Couvre :
  - Phase 1 defaults (1.5x max)
  - Reject leverage above max
  - Approve leverage within limits
  - Can advance with all conditions met
  - Cannot advance with missing conditions
  - Phase state persistence (save/load)
  - All 4 phases in sequence
  - Min duration check
  - Status report completeness
  - Edge: already at max phase
  - Edge: advance without KPI validation
  - Edge: corrupt state file recovery
  - Phase history tracking
"""

import json
import os
import sys
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.leverage_manager import LeverageManager, PHASE_ORDER


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def config_path():
    """Path to the real leverage_schedule.yaml config."""
    return ROOT / "config" / "leverage_schedule.yaml"


@pytest.fixture
def tmp_state(tmp_path):
    """Temporary state file path."""
    return tmp_path / "leverage_state.json"


@pytest.fixture
def manager(config_path, tmp_state):
    """Fresh LeverageManager with temporary state."""
    return LeverageManager(config_path=config_path, state_path=tmp_state)


@pytest.fixture
def full_kpi_phase1():
    """KPI dict that satisfies all PHASE_1 advance conditions."""
    return {
        "sharpe_30d": 1.5,
        "drawdown_pct": 2.0,
        "trades": 80,
        "critical_bugs": 0,
    }


@pytest.fixture
def full_kpi_phase2():
    """KPI dict that satisfies all PHASE_2 advance conditions."""
    return {
        "sharpe_60d": 1.2,
        "drawdown_pct": 4.0,
        "trades": 150,
        "cost_ratio": 0.20,
    }


@pytest.fixture
def full_kpi_phase3():
    """KPI dict that satisfies all PHASE_3 advance conditions."""
    return {
        "sharpe_90d": 1.5,
        "capital": 22000,
    }


@pytest.fixture
def full_kpi_phase4():
    """KPI dict that satisfies all PHASE_4 advance conditions."""
    return {
        "capital": 30000,
        "sharpe_90d": 2.0,
    }


# =============================================================================
# PHASE 1 DEFAULTS
# =============================================================================

class TestPhase1Defaults:
    def test_initial_phase_is_soft_launch(self, manager):
        assert manager.current_phase == "SOFT_LAUNCH"

    def test_initial_max_leverage_is_1_0(self, manager):
        assert manager.max_leverage == 1.0

    def test_check_leverage_within_limit(self, manager):
        result = manager.check_leverage(0.8)
        assert result["allowed"] is True
        assert result["current_phase"] == "SOFT_LAUNCH"
        assert result["max_leverage"] == 1.0
        assert result["reason"] == "OK"

    def test_check_leverage_at_exact_limit(self, manager):
        result = manager.check_leverage(1.0)
        assert result["allowed"] is True


# =============================================================================
# LEVERAGE REJECTION
# =============================================================================

class TestLeverageRejection:
    def test_reject_leverage_above_max(self, manager):
        result = manager.check_leverage(1.5)
        assert result["allowed"] is False
        assert "exceeds" in result["reason"]
        assert "SOFT_LAUNCH" in result["reason"]

    def test_reject_leverage_way_above(self, manager):
        result = manager.check_leverage(5.0)
        assert result["allowed"] is False


# =============================================================================
# PHASE ADVANCEMENT CONDITIONS
# =============================================================================

class TestCanAdvancePhase:
    def test_can_advance_all_conditions_met(self, manager, full_kpi_phase1, tmp_state):
        # Set phase start to 31 days ago to satisfy min_duration
        state = {
            "current_phase": "PHASE_1",
            "phase_start_date": (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat(),
            "history": [],
        }
        with open(tmp_state, "w") as f:
            json.dump(state, f)
        manager._state = manager._load_state()

        result = manager.can_advance_phase(full_kpi_phase1)
        assert result["can_advance"] is True
        assert result["next_phase"] == "PHASE_2"
        assert len(result["missing_conditions"]) == 0
        assert len(result["met_conditions"]) > 0

    def test_cannot_advance_missing_conditions(self, manager, tmp_state):
        # Set sufficient duration
        state = {
            "current_phase": "PHASE_1",
            "phase_start_date": (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat(),
            "history": [],
        }
        with open(tmp_state, "w") as f:
            json.dump(state, f)
        manager._state = manager._load_state()

        # KPI with low Sharpe and high drawdown
        kpi = {
            "sharpe_30d": 0.5,
            "drawdown_pct": 6.0,
            "trades": 30,
            "critical_bugs": 2,
        }
        result = manager.can_advance_phase(kpi)
        assert result["can_advance"] is False
        assert len(result["missing_conditions"]) > 0

    def test_cannot_advance_min_duration_not_met(self, manager, full_kpi_phase1):
        # Default state: just created, 0 days in phase
        result = manager.can_advance_phase(full_kpi_phase1)
        # min_duration_days is 30, so should fail
        assert result["can_advance"] is False
        assert any("min_duration_days" in m for m in result["missing_conditions"])

    def test_cannot_advance_partial_conditions(self, manager, tmp_state):
        state = {
            "current_phase": "PHASE_1",
            "phase_start_date": (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat(),
            "history": [],
        }
        with open(tmp_state, "w") as f:
            json.dump(state, f)
        manager._state = manager._load_state()

        # Only Sharpe met, rest missing
        kpi = {"sharpe_30d": 1.5}
        result = manager.can_advance_phase(kpi)
        assert result["can_advance"] is False
        assert len(result["met_conditions"]) >= 1
        assert len(result["missing_conditions"]) >= 1


# =============================================================================
# PHASE STATE PERSISTENCE
# =============================================================================

class TestStatePersistence:
    def test_save_and_load_state(self, config_path, tmp_state):
        manager1 = LeverageManager(config_path=config_path, state_path=tmp_state)
        assert manager1.current_phase == "SOFT_LAUNCH"

        # Force advance (no KPI validation)
        manager1.advance_phase()
        assert manager1.current_phase == "PHASE_1"

        # Create new manager from same state file
        manager2 = LeverageManager(config_path=config_path, state_path=tmp_state)
        assert manager2.current_phase == "PHASE_1"
        assert manager2.max_leverage == 1.5

    def test_state_file_created_on_save(self, config_path, tmp_path):
        state_path = tmp_path / "subdir" / "state.json"
        assert not state_path.exists()

        manager = LeverageManager(config_path=config_path, state_path=state_path)
        manager._save_state()
        assert state_path.exists()

    def test_corrupt_state_file_recovery(self, config_path, tmp_state):
        # Write garbage to state file
        with open(tmp_state, "w") as f:
            f.write("{invalid json!!!")

        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        # Should recover to default SOFT_LAUNCH
        assert manager.current_phase == "SOFT_LAUNCH"

    def test_invalid_phase_in_state_resets(self, config_path, tmp_state):
        with open(tmp_state, "w") as f:
            json.dump({"current_phase": "PHASE_99", "phase_start_date": "2026-01-01"}, f)

        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        assert manager.current_phase == "SOFT_LAUNCH"


# =============================================================================
# FULL PHASE SEQUENCE
# =============================================================================

class TestFullPhaseSequence:
    def test_advance_all_5_phases(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        assert manager.current_phase == "SOFT_LAUNCH"
        assert manager.max_leverage == 1.0

        manager.advance_phase()
        assert manager.current_phase == "PHASE_1"
        assert manager.max_leverage == 1.5

        manager.advance_phase()
        assert manager.current_phase == "PHASE_2"
        assert manager.max_leverage == 2.0

        manager.advance_phase()
        assert manager.current_phase == "PHASE_3"
        assert manager.max_leverage == 2.5

        manager.advance_phase()
        assert manager.current_phase == "PHASE_4"
        assert manager.max_leverage == 3.0

    def test_leverage_limits_increase_with_phases(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)

        # SOFT_LAUNCH: 1.0x max, 1.5x rejected
        assert manager.check_leverage(1.0)["allowed"] is True
        assert manager.check_leverage(1.5)["allowed"] is False

        manager.advance_phase()  # PHASE_1
        # Phase 1: 1.5x allowed, 2.0x rejected
        assert manager.check_leverage(1.5)["allowed"] is True
        assert manager.check_leverage(2.0)["allowed"] is False

        manager.advance_phase()  # PHASE_2
        # Phase 2: 2.0x allowed, 2.5x rejected
        assert manager.check_leverage(2.0)["allowed"] is True
        assert manager.check_leverage(2.5)["allowed"] is False

        manager.advance_phase()  # PHASE_3
        assert manager.check_leverage(2.5)["allowed"] is True
        assert manager.check_leverage(3.0)["allowed"] is False

        manager.advance_phase()  # PHASE_4
        assert manager.check_leverage(3.0)["allowed"] is True

    def test_history_tracked_across_advances(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        manager.advance_phase()
        manager.advance_phase()

        status = manager.get_status()
        assert len(status["history"]) == 2
        assert status["history"][0]["from_phase"] == "SOFT_LAUNCH"
        assert status["history"][0]["to_phase"] == "PHASE_1"
        assert status["history"][1]["from_phase"] == "PHASE_1"
        assert status["history"][1]["to_phase"] == "PHASE_2"


# =============================================================================
# STATUS REPORT
# =============================================================================

class TestStatusReport:
    def test_status_has_required_keys(self, manager):
        status = manager.get_status()
        expected_keys = {
            "current_phase", "max_leverage", "days_in_phase",
            "min_duration_days", "duration_met", "advance_conditions",
            "next_phase", "next_max_leverage", "phase_start_date", "history",
        }
        assert expected_keys.issubset(set(status.keys()))

    def test_status_soft_launch_values(self, manager):
        status = manager.get_status()
        assert status["current_phase"] == "SOFT_LAUNCH"
        assert status["max_leverage"] == 1.0
        assert status["next_phase"] == "PHASE_1"
        assert status["next_max_leverage"] == 1.5
        assert status["min_duration_days"] == 5

    def test_status_phase4_no_next(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        # Advance to PHASE_4 (5 phases: SOFT_LAUNCH -> P1 -> P2 -> P3 -> P4)
        for _ in range(4):
            manager.advance_phase()

        status = manager.get_status()
        assert status["current_phase"] == "PHASE_4"
        assert status["next_phase"] is None
        assert status["next_max_leverage"] is None


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    def test_cannot_advance_beyond_phase4(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        for _ in range(4):  # SOFT_LAUNCH -> P1 -> P2 -> P3 -> P4
            manager.advance_phase()

        assert manager.current_phase == "PHASE_4"
        with pytest.raises(ValueError, match="Cannot advance beyond"):
            manager.advance_phase()

    def test_can_advance_returns_false_at_max_phase(self, config_path, tmp_state):
        manager = LeverageManager(config_path=config_path, state_path=tmp_state)
        for _ in range(4):  # SOFT_LAUNCH -> P1 -> P2 -> P3 -> P4
            manager.advance_phase()

        result = manager.can_advance_phase({"sharpe_90d": 2.0, "capital": 50000})
        assert result["can_advance"] is False
        assert result["next_phase"] is None
        assert "maximum phase" in result.get("reason", "")

    def test_advance_with_kpi_validation_fails(self, manager):
        """advance_phase with KPI that don't meet conditions raises ValueError."""
        with pytest.raises(ValueError, match="Conditions not met"):
            manager.advance_phase(kpi={"sharpe_30d": 0.1, "drawdown_pct": 10.0})

    def test_empty_kpi_dict(self, manager, tmp_state):
        """Empty KPI means all conditions are missing."""
        state = {
            "current_phase": "PHASE_1",
            "phase_start_date": (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat(),
            "history": [],
        }
        with open(tmp_state, "w") as f:
            json.dump(state, f)
        manager._state = manager._load_state()

        result = manager.can_advance_phase({})
        assert result["can_advance"] is False
        # All advance conditions should be missing
        assert len(result["missing_conditions"]) >= 4
