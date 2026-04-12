"""
Tests unitaires de la reconciliation automatique des positions.

Couvre :
  - Positions orphelines (chez le broker mais pas dans le state)
  - Positions manquantes (dans le state mais pas chez le broker)
  - Reconciliation OK (pas de divergence)
  - Direction mismatch
  - State file manquant
  - Erreur API broker
  - Run complet (Alpaca + IBKR)
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.reconciliation import PositionReconciler

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def env_paper_trading():
    """Assure que PAPER_TRADING=true pour tous les tests."""
    with patch.dict(os.environ, {
        "PAPER_TRADING": "true",
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }):
        yield


@pytest.fixture
def base_state():
    """State de base avec des positions daily et intraday."""
    return {
        "capital": 100_000.0,
        "positions": {
            "momentum_25etf": {"symbols": ["SPY", "QQQ"]},
            "vrp_rotation": {"symbols": ["TLT"]},
        },
        "intraday_positions": {
            "AAPL": {
                "strategy": "orb_5min",
                "direction": "LONG",
                "entry_price": 180.0,
            },
            "NVDA": {
                "strategy": "vwap_micro",
                "direction": "SHORT",
                "entry_price": 500.0,
            },
        },
        "allocations": {},
        "last_monthly": "2026-03-25T14:35:02.712510+00:00",
        "last_run_date": "2026-03-25",
    }


@pytest.fixture
def state_file(base_state, tmp_path):
    """Cree un fichier state temporaire."""
    state_path = tmp_path / "paper_portfolio_state.json"
    with open(state_path, "w") as f:
        json.dump(base_state, f)
    return state_path


@pytest.fixture
def reconciler(state_file):
    """Cree un reconciler avec le state temporaire."""
    return PositionReconciler(state_path=state_file)


# =============================================================================
# TEST 1 : Reconciliation OK — pas de divergence
# =============================================================================

class TestReconciliationOK:
    def test_no_divergences_when_positions_match(self, reconciler):
        """Aucune divergence si les positions correspondent."""
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            {"symbol": "NVDA", "qty": -5.0, "side": "short",
             "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        assert divergences == [], f"Divergences inattendues: {divergences}"


# =============================================================================
# TEST 2 : Position orpheline (chez broker, pas dans state)
# =============================================================================

class TestOrphanPosition:
    def test_orphan_position_detected(self, reconciler):
        """Detecte une position dans Alpaca mais pas dans le state."""
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            {"symbol": "NVDA", "qty": -5.0, "side": "short",
             "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
            # ORPHELIN — pas dans le state
            {"symbol": "TSLA", "qty": 3.0, "side": "long",
             "avg_entry": 200.0, "market_val": 600.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        assert len(divergences) == 1
        assert divergences[0]["type"] == "orphan"
        assert divergences[0]["symbol"] == "TSLA"
        assert divergences[0]["severity"] == "critical"  # val > $100

    def test_small_orphan_is_warning(self, reconciler):
        """Une position orpheline de faible valeur est un warning."""
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            {"symbol": "NVDA", "qty": -5.0, "side": "short",
             "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
            # Petit orphelin < $100
            {"symbol": "PENNY", "qty": 1.0, "side": "long",
             "avg_entry": 5.0, "market_val": 5.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        assert len(divergences) == 1
        assert divergences[0]["severity"] == "warning"


# =============================================================================
# TEST 3 : Position manquante (dans state, pas chez broker)
# =============================================================================

class TestMissingPosition:
    def test_missing_position_detected(self, reconciler):
        """Detecte une position dans le state mais absente chez Alpaca."""
        # Manque TLT et NVDA par rapport au state
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        missing = [d for d in divergences if d["type"] == "missing"]
        missing_symbols = {d["symbol"] for d in missing}
        assert "TLT" in missing_symbols
        assert "NVDA" in missing_symbols
        assert all(d["severity"] == "critical" for d in missing)


# =============================================================================
# TEST 4 : Direction mismatch
# =============================================================================

class TestDirectionMismatch:
    def test_direction_mismatch_detected(self, reconciler):
        """Detecte quand le broker a une direction differente du state."""
        # NVDA est SHORT dans le state, mais LONG chez le broker
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            # Direction inversee par rapport au state
            {"symbol": "NVDA", "qty": 5.0, "side": "long",
             "avg_entry": 500.0, "market_val": 2500.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        direction_mismatches = [d for d in divergences if d["type"] == "direction_mismatch"]
        assert len(direction_mismatches) == 1
        assert direction_mismatches[0]["symbol"] == "NVDA"
        assert direction_mismatches[0]["severity"] == "critical"


# =============================================================================
# TEST 5 : State file manquant
# =============================================================================

class TestStateFileMissing:
    def test_missing_state_file(self, tmp_path):
        """Reconciliation avec un fichier state inexistant."""
        missing_path = tmp_path / "nonexistent.json"
        reconciler = PositionReconciler(state_path=missing_path)

        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        # Toutes les positions Alpaca sont orphelines
        assert len(divergences) == 1
        assert divergences[0]["type"] == "orphan"
        assert divergences[0]["symbol"] == "SPY"


# =============================================================================
# TEST 6 : Erreur API Alpaca
# =============================================================================

class TestAlpacaAPIError:
    def test_api_error_returns_error_divergence(self, reconciler):
        """Si l'API Alpaca est inaccessible, retourner une divergence d'erreur."""
        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            MockClient.from_env.side_effect = Exception("Connection refused")

            divergences = reconciler.reconcile_alpaca()

        assert len(divergences) == 1
        assert divergences[0]["type"] == "error"
        assert divergences[0]["severity"] == "critical"


# =============================================================================
# TEST 7 : Run complet
# =============================================================================

class TestRunComplete:
    def test_run_returns_ok_when_no_divergences(self, reconciler):
        """run() retourne status=OK quand pas de divergences."""
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            {"symbol": "NVDA", "qty": -5.0, "side": "short",
             "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient, \
             patch("core.broker.ibkr_adapter.IBKRBroker") as MockIBKR:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance
            mock_ibkr = MagicMock()
            mock_ibkr.get_positions.return_value = mock_alpaca_positions
            MockIBKR.return_value = mock_ibkr

            result = reconciler.run()

        assert result["status"] == "OK"
        assert result["summary"]["total_divergences"] == 0

    def test_run_returns_divergence_when_issues(self, reconciler):
        """run() retourne status=DIVERGENCE quand il y a des problemes."""
        # Positions Alpaca avec un orphelin
        mock_alpaca_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
            {"symbol": "QQQ", "qty": 5.0, "side": "long",
             "avg_entry": 350.0, "market_val": 1750.0, "unrealized_pl": 0.0},
            {"symbol": "TLT", "qty": 20.0, "side": "long",
             "avg_entry": 100.0, "market_val": 2000.0, "unrealized_pl": 0.0},
            {"symbol": "AAPL", "qty": 15.0, "side": "long",
             "avg_entry": 180.0, "market_val": 2700.0, "unrealized_pl": 0.0},
            {"symbol": "NVDA", "qty": -5.0, "side": "short",
             "avg_entry": 500.0, "market_val": -2500.0, "unrealized_pl": 0.0},
            {"symbol": "MYSTERY", "qty": 100.0, "side": "long",
             "avg_entry": 50.0, "market_val": 5000.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_alpaca_positions
            MockClient.from_env.return_value = mock_instance

            result = reconciler.run()

        assert result["status"] == "DIVERGENCE"
        assert result["summary"]["total_divergences"] >= 1
        assert result["summary"]["critical_count"] >= 1

    def test_run_has_required_fields(self, reconciler):
        """run() retourne un dict avec tous les champs requis."""
        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = []
            MockClient.from_env.return_value = mock_instance

            result = reconciler.run()

        assert "timestamp" in result
        assert "divergences" in result
        assert "status" in result
        assert "summary" in result
        assert "alpaca_checked" in result["summary"]
        assert "ibkr_checked" in result["summary"]
        assert "total_divergences" in result["summary"]
        assert "critical_count" in result["summary"]
        assert "warning_count" in result["summary"]


# =============================================================================
# TEST 8 : State vide (pas de positions)
# =============================================================================

class TestEmptyState:
    def test_empty_state_with_broker_positions(self, tmp_path):
        """Un state vide avec des positions broker = tout orphelin."""
        state_path = tmp_path / "empty_state.json"
        with open(state_path, "w") as f:
            json.dump({"capital": 100000, "positions": {},
                       "intraday_positions": {}}, f)

        reconciler = PositionReconciler(state_path=state_path)

        mock_positions = [
            {"symbol": "SPY", "qty": 10.0, "side": "long",
             "avg_entry": 450.0, "market_val": 4500.0, "unrealized_pl": 0.0},
        ]

        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = mock_positions
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        assert len(divergences) == 1
        assert divergences[0]["type"] == "orphan"

    def test_empty_broker_with_state_positions(self, reconciler):
        """Pas de positions chez le broker mais des positions dans le state."""
        with patch("scripts.reconciliation.AlpacaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get_positions.return_value = []
            MockClient.from_env.return_value = mock_instance

            divergences = reconciler.reconcile_alpaca()

        # Toutes les positions du state sont "missing"
        missing = [d for d in divergences if d["type"] == "missing"]
        assert len(missing) == 5  # SPY, QQQ, TLT, AAPL, NVDA
