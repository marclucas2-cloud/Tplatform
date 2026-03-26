"""
Tests unitaires — Risk Manager V2 + Dynamic Allocator.

Couvre :
  - Limites de position, strategie, secteur
  - Exposition long/short/gross
  - Reserve cash
  - VaR / CVaR parametrique
  - Circuit-breaker daily + hourly
  - Allocator tier caps, momentum boost, correlation penalty
  - Regime multipliers
"""

import sys
import pytest
import numpy as np
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.risk_manager import RiskManager
from core.allocator import DynamicAllocator


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def rm():
    """Instance de RiskManager avec limits.yaml du projet."""
    return RiskManager()


@pytest.fixture
def allocator():
    """Instance de DynamicAllocator avec allocation.yaml du projet."""
    return DynamicAllocator()


@pytest.fixture
def base_portfolio():
    """Portfolio de base pour les tests."""
    return {
        "equity": 100_000.0,
        "cash": 30_000.0,
        "positions": [
            {"symbol": "AAPL", "notional": 8000, "side": "LONG", "strategy": "momentum_25etf"},
            {"symbol": "MSFT", "notional": 5000, "side": "LONG", "strategy": "momentum_25etf"},
            {"symbol": "SPY", "notional": 10000, "side": "LONG", "strategy": "gap_continuation"},
            {"symbol": "COIN", "notional": 4000, "side": "SHORT", "strategy": "crypto_proxy_v2"},
        ],
    }


@pytest.fixture
def sample_strategies():
    """Strategies pour tester l'allocator."""
    return {
        "opex_gamma": {"sharpe": 10.41, "volatility": 0.08, "correlation_avg": 0.1, "edge_type": "event"},
        "gap_continuation": {"sharpe": 5.22, "volatility": 0.12, "correlation_avg": 0.2, "edge_type": "momentum"},
        "gold_fear": {"sharpe": 5.01, "volatility": 0.15, "correlation_avg": 0.3, "edge_type": "short"},
        "vwap_micro": {"sharpe": 3.08, "volatility": 0.10, "correlation_avg": 0.25, "edge_type": "mean_reversion"},
        "meanrev_v2": {"sharpe": 1.44, "volatility": 0.20, "correlation_avg": 0.4, "edge_type": "mean_reversion"},
        "momentum_25etf": {"sharpe": 0.8, "volatility": 0.18, "correlation_avg": 0.5, "edge_type": "momentum"},
        "crypto_proxy_v2": {"sharpe": 3.49, "volatility": 0.25, "correlation_avg": 0.7, "edge_type": "momentum"},
    }


# =============================================================================
# TEST 1 : Position limit bloque les ordres trop gros
# =============================================================================

class TestPositionLimit:
    def test_position_limit_blocks_oversized_order(self, rm, base_portfolio):
        """Un ordre qui porterait une position au-dela de 10% equity est rejete."""
        order = {
            "symbol": "AAPL",
            "direction": "LONG",
            "notional": 5000,  # AAPL deja a 8K, total 13K = 13% > 10%
            "strategy": "momentum_25etf",
        }
        passed, msg = rm.validate_order(order, base_portfolio)
        assert not passed, f"L'ordre devrait etre rejete: {msg}"
        assert "Position limit" in msg

    def test_position_limit_allows_small_order(self, rm, base_portfolio):
        """Un ordre qui reste sous 10% est accepte."""
        order = {
            "symbol": "AAPL",
            "direction": "LONG",
            "notional": 1500,  # AAPL deja 8K, total 9.5K = 9.5% < 10%
            "strategy": "momentum_25etf",
        }
        passed, msg = rm.validate_order(order, base_portfolio)
        assert passed, f"L'ordre devrait passer: {msg}"


# =============================================================================
# TEST 2 : Sector limit bloque la concentration tech
# =============================================================================

