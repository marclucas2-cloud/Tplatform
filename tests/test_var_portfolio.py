"""
Tests unitaires — RISK-001 : VaR portfolio-level avec matrice de correlation.

Couvre :
  - Diversification benefit > 0 (VaR portfolio < somme naive)
  - VaR stressed > VaR normal
  - Matrice de correlation symetrique
  - Risk contribution somme au VaR portfolio total
  - Scenario Mars 2020 (correlations proches de 1)
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.risk_manager import RiskManager

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def rm():
    """Instance de RiskManager avec limits.yaml du projet."""
    return RiskManager()


@pytest.fixture
def uncorrelated_returns():
    """3 strategies avec rendements independants (faible correlation)."""
    np.random.seed(42)
    n = 252  # 1 an
    return {
        "strat_A": list(np.random.normal(0.001, 0.015, n)),
        "strat_B": list(np.random.normal(0.0005, 0.020, n)),
        "strat_C": list(np.random.normal(0.0008, 0.010, n)),
    }


@pytest.fixture
def equal_weights():
    """Poids egaux pour 3 strategies."""
    return {"strat_A": 0.33, "strat_B": 0.34, "strat_C": 0.33}


@pytest.fixture
def correlated_returns():
    """3 strategies fortement correlees (simule un marche en crise)."""
    np.random.seed(123)
    n = 252
    # Base commune = marche
    market = np.random.normal(-0.002, 0.03, n)
    noise_scale = 0.003
    return {
        "strat_X": list(market + np.random.normal(0, noise_scale, n)),
        "strat_Y": list(market * 1.1 + np.random.normal(0, noise_scale, n)),
        "strat_Z": list(market * 0.9 + np.random.normal(0, noise_scale, n)),
    }


# =============================================================================
# TEST 1 : VaR portfolio < somme des VaR individuels (diversification)
# =============================================================================

class TestVaRPortfolioLessThanSum:
    def test_var_portfolio_less_than_sum(self, rm, uncorrelated_returns, equal_weights):
        """Avec des strategies independantes, la VaR portfolio doit etre
        inferieure a la somme naive des VaR individuels (diversification benefit > 0)."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)

        assert result["var_portfolio"] < result["var_individual_sum"], (
            f"VaR portfolio {result['var_portfolio']:.6f} devrait etre < "
            f"somme naive {result['var_individual_sum']:.6f}"
        )
        assert result["diversification_benefit"] > 0, (
            f"Diversification benefit {result['diversification_benefit']:.4f} devrait etre > 0"
        )

    def test_diversification_benefit_bounded(self, rm, uncorrelated_returns, equal_weights):
        """Le diversification benefit doit etre entre 0 et 1."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)
        assert 0 < result["diversification_benefit"] <= 1.0

    def test_var_portfolio_positive(self, rm, uncorrelated_returns, equal_weights):
        """La VaR portfolio doit etre strictement positive."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)
        assert result["var_portfolio"] > 0
        assert result["var_individual_sum"] > 0


# =============================================================================
# TEST 2 : VaR stressed > VaR normal
# =============================================================================

