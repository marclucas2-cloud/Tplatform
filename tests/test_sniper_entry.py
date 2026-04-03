"""
Tests for SniperEntry — ROC-007.

Covers:
  - Sniper order BTC (offset en pourcentage)
  - Sniper order FX (offset en pips)
  - Non-MR strategy ignored
  - Fallback market order after timeout
  - BUY offset below price
  - SELL offset above price
  - Unknown symbol → market order
  - Sniper stats tracking
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.sniper_entry import SniperEntry

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sniper():
    """SniperEntry avec config par defaut."""
    return SniperEntry()


# =============================================================================
# TESTS
# =============================================================================

def test_sniper_order_btc(sniper):
    """BTC : offset en pourcentage applique correctement."""
    order = sniper.create_sniper_order(
        symbol="BTCUSDT",
        side="BUY",
        current_price=68000.0,
        quantity=0.01,
        strategy_name="btc_mean_reversion",
    )

    assert order["order_type"] == "LIMIT"
    # Offset = 68000 * 0.0005 = 34.0
    expected_price = 68000.0 - 34.0
    assert abs(order["price"] - expected_price) < 0.01
    assert order["fallback"] == "MARKET"
    assert order["timeout"] == 300
    assert order["quantity"] == 0.01
    assert order["offset_applied"] == pytest.approx(34.0, rel=1e-3)


def test_sniper_order_fx_pips(sniper):
    """FX EUR/GBP : offset en pips (2 pips = 0.0002)."""
    order = sniper.create_sniper_order(
        symbol="EURGBP",
        side="BUY",
        current_price=0.8550,
        quantity=25000,
        strategy_name="eurgbp_mr",
    )

    assert order["order_type"] == "LIMIT"
    # 2 pips * 0.0001 = 0.0002
    expected_price = 0.8550 - 0.0002
    assert abs(order["price"] - expected_price) < 1e-6
    assert order["fallback"] == "MARKET"
    assert order["offset_applied"] == pytest.approx(0.0002, rel=1e-3)


def test_non_mr_strategy_ignored(sniper):
    """Une strategie non-MR recoit un ordre market standard."""
    order = sniper.create_sniper_order(
        symbol="BTCUSDT",
        side="BUY",
        current_price=68000.0,
        quantity=0.01,
        strategy_name="momentum_trend",
    )

    assert order["order_type"] == "MARKET"
    assert order["price"] == 68000.0
    assert order["fallback"] is None
    assert order["timeout"] == 0
    assert order["offset_applied"] == 0.0


def test_fallback_market_order(sniper):
    """L'ordre sniper a un fallback market configure."""
    order = sniper.create_sniper_order(
        symbol="EURUSD",
        side="BUY",
        current_price=1.0850,
        quantity=25000,
        strategy_name="mean_reversion",
    )

    assert order["order_type"] == "LIMIT"
    assert order["fallback"] == "MARKET"
    assert order["timeout"] > 0


def test_buy_offset_below_price(sniper):
    """BUY : le prix limit est SOUS le prix actuel."""
    order = sniper.create_sniper_order(
        symbol="EURJPY",
        side="BUY",
        current_price=162.50,
        quantity=25000,
        strategy_name="mean_reversion",
    )

    assert order["order_type"] == "LIMIT"
    # 3 pips * 0.01 (JPY) = 0.03
    assert order["price"] < 162.50
    assert order["price"] == pytest.approx(162.50 - 0.03, abs=1e-6)


def test_sell_offset_above_price(sniper):
    """SELL : le prix limit est AU-DESSUS du prix actuel."""
    order = sniper.create_sniper_order(
        symbol="EURGBP",
        side="SELL",
        current_price=0.8550,
        quantity=25000,
        strategy_name="eurgbp_mr",
    )

    assert order["order_type"] == "LIMIT"
    assert order["price"] > 0.8550
    # 2 pips * 0.0001 = 0.0002
    assert order["price"] == pytest.approx(0.8550 + 0.0002, abs=1e-6)


def test_unknown_symbol_market_order(sniper):
    """Symbole non configure → fallback market order."""
    order = sniper.create_sniper_order(
        symbol="XYZABC",
        side="BUY",
        current_price=100.0,
        quantity=10,
        strategy_name="mean_reversion",
    )

    assert order["order_type"] == "MARKET"
    assert order["price"] == 100.0
    assert order["fallback"] is None
    assert order["offset_applied"] == 0.0


def test_sniper_stats(sniper):
    """Les statistiques sont correctement calculees."""
    # Generer quelques ordres
    sniper.create_sniper_order(
        symbol="BTCUSDT", side="BUY", current_price=68000.0,
        quantity=0.01, strategy_name="btc_mr",
    )
    sniper.create_sniper_order(
        symbol="EURGBP", side="BUY", current_price=0.8550,
        quantity=25000, strategy_name="eurgbp_mr",
    )

    # Enregistrer les fills
    sniper.record_fill(was_limit_fill=True, improvement_bps=3.5)
    sniper.record_fill(was_limit_fill=False, improvement_bps=0.0)

    stats = sniper.get_sniper_stats()

    assert stats["total_attempts"] == 2
    assert stats["limit_fills"] == 1
    assert stats["market_fallbacks"] == 1
    assert stats["fill_rate"] == pytest.approx(0.5, rel=1e-3)
    assert stats["avg_improvement_bps"] == pytest.approx(3.5, rel=1e-3)
    assert stats["timeouts"] == 1