class TestSectorLimit:
    def test_sector_limit_blocks_tech_concentration(self, rm):
        """Ajouter trop de tech depasse le cap sectoriel de 25%."""
        portfolio = {
            "equity": 100_000.0,
            "cash": 50_000.0,
            "positions": [
                {"symbol": "AAPL", "notional": 9000, "side": "LONG", "strategy": "s1"},
                {"symbol": "MSFT", "notional": 9000, "side": "LONG", "strategy": "s2"},
            ],
        }
        order = {
            "symbol": "NVDA",
            "direction": "LONG",
            "notional": 9000,  # AAPL 9K + MSFT 9K + NVDA 9K = 27K = 27% > 25%
            "strategy": "opex_gamma",
        }
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"L'ordre devrait etre rejete: {msg}"
        assert "Sector limit" in msg

    def test_sector_limit_allows_diversified(self, rm, base_portfolio):
        """Un ordre dans un secteur peu expose passe."""
        order = {
            "symbol": "JPM",
            "direction": "LONG",
            "notional": 5000,  # Finance: 0 + 5K = 5% < 25%
            "strategy": "gap_continuation",
        }
        passed, msg = rm.validate_order(order, base_portfolio)
        assert passed, f"L'ordre devrait passer: {msg}"


# =============================================================================
# TEST 3 : Gross exposure limit
# =============================================================================

class TestGrossExposure:
    def test_gross_exposure_limit(self, rm):
        """L'exposition brute totale ne doit pas depasser 90%.

        Note: avec max_long=60% + max_short=30% = 90% = max_gross,
        les checks directionnels se declenchent avant le gross.
        On teste ici directement la methode _check_gross_exposure.
        """
        portfolio = {
            "equity": 100_000.0,
            "cash": 20_000.0,
            "positions": [
                {"symbol": "SPY", "notional": 50000, "side": "LONG", "strategy": "s1"},
                {"symbol": "COIN", "notional": 35000, "side": "SHORT", "strategy": "s2"},
            ],
        }
        # Gross = 85K. Order 8K -> 93% > 90%
        order = {
            "symbol": "TLT",
            "direction": "LONG",
            "notional": 8000,
            "strategy": "s3",
        }
        passed, msg = rm._check_gross_exposure(order, portfolio)
        assert not passed, f"Gross exposure devrait etre rejete: {msg}"
        assert "Gross exposure" in msg


# =============================================================================
# TEST 4 : Cash reserve enforced
# =============================================================================

class TestCashReserve:
    def test_cash_reserve_enforced(self, rm):
        """On ne peut pas descendre sous le min_cash de 10%."""
        portfolio = {
            "equity": 100_000.0,
            "cash": 12_000.0,  # 12%
            "positions": [
                {"symbol": "SPY", "notional": 40000, "side": "LONG", "strategy": "s1"},
            ],
        }
        # Utiliser 5K de cash -> restant 7K = 7% < 10%
        order = {
            "symbol": "QQQ",
            "direction": "LONG",
            "notional": 5000,
            "strategy": "s2",
        }
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed, f"L'ordre devrait etre rejete: {msg}"
        assert "Cash reserve" in msg

    def test_cash_reserve_allows_when_ample(self, rm):
        """Avec assez de cash, l'ordre passe."""
        portfolio = {
            "equity": 100_000.0,
            "cash": 50_000.0,
            "positions": [],
        }
        order = {
            "symbol": "SPY",
            "direction": "LONG",
            "notional": 8000,
            "strategy": "s1",
        }
        passed, msg = rm.validate_order(order, portfolio)
        assert passed, f"L'ordre devrait passer: {msg}"


# =============================================================================
# TEST 5 : VaR calculation
# =============================================================================