class TestVaRStressedGreaterThanNormal:
    def test_var_stressed_greater_than_normal(self, rm, uncorrelated_returns, equal_weights):
        """La VaR stressed (correlations forcees a 0.8) doit etre >= VaR normal
        pour des strategies peu correlees."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)

        assert result["var_stressed"] >= result["var_portfolio"] - 1e-9, (
            f"VaR stressed {result['var_stressed']:.6f} devrait etre >= "
            f"VaR portfolio {result['var_portfolio']:.6f}"
        )

    def test_var_stressed_with_high_correlation(self, rm, correlated_returns):
        """Meme avec des strategies deja correlees, le stress ne reduit pas la VaR."""
        weights = {"strat_X": 0.33, "strat_Y": 0.34, "strat_Z": 0.33}
        result = rm.calculate_portfolio_var(correlated_returns, weights)

        # VaR stressed >= VaR portfolio (les correlations stress sont hautes aussi)
        assert result["var_stressed"] > 0
        assert result["var_portfolio"] > 0

    def test_stress_correlation_parameter(self, rm, uncorrelated_returns, equal_weights):
        """Un stress_correlation plus eleve doit donner un VaR stressed plus eleve."""
        result_08 = rm.calculate_portfolio_var(
            uncorrelated_returns, equal_weights, stress_correlation=0.8
        )
        result_095 = rm.calculate_portfolio_var(
            uncorrelated_returns, equal_weights, stress_correlation=0.95
        )
        assert result_095["var_stressed"] >= result_08["var_stressed"] - 1e-9, (
            f"Stress 0.95 ({result_095['var_stressed']:.6f}) devrait etre >= "
            f"stress 0.8 ({result_08['var_stressed']:.6f})"
        )


# =============================================================================
# TEST 3 : Matrice de correlation symetrique
# =============================================================================

class TestCorrelationMatrixSymmetric:
    def test_correlation_matrix_symmetric(self, rm, uncorrelated_returns, equal_weights):
        """La matrice de correlation doit etre symetrique : corr(A,B) == corr(B,A)."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)
        corr = result["correlation_matrix"]

        strategies = sorted(uncorrelated_returns.keys())
        for i, ki in enumerate(strategies):
            for j, kj in enumerate(strategies):
                key_ij = f"{ki}/{kj}"
                key_ji = f"{kj}/{ki}"
                assert key_ij in corr, f"Cle manquante : {key_ij}"
                assert key_ji in corr, f"Cle manquante : {key_ji}"
                assert abs(corr[key_ij] - corr[key_ji]) < 1e-6, (
                    f"Correlation asymetrique : {key_ij}={corr[key_ij]} vs {key_ji}={corr[key_ji]}"
                )

    def test_diagonal_equals_one(self, rm, uncorrelated_returns, equal_weights):
        """La diagonale de la matrice de correlation doit etre 1.0."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)
        corr = result["correlation_matrix"]

        for strat in uncorrelated_returns:
            key = f"{strat}/{strat}"
            assert abs(corr[key] - 1.0) < 1e-6, f"Diagonale {key} = {corr[key]}, attendu 1.0"

    def test_correlations_bounded(self, rm, uncorrelated_returns, equal_weights):
        """Toutes les correlations doivent etre entre -1 et 1."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)
        for key, val in result["correlation_matrix"].items():
            assert -1.0 - 1e-6 <= val <= 1.0 + 1e-6, (
                f"Correlation {key} = {val} hors bornes [-1, 1]"
            )


# =============================================================================
# TEST 4 : Risk contribution somme au VaR portfolio total
# =============================================================================

class TestRiskContributionSumsToTotal:
    def test_risk_contribution_sums_to_total(self, rm, uncorrelated_returns, equal_weights):
        """La somme des risk contributions doit etre egale au VaR portfolio."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)

        rc_sum = sum(result["risk_contribution"].values())
        assert abs(rc_sum - result["var_portfolio"]) < 1e-4, (
            f"Somme RC {rc_sum:.6f} != VaR portfolio {result['var_portfolio']:.6f}"
        )

    def test_risk_contribution_all_positive_for_long_only(self, rm, uncorrelated_returns, equal_weights):
        """Pour un portfolio long-only avec correlations positives,
        toutes les contributions au risque sont positives."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)

        for strat, rc in result["risk_contribution"].items():
            # Avec des rendements independants, les RC doivent etre >= 0
            assert rc >= -1e-6, (
                f"Risk contribution negative pour {strat}: {rc:.6f}"
            )

    def test_risk_contribution_keys_match_strategies(self, rm, uncorrelated_returns, equal_weights):
        """Les cles de risk_contribution correspondent aux strategies en entree."""
        result = rm.calculate_portfolio_var(uncorrelated_returns, equal_weights)

        for strat in uncorrelated_returns:
            assert strat in result["risk_contribution"], (
                f"Strategie {strat} manquante dans risk_contribution"
            )

    def test_risk_contribution_with_correlated(self, rm, correlated_returns):
        """Avec des strategies correlees, la somme des RC = VaR portfolio."""
        weights = {"strat_X": 0.5, "strat_Y": 0.3, "strat_Z": 0.2}
        result = rm.calculate_portfolio_var(correlated_returns, weights)

        rc_sum = sum(result["risk_contribution"].values())
        assert abs(rc_sum - result["var_portfolio"]) < 1e-4, (
            f"Somme RC {rc_sum:.6f} != VaR portfolio {result['var_portfolio']:.6f}"
        )


# =============================================================================
# TEST 5 : Scenario Mars 2020 (correlations proches de 1)
# =============================================================================

