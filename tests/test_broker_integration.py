"""
Tests d'integration broker (mock) — Alpaca + IBKR.

Simule les appels API avec unittest.mock.patch pour tester :
  - Bracket orders Alpaca
  - Fills partiels
  - Rate limiting (429)
  - Connexion / deconnexion IBKR
  - Ordres rejetes (insufficient buying power)

Aucun appel reseau reel — tout est mocke.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_alpaca_client():
    """Cree un AlpacaClient avec les clients internes mockes."""
    from core.alpaca_client.client import AlpacaClient
    client = AlpacaClient(
        api_key="test-key",
        secret_key="test-secret",
        paper=True,
    )
    return client


# =============================================================================
# TEST 1 : Bracket order Alpaca (mock)
# =============================================================================

class TestAlpacaBracketOrderMock:
    def test_alpaca_bracket_order_mock(self, mock_alpaca_client):
        """Simule la soumission d'un bracket order Alpaca (market + SL + TP)."""
        mock_order = MagicMock()
        mock_order.id = "order-123-bracket"
        mock_order.symbol = "AAPL"
        mock_order.side.value = "buy"
        mock_order.status.value = "accepted"
        mock_order.qty = "10"
        mock_order.filled_avg_price = None
        mock_order.filled_qty = None

        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = mock_order

        mock_alpaca_client._trading = mock_trading
        mock_alpaca_client._paper = True

        result = mock_alpaca_client.create_position(
            symbol="AAPL",
            direction="BUY",
            qty=10,
            stop_loss=145.00,
            take_profit=165.00,
            _authorized_by="test_bracket",
        )

        assert result["orderId"] == "order-123-bracket"
        assert result["symbol"] == "AAPL"
        assert result["bracket"] is True
        assert result["stop_loss"] == 145.00
        assert result["take_profit"] == 165.00
        assert result["authorized_by"] == "test_bracket"
        mock_trading.submit_order.assert_called_once()


# =============================================================================
# TEST 2 : Fill partiel Alpaca (mock)
# =============================================================================

class TestAlpacaFillPartialMock:
    def test_alpaca_fill_partial_mock(self, mock_alpaca_client):
        """Simule un fill partiel : 7 actions remplies sur 10 demandees."""
        mock_order = MagicMock()
        mock_order.id = "order-456-partial"
        mock_order.symbol = "MSFT"
        mock_order.side.value = "buy"
        mock_order.status.value = "partially_filled"
        mock_order.qty = "10"
        mock_order.filled_avg_price = "410.25"
        mock_order.filled_qty = "7"

        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = mock_order

        mock_alpaca_client._trading = mock_trading
        mock_alpaca_client._paper = True

        result = mock_alpaca_client.create_position(
            symbol="MSFT",
            direction="BUY",
            qty=10,
            _authorized_by="test_partial",
        )

        assert result["orderId"] == "order-456-partial"
        assert result["status"] == "partially_filled"
        assert result["filled_qty"] == 7.0
        assert result["filled_price"] == 410.25


# =============================================================================
# TEST 3 : Rate limit 429 Alpaca (mock)
# =============================================================================

class TestAlpacaRateLimitMock:
    def test_alpaca_rate_limit_mock(self, mock_alpaca_client):
        """Simule une erreur 429 (Too Many Requests) de l'API Alpaca."""
        mock_trading = MagicMock()
        mock_trading.submit_order.side_effect = Exception(
            "429 Too Many Requests: Rate limit exceeded"
        )

        mock_alpaca_client._trading = mock_trading
        mock_alpaca_client._paper = True

        with pytest.raises(Exception, match="429"):
            mock_alpaca_client.create_position(
                symbol="SPY",
                direction="BUY",
                qty=5,
                _authorized_by="test_rate_limit",
            )


# =============================================================================
# TEST 4 : Connexion IBKR (mock)
# =============================================================================