class TestVaR:
    def test_var_calculation_reasonable(self, rm):
        """La VaR 99% sur des rendements normaux doit etre raisonnable."""
        np.random.seed(42)
        returns = list(np.random.normal(0.0005, 0.01, 252))  # ~1% vol daily
        var = rm.calculate_var(returns, confidence=0.99)
        # VaR 99% devrait etre entre 0.5% et 5% pour cette vol
        assert 0.005 < var < 0.05, f"VaR {var:.4f} hors bornes attendues"

    def test_var_increases_with_horizon(self, rm):
        """VaR avec horizon > 1 doit etre superieure a horizon 1."""
        np.random.seed(42)
        returns = list(np.random.normal(0.0, 0.02, 252))
        var_1d = rm.calculate_var(returns, confidence=0.99, horizon=1)
        var_5d = rm.calculate_var(returns, confidence=0.99, horizon=5)
        assert var_5d > var_1d, "VaR 5j devrait etre > VaR 1j"

    def test_var_empty_returns(self, rm):
        """VaR avec une liste vide retourne 0."""
        assert rm.calculate_var([]) == 0.0
        assert rm.calculate_var([0.01]) == 0.0


# =============================================================================
# TEST 6 : CVaR >= VaR
# =============================================================================

class TestCVaR:
    def test_cvar_greater_than_var(self, rm):
        """CVaR (Expected Shortfall) doit etre >= VaR."""
        np.random.seed(42)
        returns = list(np.random.normal(0.0, 0.02, 500))
        var = rm.calculate_var(returns, confidence=0.99)
        cvar = rm.calculate_cvar(returns, confidence=0.99)
        assert cvar >= var, f"CVaR {cvar:.4f} < VaR {var:.4f}"

    def test_cvar_empty_returns(self, rm):
        """CVaR avec une liste vide retourne 0."""
        assert rm.calculate_cvar([]) == 0.0


# =============================================================================
# TEST 7 : Circuit-breaker daily
# =============================================================================

class TestCircuitBreakerDaily:
    def test_circuit_breaker_daily_triggers(self, rm):
        """Un drawdown daily > 5% declenche le circuit-breaker."""
        triggered, msg = rm.check_circuit_breaker(daily_pnl_pct=-0.06)
        assert triggered, "Circuit-breaker daily aurait du se declencher"
        assert "DAILY" in msg

    def test_circuit_breaker_daily_normal(self, rm):
        """Un drawdown daily < 5% ne declenche pas."""
        triggered, msg = rm.check_circuit_breaker(daily_pnl_pct=-0.03)
        assert not triggered, "Circuit-breaker daily ne devrait pas se declencher"


# =============================================================================
# TEST 8 : Circuit-breaker hourly (NOUVEAU)
# =============================================================================

class TestCircuitBreakerHourly:
    def test_circuit_breaker_hourly_triggers(self, rm):
        """Un drawdown horaire > 3% declenche le circuit-breaker."""
        triggered, msg = rm.check_circuit_breaker(
            daily_pnl_pct=-0.02, hourly_pnl_pct=-0.035
        )
        assert triggered, "Circuit-breaker hourly aurait du se declencher"
        assert "HOURLY" in msg

    def test_circuit_breaker_hourly_normal(self, rm):
        """Un drawdown horaire < 3% ne declenche pas."""
        triggered, msg = rm.check_circuit_breaker(
            daily_pnl_pct=-0.01, hourly_pnl_pct=-0.02
        )
        assert not triggered, "Circuit-breaker hourly ne devrait pas se declencher"

    def test_circuit_breaker_daily_takes_priority(self, rm):
        """Si daily et hourly depassent, le message est DAILY."""
        triggered, msg = rm.check_circuit_breaker(
            daily_pnl_pct=-0.06, hourly_pnl_pct=-0.04
        )
        assert triggered
        assert "DAILY" in msg


# =============================================================================
# TEST 9 : Allocator tier caps
# =============================================================================

class TestAllocatorTierCaps:
    def test_allocator_tier_caps(self, allocator, sample_strategies):
        """Les poids doivent respecter les caps de tier."""
        weights = allocator.calculate_weights(sample_strategies)
        tiers = allocator.config["tiers"]

        for tier_name, tier_config in tiers.items():
            max_alloc = tier_config["max_alloc"]
            for strat in tier_config["strategies"]:
                if strat in weights:
                    assert weights[strat] <= max_alloc + 1e-9, (
                        f"{strat} (tier {tier_name}): {weights[strat]:.3f} > cap {max_alloc}"
                    )

    def test_allocator_sum_below_one(self, allocator, sample_strategies):
        """La somme des poids ne depasse pas 1.0."""
        weights = allocator.calculate_weights(sample_strategies)
        total = sum(weights.values())
        assert total <= 1.0 + 1e-9, f"Somme des poids {total:.3f} > 1.0"


