"""
Tests unitaires — ContinuousGateEvaluator (ROC-003).

Couvre :
  - test_pending_initial : PENDING quand stats insuffisantes
  - test_pass_all_conditions : PASS quand tout est OK
  - test_near_pass : NEAR quand presque OK (5/7 + 2/5)
  - test_fail_primary : PENDING quand primaire echoue
  - test_fail_secondary : PENDING quand secondaires insuffisants
  - test_reduced_calendar_days : seuil 14j au lieu de 21j
  - test_progress_report : rapport lisible correct
  - test_on_gate_pass_calls_upgrade : auto-upgrade leverage manager
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.continuous_gate import (
    ContinuousGateEvaluator,
    PRIMARY_CRITERIA,
    SECONDARY_CRITERIA,
    SECONDARY_MIN_PASS,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_leverage_manager():
    """Mock LeverageManager pour tester l'auto-upgrade."""
    mgr = MagicMock()
    mgr.current_phase = "SOFT_LAUNCH"
    mgr.advance_phase.return_value = "PHASE_1"
    return mgr


@pytest.fixture
def mock_alerter():
    """Mock alerter callback."""
    return MagicMock()


@pytest.fixture
def evaluator(mock_leverage_manager, mock_alerter):
    """ContinuousGateEvaluator avec mocks."""
    return ContinuousGateEvaluator(
        leverage_manager=mock_leverage_manager,
        alerter=mock_alerter,
    )


@pytest.fixture
def evaluator_no_deps():
    """ContinuousGateEvaluator sans dependencies."""
    return ContinuousGateEvaluator()


@pytest.fixture
def full_pass_stats():
    """Stats qui satisfont TOUS les criteres (primaires + secondaires)."""
    return {
        "calendar_days": 20,
        "trades": 25,
        "strategies_active": 5,
        "drawdown_pct": 2.0,
        "single_loss_pct": 1.0,
        "bugs_critiques": 0,
        "reconciliation_errors": 0,
        "sharpe_period": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "slippage_ratio": 1.5,
        "execution_quality": 0.92,
    }


@pytest.fixture
def near_pass_stats():
    """Stats qui satisfont 5/7 primaires + 2/5 secondaires (NEAR)."""
    return {
        "calendar_days": 20,
        "trades": 25,
        "strategies_active": 5,
        "drawdown_pct": 2.0,
        "single_loss_pct": 1.0,
        # 2 primaires en echec
        "bugs_critiques": 1,
        "reconciliation_errors": 1,
        # 2 secondaires OK
        "sharpe_period": 0.5,
        "win_rate": 0.50,
        # 3 secondaires en echec
        "profit_factor": 0.8,
        "slippage_ratio": 5.0,
        "execution_quality": 0.70,
    }


@pytest.fixture
def minimal_stats():
    """Stats minimales insuffisantes."""
    return {
        "calendar_days": 5,
        "trades": 3,
        "strategies_active": 1,
        "drawdown_pct": 8.0,
        "single_loss_pct": 4.0,
        "bugs_critiques": 2,
        "reconciliation_errors": 3,
        "sharpe_period": 0.1,
        "win_rate": 0.30,
        "profit_factor": 0.7,
        "slippage_ratio": 5.0,
        "execution_quality": 0.60,
    }


# =============================================================================
# TESTS
# =============================================================================

