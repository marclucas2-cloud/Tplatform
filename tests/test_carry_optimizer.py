"""
Tests unitaires du CarryOptimizer (ROC-004).

Verifie :
  - Pas de carry quand signal actif (LONG/SHORT)
  - Carry quand signal NEUTRAL
  - Taille carry dans le cap (2% du capital)
  - Paire non-carry ignoree
  - Estimation daily carry
  - Stats carry completes
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.carry_optimizer import CarryOptimizer

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def optimizer():
    """CarryOptimizer avec $10K capital et paires par defaut."""
    return CarryOptimizer(capital=10_000)


@pytest.fixture
def small_capital_optimizer():
    """CarryOptimizer avec petit capital ($1K) pour tester le cap."""
    return CarryOptimizer(capital=1_000)


# =============================================================================
# TESTS
# =============================================================================

class TestNoCarryWhenSignalActive:
    """Test : pas de carry quand un signal LONG ou SHORT est actif."""

    def test_no_carry_long_signal(self, optimizer):
        """Signal LONG -> pas de micro-position carry."""
        assert optimizer.should_hold_carry("EUR/JPY", "LONG") is False

    def test_no_carry_short_signal(self, optimizer):
        """Signal SHORT -> pas de micro-position carry."""
        assert optimizer.should_hold_carry("EUR/JPY", "SHORT") is False

    def test_no_carry_buy_signal(self, optimizer):
        """Signal 'BUY' (actif) -> pas de carry."""
        assert optimizer.should_hold_carry("AUD/JPY", "BUY") is False

    def test_no_carry_numeric_signal(self, optimizer):
        """Signal 1 (actif) -> pas de carry."""
        assert optimizer.should_hold_carry("EUR/JPY", 1) is False

    def test_no_carry_negative_signal(self, optimizer):
        """Signal -1 (actif, SHORT) -> pas de carry."""
        assert optimizer.should_hold_carry("EUR/JPY", -1) is False


class TestCarryWhenNeutral:
    """Test : carry actif quand signal NEUTRAL."""

    def test_carry_neutral_string(self, optimizer):
        """Signal 'NEUTRAL' -> carry actif."""
        assert optimizer.should_hold_carry("EUR/JPY", "NEUTRAL") is True

    def test_carry_none_signal(self, optimizer):
        """Signal None -> carry actif."""
        assert optimizer.should_hold_carry("EUR/JPY", None) is True

    def test_carry_zero_signal(self, optimizer):
        """Signal 0 -> carry actif."""
        assert optimizer.should_hold_carry("AUD/JPY", 0) is True

    def test_carry_flat_signal(self, optimizer):
        """Signal 'flat' -> carry actif."""
        assert optimizer.should_hold_carry("EUR/JPY", "flat") is True

    def test_carry_empty_string(self, optimizer):
        """Signal '' -> carry actif."""
        assert optimizer.should_hold_carry("EUR/JPY", "") is True


class TestCarrySizeWithinCap:
    """Test : la taille carry ne depasse pas 2% du capital."""

    def test_carry_size_normal_capital(self, optimizer):
        """$10K capital -> carry = min(25000*5%, 10000*2%) = min(1250, 200) = $200."""
        size = optimizer.get_carry_size("EUR/JPY")
        # 10000 * 0.02 = 200, 25000 * 0.05 = 1250 -> min = 200
        assert size == 200.0

    def test_carry_size_capped_by_capital(self, small_capital_optimizer):
        """$1K capital -> cap = 1000*2% = $20."""
        size = small_capital_optimizer.get_carry_size("EUR/JPY")
        assert size == 20.0

    def test_carry_size_large_capital(self):
        """$100K capital -> carry = min(1250, 2000) = $1250."""
        opt = CarryOptimizer(capital=100_000)
        size = opt.get_carry_size("EUR/JPY")
        # 100000 * 0.02 = 2000, 25000 * 0.05 = 1250 -> min = 1250
        assert size == 1250.0


class TestNonCarryPairIgnored:
    """Test : paire non-carry retourne toujours False/0."""

    def test_non_carry_pair_no_hold(self, optimizer):
        """EUR/USD n'est pas une paire carry."""
        assert optimizer.should_hold_carry("EUR/USD", "NEUTRAL") is False

    def test_non_carry_pair_zero_size(self, optimizer):
        """Paire non-carry -> taille = 0."""
        assert optimizer.get_carry_size("EUR/USD") == 0.0

    def test_non_carry_pair_gbp_usd(self, optimizer):
        """GBP/USD n'est pas dans les carry pairs."""
        assert optimizer.should_hold_carry("GBP/USD", None) is False


class TestDailyEstimate:
    """Test : estimation du revenu carry journalier."""

    def test_daily_estimate_default_pairs(self, optimizer):
        """Avec $10K et 2 paires, estimation > 0."""
        daily = optimizer.get_daily_carry_estimate()
        assert daily > 0

        # EUR/JPY : (200/25000) * 0.80 = 0.0064
        # AUD/JPY : (200/25000) * 0.60 = 0.0048
        # Total : 0.0112
        expected = (200 / 25_000) * 0.80 + (200 / 25_000) * 0.60
        assert abs(daily - round(expected, 4)) < 0.001

    def test_daily_estimate_no_pairs(self):
        """Sans paires carry, estimation = 0."""
        opt = CarryOptimizer(capital=10_000, carry_pairs={})
        assert opt.get_daily_carry_estimate() == 0.0


class TestCarryStats:
    """Test : statistiques carry completes."""

    def test_carry_stats_structure(self, optimizer):
        """Les stats contiennent toutes les cles attendues."""
        stats = optimizer.get_carry_stats()

        assert "pairs" in stats
        assert "n_carry_pairs" in stats
        assert "daily_estimate" in stats
        assert "monthly_estimate" in stats
        assert "annual_estimate" in stats
        assert "total_earned" in stats
        assert "total_days_held" in stats
        assert "capital" in stats

    def test_carry_stats_pairs_detail(self, optimizer):
        """Les stats incluent le detail par paire."""
        stats = optimizer.get_carry_stats()

        assert "EUR/JPY" in stats["pairs"]
        assert "AUD/JPY" in stats["pairs"]

        eurjpy = stats["pairs"]["EUR/JPY"]
        assert eurjpy["carry_size"] == 200.0
        assert eurjpy["swap_per_lot_day"] == 0.80
        assert eurjpy["daily_swap"] > 0

    def test_carry_stats_after_record(self, optimizer):
        """Apres record_carry_day, les stats refletent les gains."""
        optimizer.record_carry_day("EUR/JPY")
        optimizer.record_carry_day("EUR/JPY")

        stats = optimizer.get_carry_stats()

        assert stats["total_earned"] > 0
        assert stats["total_days_held"] == 2
        assert stats["pairs"]["EUR/JPY"]["is_active"] is True
        assert stats["pairs"]["EUR/JPY"]["days_held"] == 2

    def test_carry_stats_monthly_annual(self, optimizer):
        """Les estimations mensuelles et annuelles sont coherentes."""
        stats = optimizer.get_carry_stats()

        assert stats["monthly_estimate"] == round(stats["daily_estimate"] * 30, 2)
        assert stats["annual_estimate"] == round(stats["daily_estimate"] * 365, 2)
