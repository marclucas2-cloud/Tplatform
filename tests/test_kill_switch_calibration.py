"""
Tests unitaires — RISK-002 : Calibration kill switch par Monte Carlo.

Couvre :
  - Le calibrateur produit un seuil valide (negatif)
  - Le taux de faux positifs est < 5% avec le seuil optimal
  - Les strategies low-Sharpe ont des seuils plus serres
  - Calibration avec rendements reels synthetiques
  - Edge cases (peu de donnees, rendements constants)
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.kill_switch_calibration import KillSwitchCalibrator

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def calibrator():
    """Instance de KillSwitchCalibrator avec seed fixe pour reproductibilite."""
    return KillSwitchCalibrator(n_simulations=5_000, seed=42)


@pytest.fixture
def high_sharpe_returns():
    """Rendements d'une strategie haute performance (Sharpe ~3).
    Mean = +0.15%/jour, vol = 0.8% -> annualise Sharpe ~3.
    """
    np.random.seed(100)
    return list(np.random.normal(0.0015, 0.008, 252))


@pytest.fixture
def low_sharpe_returns():
    """Rendements d'une strategie low-performance (Sharpe ~0.5).
    Mean = +0.02%/jour, vol = 0.6% -> annualise Sharpe ~0.5.
    """
    np.random.seed(200)
    return list(np.random.normal(0.0002, 0.006, 252))


@pytest.fixture
def negative_sharpe_returns():
    """Rendements d'une strategie perdante (Sharpe < 0).
    Mean = -0.05%/jour, vol = 1.5%.
    """
    np.random.seed(300)
    return list(np.random.normal(-0.0005, 0.015, 252))


# =============================================================================
# TEST 1 : Le calibrateur produit un seuil valide
# =============================================================================

class TestCalibratorProducesValidThreshold:
    def test_calibrator_produces_valid_threshold(self, calibrator, high_sharpe_returns):
        """Le seuil optimal doit etre negatif (drawdown = perte)."""
        result = calibrator.calibrate("opex_gamma", high_sharpe_returns)

        assert result["optimal_threshold"] < 0, (
            f"Seuil optimal devrait etre negatif, got {result['optimal_threshold']}"
        )
        assert result["strategy"] == "opex_gamma"
        assert result["n_observations"] == len(high_sharpe_returns)

    def test_threshold_has_expected_keys(self, calibrator, high_sharpe_returns):
        """Le resultat contient toutes les cles attendues."""
        result = calibrator.calibrate("test_strat", high_sharpe_returns)

        expected_keys = {
            "strategy", "current_threshold", "optimal_threshold",
            "false_positive_rate_current", "false_positive_rate_optimal",
            "detection_rate_optimal", "percentile_5", "percentile_1",
            "n_observations", "recommendation",
        }
        assert expected_keys.issubset(result.keys()), (
            f"Cles manquantes : {expected_keys - result.keys()}"
        )

    def test_percentile_1_less_than_percentile_5(self, calibrator, high_sharpe_returns):
        """Le percentile 1% doit etre plus negatif que le percentile 5%."""
        result = calibrator.calibrate("test", high_sharpe_returns)
        assert result["percentile_1"] <= result["percentile_5"], (
            f"P1 {result['percentile_1']} devrait etre <= P5 {result['percentile_5']}"
        )


# =============================================================================
# TEST 2 : Taux de faux positifs < 5% avec seuil optimal
# =============================================================================

class TestFalsePositiveRateUnder5Percent:
    def test_false_positive_rate_under_5_percent(self, calibrator, high_sharpe_returns):
        """Le seuil optimal doit avoir un taux de faux positifs <= 5%."""
        result = calibrator.calibrate("opex_gamma", high_sharpe_returns)

        assert result["false_positive_rate_optimal"] <= 0.05 + 1e-6, (
            f"FP rate {result['false_positive_rate_optimal']:.2%} > 5%"
        )

    def test_fp_rate_for_low_sharpe(self, calibrator, low_sharpe_returns):
        """Le FP rate doit aussi etre < 5% pour les strategies low Sharpe."""
        result = calibrator.calibrate("mean_rev", low_sharpe_returns)

        assert result["false_positive_rate_optimal"] <= 0.05 + 1e-6, (
            f"FP rate low sharpe {result['false_positive_rate_optimal']:.2%} > 5%"
        )

    def test_detection_rate_reasonable(self, calibrator, high_sharpe_returns):
        """Le taux de detection (degradation) doit etre raisonnablement eleve."""
        result = calibrator.calibrate("opex_gamma", high_sharpe_returns)

        # La detection depend des donnees, mais on veut au minimum > 50%
        assert result["detection_rate_optimal"] > 0.50, (
            f"Detection rate {result['detection_rate_optimal']:.2%} trop faible"
        )


# =============================================================================
# TEST 3 : Strategies low-Sharpe ont des seuils plus serres
# =============================================================================

class TestThresholdTighterForLowSharpe:
    def test_threshold_tighter_for_low_sharpe_strategies(
        self, calibrator, high_sharpe_returns, low_sharpe_returns
    ):
        """Les strategies low-Sharpe (plus de risque de deviance) doivent avoir
        des seuils plus proches de zero (plus serres / plus conservateurs).

        Intuition : une strategie performante a un "coussin" de profits
        qui eloigne le seuil, alors qu'une strategie mediocre est deja
        plus proche de la zone de perte.
        """
        result_high = calibrator.calibrate("high_sharpe", high_sharpe_returns)
        result_low = calibrator.calibrate("low_sharpe", low_sharpe_returns)

        # Le seuil low-sharpe doit etre plus proche de zero (moins negatif)
        # car la strategie genere moins de P&L positif sur 5j
        assert result_low["optimal_threshold"] > result_high["optimal_threshold"], (
            f"Low sharpe threshold {result_low['optimal_threshold']:.4%} devrait etre "
            f"plus proche de zero que high sharpe {result_high['optimal_threshold']:.4%}"
        )

    def test_negative_sharpe_more_extreme_threshold(self, calibrator, negative_sharpe_returns, high_sharpe_returns):
        """Une strategie Sharpe negatif avec plus de vol a un seuil plus extreme
        (plus negatif) car ses trajectoires descendent plus bas sur 5j."""
        result_neg = calibrator.calibrate("negative", negative_sharpe_returns)
        result_high = calibrator.calibrate("high_sharpe", high_sharpe_returns)

        # Sharpe negatif + vol elevee : le min 5j rolling est plus extreme
        # Donc le seuil optimal est plus eloigne de zero
        assert result_neg["optimal_threshold"] < result_high["optimal_threshold"], (
            f"Negative sharpe threshold {result_neg['optimal_threshold']:.4%} devrait etre "
            f"plus extreme que high sharpe {result_high['optimal_threshold']:.4%}"
        )


# =============================================================================
# TEST 4 : Calibration pour les 5 strategies les plus actives
# =============================================================================

class TestCalibrationTopStrategies:
    def test_calibrate_all_five_strategies(self, calibrator):
        """Simule la calibration des 5 strategies les plus actives."""
        np.random.seed(42)
        strategy_returns = {
            "opex_gamma": list(np.random.normal(0.002, 0.008, 252)),       # Sharpe ~4
            "vwap_micro": list(np.random.normal(0.0008, 0.008, 252)),      # Sharpe ~1.6
            "orb_v2": list(np.random.normal(0.0006, 0.010, 252)),          # Sharpe ~1.0
            "dow_seasonal": list(np.random.normal(0.0007, 0.007, 252)),    # Sharpe ~1.6
            "gap_continuation": list(np.random.normal(0.0012, 0.010, 252)), # Sharpe ~1.9
        }

        results = calibrator.calibrate_all(strategy_returns, current_threshold=-0.02)

        assert len(results) == 5, f"Attendu 5 resultats, got {len(results)}"
        for name, result in results.items():
            assert result["optimal_threshold"] < 0, (
                f"{name}: seuil doit etre negatif"
            )
            assert result["false_positive_rate_optimal"] <= 0.06, (
                f"{name}: FP rate {result['false_positive_rate_optimal']:.2%} trop eleve"
            )
            assert "recommendation" in result

    def test_calibrate_all_saves_to_file(self, calibrator, tmp_path):
        """calibrate_all sauvegarde le JSON au chemin specifie."""
        np.random.seed(42)
        strategy_returns = {
            "test_strat": list(np.random.normal(0.001, 0.01, 100)),
        }
        output = str(tmp_path / "calibration.json")
        results = calibrator.calibrate_all(strategy_returns, output_path=output)

        import json
        with open(output) as f:
            saved = json.load(f)
        assert "test_strat" in saved
        assert saved["test_strat"]["optimal_threshold"] < 0


# =============================================================================
# TEST 5 : Edge cases
# =============================================================================

class TestKillSwitchEdgeCases:
    def test_too_few_observations(self, calibrator):
        """Avec moins d'observations que la fenetre, retour par defaut."""
        result = calibrator.calibrate("short_data", [0.01, 0.02])

        assert result["optimal_threshold"] == -0.02  # fallback au current
        assert "Pas assez de donnees" in result["recommendation"]

    def test_empty_returns(self, calibrator):
        """Avec une liste vide, retour par defaut."""
        result = calibrator.calibrate("empty", [])

        assert result["optimal_threshold"] == -0.02
        assert result["n_observations"] == 0

    def test_constant_returns(self, calibrator):
        """Des rendements constants (vol = 0) ne crashent pas.
        Avec des rendements tous positifs et identiques, le min rolling
        est positif (= pas de drawdown). Le calibrateur ne crashe pas."""
        returns = [0.001] * 100
        result = calibrator.calibrate("constant", returns)

        # Le calibrateur retourne un resultat sans crash
        assert isinstance(result["optimal_threshold"], float)
        assert "recommendation" in result

    def test_rolling_min_pnl_basic(self, calibrator):
        """Test unitaire de _rolling_min_pnl."""
        returns = np.array([0.01, -0.02, 0.01, -0.03, 0.02])
        min_pnl = calibrator._rolling_min_pnl(returns, window=3)

        # Fenetres de 3 :
        # [0.01, -0.02, 0.01] -> sum = 0.00
        # [-0.02, 0.01, -0.03] -> sum = -0.04
        # [0.01, -0.03, 0.02] -> sum = 0.00
        assert abs(min_pnl - (-0.04)) < 1e-9, f"Min rolling PnL = {min_pnl}, attendu -0.04"

    def test_recommendation_text(self, calibrator, high_sharpe_returns):
        """La recommandation contient un texte actionnable."""
        result = calibrator.calibrate("test", high_sharpe_returns, current_threshold=-0.02)
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 10  # pas vide