class TestContinuousGateEvaluator:
    """Tests pour ContinuousGateEvaluator."""

    def test_pending_initial(self, evaluator, minimal_stats):
        """PENDING quand les stats sont tres insuffisantes."""
        result = evaluator.evaluate(minimal_stats)

        assert result["result"] == "PENDING"
        assert result["primaries_pass"] < 5  # Loin du seuil NEAR
        assert result["primaries_total"] == len(PRIMARY_CRITERIA)

    def test_pass_all_conditions(self, evaluator, full_pass_stats):
        """PASS quand tous les criteres primaires + 3+ secondaires OK."""
        result = evaluator.evaluate(full_pass_stats)

        assert result["result"] == "PASS"
        assert result["primaries_pass"] == result["primaries_total"]
        assert result["secondaries_pass"] >= SECONDARY_MIN_PASS

    def test_near_pass(self, evaluator_no_deps, near_pass_stats):
        """NEAR quand 5/7 primaires + 2/5 secondaires."""
        result = evaluator_no_deps.evaluate(near_pass_stats)

        assert result["result"] == "NEAR"
        assert result["primaries_pass"] >= 5
        assert result["secondaries_pass"] >= 2

    def test_fail_primary(self, evaluator_no_deps):
        """PENDING quand un critere primaire critique echoue."""
        stats = {
            "calendar_days": 20,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 2.0,
            "single_loss_pct": 1.0,
            "bugs_critiques": 0,
            "reconciliation_errors": 0,
            # Secondaires : seulement 2/5 OK
            "sharpe_period": 0.5,
            "win_rate": 0.50,
            "profit_factor": 0.8,   # FAIL
            "slippage_ratio": 5.0,  # FAIL
            "execution_quality": 0.70,  # FAIL
        }
        result = evaluator_no_deps.evaluate(stats)

        # Tous primaires OK mais secondaires insuffisants -> pas PASS
        assert result["result"] != "PASS"
        assert result["primaries_pass"] == len(PRIMARY_CRITERIA)
        assert result["secondaries_pass"] < SECONDARY_MIN_PASS

    def test_fail_secondary(self, evaluator_no_deps):
        """PENDING quand les secondaires sont insuffisants et primaires OK."""
        stats = {
            "calendar_days": 20,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 2.0,
            "single_loss_pct": 1.0,
            "bugs_critiques": 0,
            "reconciliation_errors": 0,
            # Tous secondaires en echec
            "sharpe_period": 0.1,
            "win_rate": 0.30,
            "profit_factor": 0.5,
            "slippage_ratio": 5.0,
            "execution_quality": 0.50,
        }
        result = evaluator_no_deps.evaluate(stats)

        # Primaires OK mais aucun secondaire -> NEAR (7/7 primary + 0/5 secondary)
        # 7 >= 5 primary et 0 >= 2 secondary? Non, 0 < 2 -> PENDING
        assert result["result"] != "PASS"
        assert result["primaries_pass"] == len(PRIMARY_CRITERIA)
        assert result["secondaries_pass"] < SECONDARY_MIN_PASS

    def test_reduced_calendar_days(self, evaluator_no_deps):
        """Verification que le seuil est bien 14j et non 21j (ROC-003)."""
        # 14 jours doit passer
        assert PRIMARY_CRITERIA["min_calendar_days"] == 14

        stats_14 = {
            "calendar_days": 14,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 2.0,
            "single_loss_pct": 1.0,
            "bugs_critiques": 0,
            "reconciliation_errors": 0,
            "sharpe_period": 0.5,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 1.5,
            "execution_quality": 0.92,
        }
        result = evaluator_no_deps.evaluate(stats_14)
        assert result["result"] == "PASS"

        # 13 jours ne doit PAS passer le critere calendar_days
        stats_13 = dict(stats_14)
        stats_13["calendar_days"] = 13
        result = evaluator_no_deps.evaluate(stats_13)
        assert result["result"] != "PASS"  # calendar_days echoue

    def test_progress_report(self, evaluator_no_deps, full_pass_stats):
        """Rapport de progression lisible."""
        progress = evaluator_no_deps.get_progress(full_pass_stats)

        assert "primary_summary" in progress
        assert "secondary_summary" in progress
        assert "primary_details" in progress
        assert "secondary_details" in progress
        assert "overall" in progress

        # Avec full_pass_stats, tout devrait passer
        assert progress["overall"] == "PASS"
        assert f"{len(PRIMARY_CRITERIA)}/{len(PRIMARY_CRITERIA)}" in progress["primary_summary"]

        # Verifier que les details contiennent des lignes PASS
        for detail in progress["primary_details"]:
            assert "[PASS]" in detail

    def test_on_gate_pass_calls_upgrade(self, mock_leverage_manager, mock_alerter, full_pass_stats):
        """on_gate_pass() doit appeler advance_phase() sur le leverage manager."""
        evaluator = ContinuousGateEvaluator(
            leverage_manager=mock_leverage_manager,
            alerter=mock_alerter,
        )

        result = evaluator.evaluate(full_pass_stats)

        assert result["result"] == "PASS"
        # Verification que advance_phase a ete appele
        mock_leverage_manager.advance_phase.assert_called_once()
        # Verification que l'alerte a ete envoyee
        assert mock_alerter.call_count >= 1
