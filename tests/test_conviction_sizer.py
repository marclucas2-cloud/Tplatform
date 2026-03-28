"""
Tests unitaires — ConvictionSizer (ROC-002).

Couvre :
  - Conviction STRONG -> 1.5x sizing
  - Conviction NORMAL -> 1.0x sizing
  - Conviction WEAK -> 0.7x sizing
  - Score < 0.3 -> SKIP
  - Cap au max_kelly_fraction
  - Frontieres exactes des niveaux
  - Calcul du score de conviction
  - Protection prix zero
  - Score negatif traite comme SKIP
  - Logging de conviction
"""

import sys
from pathlib import Path

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.conviction_sizer import ConvictionSizer


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sizer():
    """ConvictionSizer avec parametres par defaut (1/8 Kelly base, 1/4 max)."""
    return ConvictionSizer(
        base_kelly_fraction=0.125,
        max_kelly_fraction=0.25,
    )


@pytest.fixture
def capital():
    """Capital de test : $10,000."""
    return 10_000.0


@pytest.fixture
def price():
    """Prix de test : $50/action."""
    return 50.0


# =============================================================================
# TESTS
# =============================================================================

def test_strong_conviction_1_5x(sizer, capital, price):
    """Conviction >= 0.8 -> multiplier 1.5x sur le sizing de base."""
    result = sizer.calculate_size(conviction_score=0.9, capital=capital, price=price)

    assert result["level"] == "STRONG"
    assert result["skip"] is False
    # 1/8 Kelly * 1.5 = 3/16 Kelly = 0.1875
    assert result["kelly_used"] == pytest.approx(0.1875, abs=1e-4)
    # Notional = 10000 * 0.1875 = $1875
    assert result["adjusted_notional"] == pytest.approx(1875.0, abs=1.0)
    # Shares = floor(1875 / 50) = 37
    assert result["shares"] == 37


def test_normal_conviction_1x(sizer, capital, price):
    """Conviction 0.5-0.8 -> multiplier 1.0x (pas de modification)."""
    result = sizer.calculate_size(conviction_score=0.65, capital=capital, price=price)

    assert result["level"] == "NORMAL"
    assert result["skip"] is False
    # 1/8 Kelly * 1.0 = 1/8 Kelly = 0.125
    assert result["kelly_used"] == pytest.approx(0.125, abs=1e-4)
    # Notional = 10000 * 0.125 = $1250
    assert result["adjusted_notional"] == pytest.approx(1250.0, abs=1.0)
    # Shares = floor(1250 / 50) = 25
    assert result["shares"] == 25


def test_weak_conviction_0_7x(sizer, capital, price):
    """Conviction 0.3-0.5 -> multiplier 0.7x (reduction du sizing)."""
    result = sizer.calculate_size(conviction_score=0.4, capital=capital, price=price)

    assert result["level"] == "WEAK"
    assert result["skip"] is False
    # 1/8 Kelly * 0.7 = 0.0875
    assert result["kelly_used"] == pytest.approx(0.0875, abs=1e-4)
    # Notional = 10000 * 0.0875 = $875
    assert result["adjusted_notional"] == pytest.approx(875.0, abs=1.0)
    # Shares = floor(875 / 50) = 17
    assert result["shares"] == 17


def test_skip_below_0_3(sizer, capital, price):
    """Conviction < 0.3 -> SKIP, pas de trade."""
    result = sizer.calculate_size(conviction_score=0.2, capital=capital, price=price)

    assert result["level"] == "SKIP"
    assert result["skip"] is True
    assert result["shares"] == 0
    assert result["kelly_used"] == 0.0
    assert result["adjusted_notional"] == 0.0


def test_max_kelly_cap(capital, price):
    """Le sizing ajuste ne depasse jamais max_kelly_fraction."""
    # Base 0.20, max 0.25 -> STRONG (1.5x) donnerait 0.30, cappe a 0.25
    sizer = ConvictionSizer(
        base_kelly_fraction=0.20,
        max_kelly_fraction=0.25,
    )
    result = sizer.calculate_size(conviction_score=0.9, capital=capital, price=price)

    assert result["level"] == "STRONG"
    # 0.20 * 1.5 = 0.30, mais cap a 0.25
    assert result["kelly_used"] == pytest.approx(0.25, abs=1e-4)
    assert result["adjusted_notional"] == pytest.approx(2500.0, abs=1.0)


