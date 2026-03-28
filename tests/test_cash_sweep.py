"""
Tests unitaires du CashSweepManager (ROC-001).

Verifie :
  - Sweep quand excedent de cash
  - Pas de sweep sous le buffer minimum
  - Pas de sweep pour petit montant
  - Pre-order redemption depuis Earn
  - Pre-order sans redemption si cash suffisant
  - Total available (spot + earn)
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.crypto.cash_sweep import CashSweepManager


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_broker():
    """Mock broker Binance avec methodes earn."""
    broker = MagicMock()
    # Par defaut : $1000 en spot, $500 en earn USDT
    broker.get_account_info.return_value = {
        "spot_usdt": 1000.0,
        "cash": 1000.0,
        "equity": 1500.0,
    }
    broker.get_earn_positions.return_value = [
        {"asset": "USDT", "amount": 500.0, "apy": 0.05, "product_id": "USDT001"},
    ]
    broker.subscribe_earn.return_value = {"status": "ok"}
    broker.redeem_earn.return_value = {"status": "ok"}
    return broker


@pytest.fixture
def sweeper(mock_broker):
    """CashSweepManager avec broker mock, buffer $500, min sweep $100."""
    return CashSweepManager(
        broker=mock_broker,
        min_cash_buffer=500.0,
        min_sweep_amount=100.0,
    )


# =============================================================================
# TESTS
# =============================================================================

class TestSweepWhenExcessCash:
    """Test sweep quand le cash excede le buffer."""

    def test_sweep_when_excess_cash(self, sweeper, mock_broker):
        """Avec $1000 spot et buffer $500, on sweep $500 en Earn."""
        result = sweeper.sweep()

        assert result["swept"] is True
        assert result["amount"] == 500.0
        assert result["spot_before"] == 1000.0
        assert result["spot_after"] == 500.0
        mock_broker.subscribe_earn.assert_called_once_with("USDT001", 500.0)

    def test_sweep_updates_stats(self, sweeper):
        """Le sweep met a jour les statistiques internes."""
        sweeper.sweep()
        stats = sweeper.get_sweep_stats()

        assert stats["total_swept"] == 500.0
        assert stats["sweep_count"] == 1
        assert stats["last_sweep_at"] is not None


class TestNoSweepBelowBuffer:
    """Test pas de sweep quand le cash est sous le buffer."""

    def test_no_sweep_below_buffer(self, sweeper, mock_broker):
        """Avec $400 spot et buffer $500, pas de sweep."""
        mock_broker.get_account_info.return_value = {"spot_usdt": 400.0}

        result = sweeper.sweep()

        assert result["swept"] is False
        assert result["reason"] == "insufficient_excess"
        mock_broker.subscribe_earn.assert_not_called()


class TestNoSweepSmallAmount:
    """Test pas de sweep quand l'excedent est trop petit."""

    def test_no_sweep_small_amount(self, sweeper, mock_broker):
        """Avec $550 spot et buffer $500, excedent $50 < min_sweep $100."""
        mock_broker.get_account_info.return_value = {"spot_usdt": 550.0}

        result = sweeper.sweep()

        assert result["swept"] is False
        assert result["reason"] == "insufficient_excess"
        assert result["excess"] == 50.0
        mock_broker.subscribe_earn.assert_not_called()


class TestPreOrderRedeem:
    """Test redemption pre-ordre quand le cash spot est insuffisant."""

    @patch("core.crypto.cash_sweep.time.sleep")
    def test_pre_order_redeem(self, mock_sleep, sweeper, mock_broker):
        """Avec $200 spot et $500 en earn, on redeem $600 pour couvrir $800."""
        mock_broker.get_account_info.return_value = {"spot_usdt": 200.0}

        result = sweeper.on_signal_pre_order(required_cash=800.0)

        assert result is True
        # On doit redeem 800 - 200 = $600, mais earn n'a que $500
        # Donc on redeem $500 (tout le earn disponible)
        mock_broker.redeem_earn.assert_called_once_with("USDT001", 500.0)
        mock_sleep.assert_called_once_with(3)

        stats = sweeper.get_sweep_stats()
        assert stats["total_redeemed"] == 500.0
        assert stats["redeem_count"] == 1


class TestPreOrderEnoughCash:
    """Test pre-ordre quand le cash est deja suffisant."""

    def test_pre_order_enough_cash(self, sweeper, mock_broker):
        """Avec $1000 spot et besoin de $800, pas de redeem."""
        result = sweeper.on_signal_pre_order(required_cash=800.0)

        assert result is True
        mock_broker.redeem_earn.assert_not_called()


class TestTotalAvailable:
    """Test du calcul du total disponible (spot + earn)."""

    def test_total_available(self, sweeper):
        """$1000 spot + $500 earn = $1500 total."""
        total = sweeper.get_total_available()

        assert total == 1500.0

    def test_total_available_no_earn(self, sweeper, mock_broker):
        """Si pas de position earn, total = spot seulement."""
        mock_broker.get_earn_positions.return_value = []

        total = sweeper.get_total_available()

        assert total == 1000.0

    def test_total_available_broker_error(self, sweeper, mock_broker):
        """Si le broker echoue, retourne 0."""
        mock_broker.get_account_info.side_effect = Exception("API error")

        total = sweeper.get_total_available()

        assert total == 0.0
