"""
Integration test: signal → fill → SL attached → close → SL cancelled.

Tests the full execution pipeline WITHOUT hitting a real exchange.
Mocks BinanceBroker at the HTTP level to verify the complete chain.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from core.broker.binance_broker import BinanceBroker, BrokerError


@pytest.fixture
def mock_broker():
    """BinanceBroker with mocked HTTP requests."""
    with patch.object(BinanceBroker, '_request') as mock_req:
        broker = BinanceBroker.__new__(BinanceBroker)
        broker._spot_base = "https://api.binance.com"
        broker._testnet = True
        broker._api_key = "test"
        broker._api_secret = "test"
        broker._rate_limiter = MagicMock()
        broker._fill_prices = {}
        broker._mock_req = mock_req
        yield broker


class TestSignalToFillToClose:
    """Full pipeline: BUY → SL attached → verify SL → close → SL cancelled."""

    def test_spot_buy_attaches_sl(self, mock_broker):
        """create_position(BUY) should place market order + SL order."""
        # Mock: market order returns fill
        mock_broker._mock_req.side_effect = [
            # 1st call: market BUY
            {
                "orderId": 1001,
                "status": "FILLED",
                "executedQty": "0.00050",
                "fills": [{"price": "85000.00", "qty": "0.00050"}],
            },
            # 2nd call: SL order
            {
                "orderId": 1002,
                "status": "NEW",
                "type": "STOP_LOSS_LIMIT",
            },
        ]

        result = mock_broker._create_spot_position(
            symbol="BTCUSDC",
            direction="BUY",
            qty=0.00050,
            notional=None,
            stop_loss=80750.0,
            authorized_by="test_integration",
        )

        assert result["status"] == "FILLED"
        assert result["filled_qty"] == 0.00050
        assert result["sl_order_id"] == "1002"
        assert result["stop_loss"] == 80750.0
        # 2 API calls: market order + SL order
        assert mock_broker._mock_req.call_count == 2

    def test_spot_buy_sl_fail_triggers_emergency_close(self, mock_broker):
        """If SL order fails, emergency close the position."""
        mock_broker._mock_req.side_effect = [
            # 1st: market BUY succeeds
            {
                "orderId": 2001,
                "status": "FILLED",
                "executedQty": "0.00050",
                "fills": [{"price": "85000.00", "qty": "0.00050"}],
            },
            # 2nd: SL order FAILS
            BrokerError("Binance API 400: {\"code\":-1013,\"msg\":\"Stop price invalid\"}"),
            # 3rd: emergency close market SELL
            {
                "orderId": 2003,
                "status": "FILLED",
                "executedQty": "0.00050",
            },
        ]

        result = mock_broker._create_spot_position(
            symbol="BTCUSDC",
            direction="BUY",
            qty=0.00050,
            notional=None,
            stop_loss=80750.0,
            authorized_by="test_emergency",
        )

        assert result["status"] == "CLOSED_NO_SL"
        assert result["reason"] == "sl_failed_emergency_close"
        assert result["filled_qty"] == 0
        assert mock_broker._mock_req.call_count == 3

    def test_close_position_cancels_orphan_sl(self, mock_broker):
        """close_position should cancel remaining SL orders for the symbol."""
        # Mock get_positions to return a LONG position
        with patch.object(mock_broker, 'get_positions', return_value=[
            {"symbol": "BTCUSDC", "side": "LONG", "qty": 0.00050,
             "market_price": 85000, "current_price": 85000},
        ]):
            mock_broker._mock_req.side_effect = [
                # 1st: market SELL to close
                {
                    "orderId": 3001,
                    "status": "FILLED",
                    "executedQty": "0.00050",
                    "fills": [{"price": "85100.00", "qty": "0.00050"}],
                },
                # 2nd: get open orders for symbol (orphan SL cleanup)
                [
                    {"orderId": 1002, "symbol": "BTCUSDC", "type": "STOP_LOSS_LIMIT"},
                ],
                # 3rd: cancel the orphan SL
                {"orderId": 1002, "status": "CANCELED"},
            ]

            result = mock_broker.close_position("BTCUSDC", _authorized_by="test_close")

            assert result["status"] == "FILLED"
            assert mock_broker._mock_req.call_count == 3

    def test_close_without_authorized_by_raises(self, mock_broker):
        """close_position without _authorized_by must raise."""
        with pytest.raises(BrokerError, match="authorized"):
            mock_broker.close_position("BTCUSDC")

    def test_create_without_authorized_by_raises(self, mock_broker):
        """create_position without _authorized_by must raise."""
        with pytest.raises(BrokerError, match="authorized"):
            mock_broker.create_position(
                symbol="BTCUSDC", direction="BUY", qty=0.001,
            )


class TestVerifySLExists:
    """verify_sl_exists checks that SL order is live on exchange."""

    def test_sl_found(self, mock_broker):
        mock_broker._mock_req.return_value = [
            {"orderId": 5001, "type": "STOP_LOSS_LIMIT", "symbol": "BTCUSDC"},
        ]
        assert mock_broker.verify_sl_exists("BTCUSDC", "5001") is True

    def test_sl_missing(self, mock_broker):
        mock_broker._mock_req.return_value = []  # no open orders
        assert mock_broker.verify_sl_exists("BTCUSDC", "5001") is False

    def test_sl_none_id(self, mock_broker):
        assert mock_broker.verify_sl_exists("BTCUSDC", None) is False


class TestSlippageDetection:
    """Verify slippage is computed correctly."""

    def test_slippage_calculation(self):
        """1% slippage should be detected."""
        signal_price = 85000.0
        fill_price = 84150.0  # -1% slippage
        slippage_pct = abs(fill_price - signal_price) / signal_price * 100
        assert slippage_pct == pytest.approx(1.0, abs=0.01)

    def test_no_slippage(self):
        signal_price = 85000.0
        fill_price = 85000.0
        slippage_pct = abs(fill_price - signal_price) / signal_price * 100
        assert slippage_pct == 0.0


class TestValidateOrderCrypto:
    """CryptoRiskManager.validate_order integration."""

    def test_valid_order_passes(self, tmp_path):
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=20_000)
        rm.kill_switch._STATE_PATH = tmp_path / "ks_valid.json"
        rm.kill_switch._active = False
        passed, msg = rm.validate_order(notional=1000, strategy="test", current_equity=20_000)
        assert passed is True
        assert msg == "OK"

    def test_oversized_order_rejected(self, tmp_path):
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=20_000)
        rm.kill_switch._STATE_PATH = tmp_path / "ks_oversize.json"
        rm.kill_switch._active = False
        # 20% of 20K = 4000 > max 15%
        passed, msg = rm.validate_order(notional=4000, strategy="test", current_equity=20_000)
        assert passed is False
        assert "position" in msg

    def test_kill_switch_active_rejects(self, tmp_path):
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=20_000)
        rm.kill_switch._STATE_PATH = tmp_path / "ks.json"
        rm.kill_switch._active = True
        rm.kill_switch._trigger_reason = "test_kill"
        passed, msg = rm.validate_order(notional=100, strategy="test", current_equity=20_000)
        assert passed is False
        assert "kill switch" in msg