def test_conviction_level_boundaries(sizer):
    """Verification des frontieres exactes entre niveaux."""
    # Exactement 0.8 -> STRONG
    assert sizer.get_conviction_level(0.8) == "STRONG"
    # Juste en dessous -> NORMAL
    assert sizer.get_conviction_level(0.79) == "NORMAL"

    # Exactement 0.5 -> NORMAL
    assert sizer.get_conviction_level(0.5) == "NORMAL"
    # Juste en dessous -> WEAK
    assert sizer.get_conviction_level(0.49) == "WEAK"

    # Exactement 0.3 -> WEAK
    assert sizer.get_conviction_level(0.3) == "WEAK"
    # Juste en dessous -> SKIP
    assert sizer.get_conviction_level(0.29) == "SKIP"

    # Extremes
    assert sizer.get_conviction_level(1.0) == "STRONG"
    assert sizer.get_conviction_level(0.0) == "SKIP"


def test_compute_conviction_score():
    """Calcul du score de conviction avec les poids par defaut."""
    # Toutes les composantes a 1.0 -> score = 1.0
    score_max = ConvictionSizer.compute_conviction_score(
        adx_strength=1.0,
        volume_confirmation=1.0,
        multi_timeframe_alignment=1.0,
        regime_alignment=1.0,
        historical_edge=1.0,
    )
    assert score_max == pytest.approx(1.0, abs=1e-4)

    # Toutes les composantes a 0.0 -> score = 0.0
    score_min = ConvictionSizer.compute_conviction_score(
        adx_strength=0.0,
        volume_confirmation=0.0,
        multi_timeframe_alignment=0.0,
        regime_alignment=0.0,
        historical_edge=0.0,
    )
    assert score_min == pytest.approx(0.0, abs=1e-4)

    # Test partiel : ADX fort, reste moyen
    score_partial = ConvictionSizer.compute_conviction_score(
        adx_strength=1.0,         # 0.25 * 1.0 = 0.250
        volume_confirmation=0.5,  # 0.20 * 0.5 = 0.100
        multi_timeframe_alignment=0.5,  # 0.20 * 0.5 = 0.100
        regime_alignment=0.5,     # 0.20 * 0.5 = 0.100
        historical_edge=0.5,      # 0.15 * 0.5 = 0.075
    )
    # Total = 0.250 + 0.100 + 0.100 + 0.100 + 0.075 = 0.625
    assert score_partial == pytest.approx(0.625, abs=1e-3)


def test_zero_price_safe(sizer, capital):
    """Prix zero ou negatif -> SKIP sans erreur."""
    result_zero = sizer.calculate_size(conviction_score=0.9, capital=capital, price=0.0)
    assert result_zero["skip"] is True
    assert result_zero["shares"] == 0

    result_neg = sizer.calculate_size(conviction_score=0.9, capital=capital, price=-10.0)
    assert result_neg["skip"] is True
    assert result_neg["shares"] == 0


def test_negative_score_treated_as_skip(sizer, capital, price):
    """Score negatif -> SKIP (traite comme invalide)."""
    result = sizer.calculate_size(conviction_score=-0.5, capital=capital, price=price)

    assert result["level"] == "SKIP"
    assert result["skip"] is True
    assert result["shares"] == 0

    # None aussi
    result_none = sizer.calculate_size(conviction_score=None, capital=capital, price=price)
    assert result_none["skip"] is True


def test_log_conviction(sizer):
    """Le log de conviction enregistre correctement les trades et les stats."""
    # Logger quelques trades
    sizer.log_conviction("T001", 0.9, "STRONG", 1250.0, 1875.0)
    sizer.log_conviction("T002", 0.6, "NORMAL", 1250.0, 1250.0)
    sizer.log_conviction("T003", 0.4, "WEAK", 1250.0, 875.0)

    # Mettre a jour les resultats
    sizer.update_trade_result("T001", pnl=150.0)
    sizer.update_trade_result("T002", pnl=-50.0)
    sizer.update_trade_result("T003", pnl=30.0)

    stats = sizer.get_conviction_stats()

    assert stats["total_trades"] == 3
    assert stats["STRONG"]["count"] == 1
    assert stats["STRONG"]["avg_pnl"] == 150.0
    assert stats["STRONG"]["win_rate"] == 1.0

    assert stats["NORMAL"]["count"] == 1
    assert stats["NORMAL"]["avg_pnl"] == -50.0
    assert stats["NORMAL"]["win_rate"] == 0.0

    assert stats["WEAK"]["count"] == 1
    assert stats["WEAK"]["avg_pnl"] == 30.0
    assert stats["WEAK"]["win_rate"] == 1.0

    assert stats["SKIP"]["count"] == 0
