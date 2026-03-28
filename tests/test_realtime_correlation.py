"""
Tests for RealTimeCorrelationMonitor — ROC-006.

Covers:
  - Independent positions (no correlation)
  - High correlation cluster detection
  - Sizing override on concentrated cluster
  - Effective positions (uncorrelated)
  - Effective positions (correlated)
  - Cluster report
  - Max cluster alert
  - Empty positions
  - Single position
  - Reduction factor applied
"""

import sys
import pytest
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.realtime_correlation import RealTimeCorrelationMonitor


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def monitor():
    """Monitor avec seuils par defaut."""
    return RealTimeCorrelationMonitor(
        cluster_threshold=0.6,
        max_cluster_pct=40.0,
        reduction_factor=0.7,
    )


@pytest.fixture
def independent_positions():
    """3 positions avec rendements non correles."""
    positions = [
        {"symbol": "AAPL", "notional": 2000},
        {"symbol": "GOLD", "notional": 2000},
        {"symbol": "EURUSD", "notional": 2000},
    ]
    # Rendements aleatoires non correles (seed fixe pour reproductibilite)
    np.random.seed(42)
    returns_data = {
        "AAPL": list(np.random.randn(30) * 0.02),
        "GOLD": list(np.random.randn(30) * 0.01),
        "EURUSD": list(np.random.randn(30) * 0.005),
    }
    return positions, returns_data


@pytest.fixture
def correlated_positions():
    """4 positions dont 3 tres correlees (cluster tech)."""
    positions = [
        {"symbol": "AAPL", "notional": 2000},
        {"symbol": "MSFT", "notional": 2000},
        {"symbol": "GOOG", "notional": 2000},
        {"symbol": "GOLD", "notional": 1000},
    ]
    # AAPL, MSFT, GOOG = tres correles (meme signal de base + bruit)
    np.random.seed(42)
    base_signal = np.random.randn(30) * 0.02
    returns_data = {
        "AAPL": list(base_signal + np.random.randn(30) * 0.002),
        "MSFT": list(base_signal + np.random.randn(30) * 0.002),
        "GOOG": list(base_signal + np.random.randn(30) * 0.003),
        "GOLD": list(np.random.randn(30) * 0.01),  # independant
    }
    return positions, returns_data


# =============================================================================
# TESTS
# =============================================================================

def test_no_correlation_independent(monitor, independent_positions):
    """Positions independantes : pas de cluster detecte."""
    positions, returns_data = independent_positions
    result = monitor.update(positions, returns_data)

    # Pas de cluster avec rho > 0.6 pour des positions random
    assert result["clusters"] == [] or all(
        len(c) <= 1 for c in result["clusters"]
    )
    assert result["alerts"] == []
    assert result["sizing_overrides"] == {}


def test_high_correlation_cluster(monitor, correlated_positions):
    """Positions correlees : un cluster tech detecte."""
    positions, returns_data = correlated_positions
    result = monitor.update(positions, returns_data)

    # Au moins un cluster contenant AAPL, MSFT, GOOG
    assert len(result["clusters"]) >= 1
    tech_cluster = None
    for cluster in result["clusters"]:
        if "AAPL" in cluster and "MSFT" in cluster:
            tech_cluster = cluster
            break
    assert tech_cluster is not None
    assert "GOLD" not in tech_cluster


def test_sizing_override_on_cluster(monitor, correlated_positions):
    """Le sizing override est applique aux symboles d'un cluster concentre."""
    positions, returns_data = correlated_positions
    # Le cluster tech (AAPL+MSFT+GOOG) = 6000/7000 = 85.7% > 40% → alerte
    result = monitor.update(positions, returns_data)

    # Les 3 tech doivent avoir un override
    assert len(result["sizing_overrides"]) >= 2
    for sym in result["sizing_overrides"]:
        assert result["sizing_overrides"][sym] == 0.7


def test_effective_positions_uncorrelated(monitor, independent_positions):
    """Positions non correlees : N_eff proche de N."""
    positions, returns_data = independent_positions
    monitor.update(positions, returns_data)

    n_eff = monitor.get_effective_positions(
        positions, monitor._corr_matrix
    )
    # Avec 3 positions non correlees, N_eff devrait etre proche de 3
    assert n_eff >= 2.0  # au moins 2 sur 3


def test_effective_positions_correlated(monitor, correlated_positions):
    """Positions correlees : N_eff significativement inferieur a N."""
    positions, returns_data = correlated_positions
    monitor.update(positions, returns_data)

    n_eff = monitor.get_effective_positions(
        positions, monitor._corr_matrix
    )
    # 4 positions mais 3 tres correlees → N_eff < 3
    assert n_eff < 3.0
    assert n_eff > 0.5  # pas degenere


def test_cluster_report(monitor, correlated_positions):
    """Le rapport de cluster contient les bonnes cles."""
    positions, returns_data = correlated_positions
    monitor.update(positions, returns_data)

    report = monitor.get_cluster_report()
    assert "clusters" in report
    assert "n_clusters" in report
    assert "symbols" in report
    assert "alerts" in report
    assert "sizing_overrides" in report
    assert "last_update" in report
    assert report["last_update"] is not None
    assert report["n_clusters"] >= 1


def test_max_cluster_alert(monitor, correlated_positions):
    """Une alerte est generee quand un cluster depasse le seuil."""
    positions, returns_data = correlated_positions
    result = monitor.update(positions, returns_data)

    # 3 tech sur 4 positions = 85.7% > 40% → alerte
    assert len(result["alerts"]) >= 1
    alert = result["alerts"][0]
    assert alert["type"] == "CLUSTER_CONCENTRATION"
    assert alert["pct"] > monitor.max_cluster_pct


def test_empty_positions(monitor):
    """Pas de positions : pas d'erreur, pas de cluster."""
    result = monitor.update([], {})

    assert result["matrix"] is None
    assert result["clusters"] == []
    assert result["alerts"] == []
    assert result["sizing_overrides"] == {}


def test_single_position(monitor):
    """Une seule position : pas de correlation possible."""
    positions = [{"symbol": "AAPL", "notional": 5000}]
    returns_data = {"AAPL": [0.01, -0.02, 0.015, -0.01]}

    result = monitor.update(positions, returns_data)

    assert result["matrix"] is None
    assert result["clusters"] == []
    assert result["alerts"] == []


def test_reduction_factor_applied():
    """Le reduction_factor custom est bien applique."""
    monitor = RealTimeCorrelationMonitor(
        cluster_threshold=0.5,
        max_cluster_pct=30.0,
        reduction_factor=0.5,
    )

    # 2 positions tres correlees (rendements identiques)
    positions = [
        {"symbol": "A", "notional": 5000},
        {"symbol": "B", "notional": 5000},
    ]
    base = list(np.random.randn(20) * 0.02)
    returns_data = {
        "A": base,
        "B": [x + 0.001 for x in base],  # quasi identique
    }

    result = monitor.update(positions, returns_data)

    # Cluster {A, B} = 100% du capital > 30% → override a 0.5
    assert len(result["clusters"]) >= 1
    for sym in result["sizing_overrides"]:
        assert result["sizing_overrides"][sym] == 0.5

    # Verification via get_sizing_override
    assert monitor.get_sizing_override("A") == 0.5
    assert monitor.get_sizing_override("B") == 0.5
    assert monitor.get_sizing_override("C") == 1.0  # pas dans un cluster