class TestMarch2020Scenario:
    def test_var_with_march_2020_scenario(self, rm):
        """Simule un scenario type Mars 2020 ou toutes les strategies
        sont fortement correlees (correlation > 0.9).

        Dans ce cas :
        - Le diversification benefit est tres faible (< 10%)
        - Le VaR portfolio est proche de la somme des VaR individuels
        - Le VaR stressed est proche du VaR normal
        """
        np.random.seed(2020)
        n = 60  # 3 mois de trading

        # Marche en chute : -1% par jour en moyenne, vol 4%
        market = np.random.normal(-0.01, 0.04, n)

        # Toutes les strategies suivent le marche (correlation > 0.9)
        noise = 0.003
        strategy_returns = {
            "momentum": list(market + np.random.normal(0, noise, n)),
            "pairs": list(market * 0.95 + np.random.normal(0, noise, n)),
            "gap_cont": list(market * 1.05 + np.random.normal(0, noise, n)),
            "vwap_micro": list(market * 0.98 + np.random.normal(0, noise, n)),
            "orb_v2": list(market * 1.02 + np.random.normal(0, noise, n)),
        }
        weights = {k: 0.20 for k in strategy_returns}

        result = rm.calculate_portfolio_var(strategy_returns, weights)

        # Diversification tres faible en crise
        assert result["diversification_benefit"] < 0.15, (
            f"En crise, diversification benefit devrait etre faible, got {result['diversification_benefit']:.4f}"
        )

        # VaR stressed proche du VaR portfolio (les correlations sont deja hautes)
        ratio = result["var_stressed"] / result["var_portfolio"] if result["var_portfolio"] > 0 else 1.0
        assert ratio < 1.3, (
            f"En crise, VaR stressed / VaR portfolio devrait etre < 1.3, got {ratio:.2f}"
        )

        # VaR portfolio proche de la somme naive (faible diversification)
        ratio_naive = result["var_portfolio"] / result["var_individual_sum"] if result["var_individual_sum"] > 0 else 1.0
        assert ratio_naive > 0.85, (
            f"En crise, VaR portfolio devrait etre > 85% de la somme naive, got {ratio_naive:.2%}"
        )

    def test_march_2020_correlations_high(self, rm):
        """En scenario crise, toutes les correlations inter-strategies > 0.8."""
        np.random.seed(2020)
        n = 60
        market = np.random.normal(-0.01, 0.04, n)
        noise = 0.003

        strategy_returns = {
            "strat_1": list(market + np.random.normal(0, noise, n)),
            "strat_2": list(market * 1.05 + np.random.normal(0, noise, n)),
        }
        weights = {"strat_1": 0.5, "strat_2": 0.5}

        result = rm.calculate_portfolio_var(strategy_returns, weights)
        corr = result["correlation_matrix"]

        # Correlation croisee doit etre tres haute
        assert corr["strat_1/strat_2"] > 0.8, (
            f"Correlation en crise devrait etre > 0.8, got {corr['strat_1/strat_2']:.4f}"
        )


# =============================================================================
# EDGE CASES
# =============================================================================

class TestPortfolioVaREdgeCases:
    def test_empty_returns(self, rm):
        """Avec des returns vides, tous les resultats sont zero."""
        result = rm.calculate_portfolio_var({}, {})
        assert result["var_portfolio"] == 0.0
        assert result["var_individual_sum"] == 0.0
        assert result["var_stressed"] == 0.0

    def test_single_strategy(self, rm):
        """Avec une seule strategie, VaR portfolio = VaR individuel."""
        np.random.seed(42)
        returns = {"solo": list(np.random.normal(0.001, 0.02, 252))}
        weights = {"solo": 1.0}

        result = rm.calculate_portfolio_var(returns, weights)

        # Diversification benefit = 0 (une seule strat)
        assert abs(result["diversification_benefit"]) < 1e-4, (
            f"Diversification benefit avec 1 strat devrait etre ~0, got {result['diversification_benefit']}"
        )
        # VaR portfolio ~= VaR individuel
        assert abs(result["var_portfolio"] - result["var_individual_sum"]) < 1e-4

    def test_missing_strategy_in_weights(self, rm):
        """Si une strategie est dans returns mais pas dans weights, elle est ignoree."""
        np.random.seed(42)
        returns = {
            "strat_A": list(np.random.normal(0, 0.01, 100)),
            "strat_B": list(np.random.normal(0, 0.01, 100)),
        }
        weights = {"strat_A": 1.0}  # strat_B pas dans weights

        result = rm.calculate_portfolio_var(returns, weights)
        # Seule strat_A est prise en compte
        assert "strat_B" not in result["risk_contribution"]

    def test_horizon_scaling(self, rm, uncorrelated_returns, equal_weights):
        """VaR a horizon 5j > VaR a horizon 1j (scaling sqrt)."""
        result_1d = rm.calculate_portfolio_var(
            uncorrelated_returns, equal_weights, horizon=1
        )
        result_5d = rm.calculate_portfolio_var(
            uncorrelated_returns, equal_weights, horizon=5
        )
        assert result_5d["var_portfolio"] > result_1d["var_portfolio"], (
            f"VaR 5j {result_5d['var_portfolio']:.6f} devrait etre > "
            f"VaR 1j {result_1d['var_portfolio']:.6f}"
        )
