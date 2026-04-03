"""
Tests for TimezoneCapitalAllocator — ROC-008.

Covers:
  - Overlap max capital
  - Night restricted
  - Reserve always held
  - Blocked margin reduces available
  - Asia crypto only
  - EU morning markets
  - Current slot
  - Market active
  - Utilization report
  - Custom schedule
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.timezone_allocator import TimezoneCapitalAllocator

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def allocator():
    """Allocateur avec $10K, reserve 20%."""
    return TimezoneCapitalAllocator(
        total_capital=10_000.0,
        reserve_pct=0.20,
    )


# =============================================================================
# TESTS
# =============================================================================

def test_overlap_max_capital(allocator):
    """Overlap (14h-18h) : max 50% du deployable = $4000."""
    result = allocator.get_available_capital(hour_cet=15, blocked_margin=0.0)

    # Deployable = 10000 * 0.80 = 8000
    # Max slot = 8000 * 0.50 = 4000
    assert result["slot_name"] == "OVERLAP"
    assert result["max_for_slot"] == 4000.0
    assert result["available"] == 4000.0
    assert "us" in result["markets_active"]
    assert "eu" in result["markets_active"]
    assert "fx" in result["markets_active"]
    assert "futures" in result["markets_active"]
    assert "crypto" in result["markets_active"]


def test_night_restricted(allocator):
    """Night (22h-0h) : max 20% du deployable = $1600, FX+crypto seulement."""
    result = allocator.get_available_capital(hour_cet=23, blocked_margin=0.0)

    assert result["slot_name"] == "NIGHT"
    assert result["max_for_slot"] == 1600.0
    assert result["available"] == 1600.0
    assert "fx" in result["markets_active"]
    assert "crypto" in result["markets_active"]
    assert "us" not in result["markets_active"]
    assert "eu" not in result["markets_active"]
    assert "futures" not in result["markets_active"]


def test_reserve_always_held(allocator):
    """La reserve est toujours maintenue, meme au slot le plus genereux."""
    result = allocator.get_available_capital(hour_cet=15, blocked_margin=0.0)

    # Reserve = 10000 * 0.20 = 2000
    assert result["reserve"] == 2000.0
    # Max available ne depasse jamais deployable
    assert result["available"] <= allocator.deployable_capital


def test_blocked_margin_reduces_available(allocator):
    """La marge bloquee reduit le capital disponible."""
    result = allocator.get_available_capital(hour_cet=15, blocked_margin=3000.0)

    # Max slot = 4000, blocked = 3000 → available = 1000
    assert result["available"] == 1000.0
    assert result["max_for_slot"] == 4000.0


def test_asia_crypto_only(allocator):
    """Asia (0h-8h) : crypto seulement, max 25%."""
    result = allocator.get_available_capital(hour_cet=3, blocked_margin=0.0)

    assert result["slot_name"] == "ASIA_CRYPTO"
    assert result["markets_active"] == ["crypto"]
    # Max = 8000 * 0.25 = 2000
    assert result["max_for_slot"] == 2000.0


def test_eu_morning_markets(allocator):
    """EU Morning (8h-14h) : EU + FX + crypto, max 35%."""
    result = allocator.get_available_capital(hour_cet=10, blocked_margin=0.0)

    assert result["slot_name"] == "EU_MORNING"
    assert "eu" in result["markets_active"]
    assert "fx" in result["markets_active"]
    assert "crypto" in result["markets_active"]
    assert "us" not in result["markets_active"]
    # Max = 8000 * 0.35 = 2800
    assert result["max_for_slot"] == 2800.0


def test_current_slot(allocator):
    """get_current_slot retourne le bon creneau."""
    slot = allocator.get_current_slot(hour_cet=16)
    assert slot["name"] == "OVERLAP"
    assert slot["start"] == 14
    assert slot["end"] == 18
    assert slot["max_pct"] == 50.0

    slot = allocator.get_current_slot(hour_cet=5)
    assert slot["name"] == "ASIA_CRYPTO"


def test_market_active(allocator):
    """is_market_active verifie correctement l'activite par marche."""
    # Overlap : tous actifs
    assert allocator.is_market_active("us", hour_cet=15) is True
    assert allocator.is_market_active("eu", hour_cet=15) is True
    assert allocator.is_market_active("futures", hour_cet=15) is True

    # Night : seulement FX et crypto
    assert allocator.is_market_active("fx", hour_cet=23) is True
    assert allocator.is_market_active("crypto", hour_cet=23) is True
    assert allocator.is_market_active("us", hour_cet=23) is False
    assert allocator.is_market_active("futures", hour_cet=23) is False

    # Asia : seulement crypto
    assert allocator.is_market_active("crypto", hour_cet=3) is True
    assert allocator.is_market_active("fx", hour_cet=3) is False


def test_utilization_report(allocator):
    """Le rapport d'utilisation agrege correctement les donnees."""
    # Generer quelques appels
    allocator.get_available_capital(hour_cet=3, blocked_margin=500.0)
    allocator.get_available_capital(hour_cet=3, blocked_margin=1000.0)
    allocator.get_available_capital(hour_cet=15, blocked_margin=2000.0)

    report = allocator.get_utilization_report()

    assert report["total_entries"] == 3
    assert "ASIA_CRYPTO" in report["by_slot"]
    assert "OVERLAP" in report["by_slot"]
    assert report["by_slot"]["ASIA_CRYPTO"]["count"] == 2
    assert report["by_slot"]["OVERLAP"]["count"] == 1
    assert report["by_slot"]["ASIA_CRYPTO"]["avg_blocked"] == 750.0


def test_custom_schedule():
    """Un schedule custom est correctement utilise."""
    custom_schedule = {
        "MORNING": {
            "start": 0,
            "end": 12,
            "max_pct": 60.0,
            "markets": ["crypto", "fx"],
        },
        "AFTERNOON": {
            "start": 12,
            "end": 24,
            "max_pct": 80.0,
            "markets": ["us", "eu", "fx", "crypto"],
        },
    }

    allocator = TimezoneCapitalAllocator(
        total_capital=20_000.0,
        reserve_pct=0.10,
        schedule=custom_schedule,
    )

    # Deployable = 20000 * 0.90 = 18000
    result = allocator.get_available_capital(hour_cet=5, blocked_margin=0.0)
    assert result["slot_name"] == "MORNING"
    # 18000 * 0.60 = 10800
    assert result["max_for_slot"] == 10800.0
    assert result["markets_active"] == ["crypto", "fx"]

    result = allocator.get_available_capital(hour_cet=14, blocked_margin=0.0)
    assert result["slot_name"] == "AFTERNOON"
    # 18000 * 0.80 = 14400
    assert result["max_for_slot"] == 14400.0
    assert "us" in result["markets_active"]