class TestIBKRConnectionMock:
    @patch.dict("os.environ", {"IBKR_PAPER": "true"})
    @patch("core.broker.ibkr_adapter.IB", create=True)
    def test_ibkr_connection_mock(self, mock_ib_class):
        """Simule une connexion reussie a IBKR."""
        mock_ib_instance = MagicMock()
        mock_ib_instance.isConnected.return_value = False
        mock_ib_instance.connect.return_value = None
        mock_ib_instance.managedAccounts.return_value = ["DU12345"]

        mock_summary_tag = MagicMock()
        mock_summary_tag.tag = "NetLiquidation"
        mock_summary_tag.value = "100000.00"

        cash_tag = MagicMock()
        cash_tag.tag = "TotalCashValue"
        cash_tag.value = "50000.00"

        bp_tag = MagicMock()
        bp_tag.tag = "BuyingPower"
        bp_tag.value = "200000.00"

        mock_ib_instance.accountSummary.return_value = [
            mock_summary_tag, cash_tag, bp_tag
        ]
        mock_ib_class.return_value = mock_ib_instance

        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib_instance
        broker._paper = True
        broker._host = "127.0.0.1"
        broker._port = 7497
        broker._client_id = 1
        broker._connected = False
        broker._permanently_down = False
        broker._reconnect_attempts = 0

        broker._ensure_connected()
        assert broker._connected is True
        mock_ib_instance.connect.assert_called_once()


# =============================================================================
# TEST 5 : Deconnexion + Reconnexion IBKR (mock)
# =============================================================================

class TestIBKRDisconnectReconnectMock:
    def test_ibkr_disconnect_reconnect_mock(self):
        """Simule une deconnexion puis reconnexion IBKR."""
        mock_ib = MagicMock()

        # Premier appel : deconnecte, deuxieme : connecte
        mock_ib.isConnected.side_effect = [False, True]
        mock_ib.connect.return_value = None

        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib
        broker._paper = True
        broker._host = "127.0.0.1"
        broker._port = 7497
        broker._client_id = 1
        broker._connected = True  # Etait connecte
        broker._permanently_down = False
        broker._reconnect_attempts = 0

        # isConnected() retourne False → reconnexion
        broker._ensure_connected()
        assert broker._connected is True
        mock_ib.connect.assert_called_once()

    def test_ibkr_permanently_down_after_max_retries(self):
        """Apres max retries, le broker est marque permanently down."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False
        mock_ib.connect.side_effect = Exception("Connection refused")

        from core.broker.ibkr_adapter import IBKRBroker
        from core.broker.base import BrokerError
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib
        broker._paper = True
        broker._host = "127.0.0.1"
        broker._port = 7497
        broker._client_id = 1
        broker._connected = False
        broker._permanently_down = False
        broker._reconnect_attempts = 0

        with pytest.raises(BrokerError, match="permanently"):
            broker._ensure_connected()

        assert broker._permanently_down is True


# =============================================================================
# TEST 6 : Ordre rejete (insufficient buying power)
# =============================================================================

class TestOrderRejectedInsufficientBuyingPower:
    def test_order_rejected_insufficient_buying_power(self, mock_alpaca_client):
        """Simule un rejet d'ordre pour buying power insuffisant."""
        mock_trading = MagicMock()
        mock_trading.submit_order.side_effect = Exception(
            "403 Forbidden: insufficient buying power"
        )

        mock_alpaca_client._trading = mock_trading
        mock_alpaca_client._paper = True

        with pytest.raises(Exception, match="insufficient buying power"):
            mock_alpaca_client.create_position(
                symbol="TSLA",
                direction="BUY",
                qty=1000,
                _authorized_by="test_buying_power",
            )


# =============================================================================
# TEST 7 : IBKR health_check (mock)
# =============================================================================

class TestIBKRHealthCheck:
    def test_health_check_connected(self):
        """health_check retourne True si connecte."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True

        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib
        broker._connected = True
        broker._permanently_down = False
        broker._reconnect_attempts = 0

        assert broker.health_check() is True

    def test_health_check_disconnected(self):
        """health_check retourne False si deconnecte."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False

        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib
        broker._connected = False
        broker._permanently_down = False
        broker._reconnect_attempts = 0

        assert broker.health_check() is False

    def test_health_check_permanently_down(self):
        """health_check retourne False si permanently down."""
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = False

        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker.__new__(IBKRBroker)
        broker._ib = mock_ib
        broker._connected = False
        broker._permanently_down = True
        broker._reconnect_attempts = 0

        assert broker.health_check() is False


# =============================================================================
# TEST 8 : Guard _authorized_by refuse les ordres non autorises
# =============================================================================

class TestAuthorizedByGuard:
    def test_alpaca_rejects_without_authorized_by(self, mock_alpaca_client):
        """Un ordre sans _authorized_by est refuse."""
        from core.alpaca_client.client import AlpacaAPIError
        with pytest.raises(AlpacaAPIError, match="sans _authorized_by"):
            mock_alpaca_client.create_position(
                symbol="AAPL",
                direction="BUY",
                qty=10,
                # _authorized_by omis volontairement
            )