# =============================================================================
# TEST 10 : Allocator momentum boost
# =============================================================================

class TestAllocatorMomentumBoost:
    def test_allocator_momentum_boost(self, allocator):
        """Une strategie Sharpe > 2.0 recoit plus de poids qu'une Sharpe < 1.0."""
        strategies = {
            "high_sharpe": {"sharpe": 5.0, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
            "low_sharpe": {"sharpe": 0.5, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
        }
        weights = allocator.calculate_weights(strategies)
        # High sharpe gets 1.3x boost, low sharpe stays at 1.0x (no cut since 0 < 0.5 < 1.0)
        # Actually 0.5 < 1.0 and 0.5 >= 0 so no penalty
        # Same base vol -> same base weight -> after momentum: 1.3 vs 1.0
        assert weights["high_sharpe"] > weights["low_sharpe"], (
            f"high_sharpe {weights['high_sharpe']:.4f} devrait etre > low_sharpe {weights['low_sharpe']:.4f}"
        )

    def test_allocator_negative_sharpe_penalized(self, allocator):
        """Une strategie Sharpe < 0 est penalisee (x0.5)."""
        strategies = {
            "positive": {"sharpe": 1.5, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
            "negative": {"sharpe": -0.5, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
        }
        weights = allocator.calculate_weights(strategies)
        # positive gets 1.1x, negative gets 0.5x
        assert weights["positive"] > weights["negative"], (
            f"positive {weights['positive']:.4f} devrait etre > negative {weights['negative']:.4f}"
        )


# =============================================================================
# TEST 11 : Allocator correlation penalty
# =============================================================================

class TestAllocatorCorrelationPenalty:
    def test_allocator_correlation_penalty(self, allocator):
        """Une strategie tres correlee recoit moins de poids."""
        strategies = {
            "low_corr": {"sharpe": 2.0, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
            "high_corr": {"sharpe": 2.0, "volatility": 0.10, "correlation_avg": 0.8, "edge_type": "momentum"},
        }
        weights = allocator.calculate_weights(strategies)
        # high_corr (0.8 > 0.6) gets multiplied by (1-0.8) = 0.2
        assert weights["low_corr"] > weights["high_corr"], (
            f"low_corr {weights['low_corr']:.4f} devrait etre > high_corr {weights['high_corr']:.4f}"
        )


# =============================================================================
# TEST 12 : Regime multipliers
# =============================================================================

class TestRegimeMultipliers:
    def test_regime_multipliers_bear_boosts_short(self, allocator):
        """En regime BEAR_HIGH_VOL, les strategies short recoivent un boost x2.0."""
        mults = allocator.get_regime_multipliers("BEAR_HIGH_VOL")
        assert mults["short"] == 2.0, f"Short mult = {mults['short']}, attendu 2.0"
        assert mults["momentum"] < 1.0, "Momentum devrait etre reduit en bear"

    def test_regime_multipliers_bull_reduces_short(self, allocator):
        """En regime BULL_NORMAL, les strategies short sont reduites."""
        mults = allocator.get_regime_multipliers("BULL_NORMAL")
        assert mults["short"] == 0.5
        assert mults["momentum"] == 1.0

    def test_regime_unknown_defaults_to_bull(self, allocator):
        """Un regime inconnu retourne BULL_NORMAL par defaut."""
        mults = allocator.get_regime_multipliers("UNKNOWN_REGIME")
        bull_mults = allocator.get_regime_multipliers("BULL_NORMAL")
        assert mults == bull_mults

    def test_apply_regime_changes_weights(self, allocator):
        """apply_regime ajuste les poids selon le regime."""
        strategies = {
            "momentum_strat": {"sharpe": 2.0, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "momentum"},
            "short_strat": {"sharpe": 2.0, "volatility": 0.10, "correlation_avg": 0.1, "edge_type": "short"},
        }
        base_weights = allocator.calculate_weights(strategies)

        bear_weights = allocator.apply_regime(base_weights, strategies, "BEAR_HIGH_VOL")
        bull_weights = allocator.apply_regime(base_weights, strategies, "BULL_NORMAL")

        # En bear, short_strat devrait avoir un poids relatif plus eleve
        bear_short_ratio = bear_weights["short_strat"] / (bear_weights["momentum_strat"] + 1e-9)
        bull_short_ratio = bull_weights["short_strat"] / (bull_weights["momentum_strat"] + 1e-9)
        assert bear_short_ratio > bull_short_ratio, (
            f"Short/momentum ratio bear {bear_short_ratio:.2f} devrait etre > bull {bull_short_ratio:.2f}"
        )


# =============================================================================
# TEST 13 : Sector exposure
# =============================================================================

class TestSectorExposure:
    def test_get_sector_exposure(self, rm):
        """Verifie le calcul d'exposition sectorielle."""
        positions = [
            {"symbol": "AAPL", "notional": 10000, "side": "LONG"},
            {"symbol": "MSFT", "notional": 5000, "side": "LONG"},
            {"symbol": "COIN", "notional": 3000, "side": "SHORT"},
            {"symbol": "JPM", "notional": 7000, "side": "LONG"},
        ]
        expo = rm.get_sector_exposure(positions, 100_000.0)
        assert abs(expo.get("tech", 0) - 0.15) < 1e-9, f"Tech expo: {expo.get('tech')}"
        assert abs(expo.get("crypto", 0) - (-0.03)) < 1e-9, f"Crypto expo: {expo.get('crypto')}"
        assert abs(expo.get("finance", 0) - 0.07) < 1e-9, f"Finance expo: {expo.get('finance')}"


# =============================================================================
# TEST 14 : Exposure limits (long/short)
# =============================================================================

class TestExposureLimits:
    def test_long_exposure_limit_blocks(self, rm):
        """L'exposition long ne doit pas depasser 60%."""
        portfolio = {
            "equity": 100_000.0,
            "cash": 50_000.0,
            "positions": [
                {"symbol": "SPY", "notional": 55000, "side": "LONG", "strategy": "s1"},
            ],
        }
        order = {
            "symbol": "QQQ",
            "direction": "LONG",
            "notional": 8000,  # 55K + 8K = 63% > 60%
            "strategy": "s2",
        }
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed
        assert "Long exposure" in msg

    def test_short_exposure_limit_blocks(self, rm):
        """L'exposition short ne doit pas depasser 30%."""
        portfolio = {
            "equity": 100_000.0,
            "cash": 50_000.0,
            "positions": [
                {"symbol": "COIN", "notional": 28000, "side": "SHORT", "strategy": "s1"},
            ],
        }
        order = {
            "symbol": "MARA",
            "direction": "SHORT",
            "notional": 5000,  # 28K + 5K = 33% > 30%
            "strategy": "s2",
        }
        passed, msg = rm.validate_order(order, portfolio)
        assert not passed
        assert "Short exposure" in msg


# =============================================================================
# TEST 15 : Allocator empty strategies
# =============================================================================

class TestAllocatorEdgeCases:
    def test_empty_strategies(self, allocator):
        """Aucune strategie -> aucun poids."""
        weights = allocator.calculate_weights({})
        assert weights == {}

    def test_get_tier_for_strategy(self, allocator):
        """Verifie le lookup tier."""
        assert allocator.get_tier_for_strategy("opex_gamma") == "S"
        assert allocator.get_tier_for_strategy("gold_fear") == "B"
        assert allocator.get_tier_for_strategy("unknown_strat") == "unknown"
