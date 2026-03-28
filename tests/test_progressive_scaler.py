"""
Tests unitaires — ProgressiveScaler (ROC-009).

Couvre :
  - Fraction initiale 1/8 Kelly
  - Passage au niveau 1 a 6 trades
  - Passage au niveau 2 a 11 trades
  - Pas de passage si DD trop eleve
  - Revert si DD augmente
  - Gate M1 PASS complet
  - Gate M1 FAIL criteres primaires
  - Gate M1 FAIL criteres secondaires
"""

import sys
from pathlib import Path

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.progressive_scaler import ProgressiveScaler


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def scaler():
    """ProgressiveScaler avec progression par defaut."""
    return ProgressiveScaler()


@pytest.fixture
def gate_m1_stats_pass():
    """Stats qui passent tous les criteres Gate M1."""
    return {
        "calendar_days": 21,
        "trades": 25,
        "strategies": 4,
        "max_dd_pct": 2.5,
        "max_single_loss_pct": 1.0,
        "bugs": 0,
        "recon_errors": 0,
        "sharpe": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "slippage_ratio": 1.5,
        "execution_quality": 0.92,
    }


@pytest.fixture
def gate_m1_stats_fail_primary():
    """Stats qui echouent sur les criteres primaires Gate M1."""
    return {
        "calendar_days": 10,    # < 14 requis
        "trades": 8,            # < 15 requis
        "strategies": 2,        # < 3 requis
        "max_dd_pct": 2.0,
        "max_single_loss_pct": 1.0,
        "bugs": 0,
        "recon_errors": 0,
        "sharpe": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "slippage_ratio": 1.5,
        "execution_quality": 0.92,
    }


@pytest.fixture
def gate_m1_stats_fail_secondary():
    """Stats qui passent les primaires mais echouent sur les secondaires (2/5 < 3)."""
    return {
        "calendar_days": 21,
        "trades": 25,
        "strategies": 4,
        "max_dd_pct": 2.5,
        "max_single_loss_pct": 1.0,
        "bugs": 0,
        "recon_errors": 0,
        # Seulement 2 secondaires passes (sharpe, win_rate)
        "sharpe": 0.5,            # PASS (> 0.3)
        "win_rate": 0.50,         # PASS (> 0.42)
        "profit_factor": 0.8,     # FAIL (< 1.1)
        "slippage_ratio": 5.0,    # FAIL (> 3.0)
        "execution_quality": 0.70, # FAIL (< 0.85)
    }


# =============================================================================
# TESTS
# =============================================================================

def test_initial_fraction(scaler):
    """Au demarrage (0 trades), la fraction est 1/8 Kelly."""
    result = scaler.get_current_fraction(
        trade_count=0, max_drawdown_pct=0.0, critical_bugs=0
    )

    assert result["fraction"] == pytest.approx(0.125, abs=1e-4)
    assert result["level_name"] == "SOFT_LAUNCH"
    assert result["level"] == 0
    assert result["next_level_at"] == 6  # Prochain niveau a 6 trades


def test_level_up_at_6_trades(scaler):
    """A 6 trades avec DD < 2% et 0 bugs -> passage a 3/16 Kelly."""
    result = scaler.get_current_fraction(
        trade_count=6, max_drawdown_pct=1.5, critical_bugs=0
    )

    assert result["fraction"] == pytest.approx(0.1875, abs=1e-4)
    assert result["level_name"] == "RAMP_UP_1"
    assert result["level"] == 1
    assert result["next_level_at"] == 11  # Prochain niveau a 11 trades


def test_level_up_at_11_trades(scaler):
    """A 11 trades avec DD < 2% (satisfait niveau 1 et 2) et 0 bugs -> passage a 1/4 Kelly."""
    result = scaler.get_current_fraction(
        trade_count=11, max_drawdown_pct=1.8, critical_bugs=0
    )

    assert result["fraction"] == pytest.approx(0.25, abs=1e-4)
    assert result["level_name"] == "RAMP_UP_2"
    assert result["level"] == 2


def test_no_level_up_if_dd_too_high(scaler):
    """Pas de passage au niveau 1 si DD > 2%."""
    # D'abord, rester au niveau 0 malgre les trades
    result = scaler.get_current_fraction(
        trade_count=8, max_drawdown_pct=3.0, critical_bugs=0
    )

    # DD 3.0% > max 2.0% pour RAMP_UP_1 -> reste a SOFT_LAUNCH
    assert result["fraction"] == pytest.approx(0.125, abs=1e-4)
    assert result["level_name"] == "SOFT_LAUNCH"
    assert result["level"] == 0


def test_revert_on_dd_increase(scaler):
    """Si le DD augmente au-dela du seuil, retour au niveau precedent."""
    # D'abord, monter au niveau 1
    result1 = scaler.get_current_fraction(
        trade_count=8, max_drawdown_pct=1.5, critical_bugs=0
    )
    assert result1["level"] == 1
    assert result1["level_name"] == "RAMP_UP_1"

    # Puis le DD depasse 2% -> retour au niveau 0
    result2 = scaler.get_current_fraction(
        trade_count=8, max_drawdown_pct=2.5, critical_bugs=0
    )
    assert result2["level"] == 0
    assert result2["level_name"] == "SOFT_LAUNCH"
    assert result2["fraction"] == pytest.approx(0.125, abs=1e-4)

    # Verifier aussi should_revert directement
    assert scaler.should_revert(current_level=1, max_drawdown_pct=2.5) is True
    assert scaler.should_revert(current_level=1, max_drawdown_pct=1.5) is False
    assert scaler.should_revert(current_level=0, max_drawdown_pct=10.0) is False


def test_gate_m1_pass(scaler, gate_m1_stats_pass):
    """Gate M1 passe avec tous les criteres satisfaits."""
    result = scaler.evaluate_gate_m1(gate_m1_stats_pass)

    assert result["passed"] is True
    assert result["primary_passed"] is True
    assert result["secondary_passed"] is True
    assert result["secondary_met"] == 5  # Tous les 5 passes
    assert result["secondary_required"] == 3
    assert "PASSED" in result["recommendation"]


def test_gate_m1_fail_primary(scaler, gate_m1_stats_fail_primary):
    """Gate M1 echoue si un critere primaire n'est pas satisfait."""
    result = scaler.evaluate_gate_m1(gate_m1_stats_fail_primary)

    assert result["passed"] is False
    assert result["primary_passed"] is False
    # Verifier que les criteres defaillants sont identifies
    failed_names = [
        r["name"] for r in result["primary_results"] if not r["passed"]
    ]
    assert "min_calendar_days" in failed_names
    assert "min_trades" in failed_names
    assert "min_strategies" in failed_names
    assert "FAILED" in result["recommendation"]


def test_gate_m1_fail_secondary(scaler, gate_m1_stats_fail_secondary):
    """Gate M1 echoue si moins de 3/5 criteres secondaires passes."""
    result = scaler.evaluate_gate_m1(gate_m1_stats_fail_secondary)

    assert result["passed"] is False
    assert result["primary_passed"] is True   # Primaires OK
    assert result["secondary_passed"] is False  # Secondaires FAIL
    assert result["secondary_met"] == 2  # Seulement sharpe + win_rate
    assert result["secondary_required"] == 3
    assert "FAILED" in result["recommendation"]
