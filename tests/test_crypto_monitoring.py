"""Tests for crypto monitoring V2 (margin + earn) — 16 tests."""
from unittest.mock import MagicMock


from core.crypto.monitoring import CryptoAlerter, CryptoReconciliation


class TestAlerterV2:
    def test_initial_empty(self):
        assert CryptoAlerter().get_recent_alerts() == []

    def test_alert_logged(self):
        a = CryptoAlerter()
        a.alert("INFO", "test", "test")
        assert len(a.get_recent_alerts()) == 1

    def test_critical_no_cooldown(self):
        a = CryptoAlerter()
        a.alert("CRITICAL", "msg1", "t")
        a.alert("CRITICAL", "msg2", "t")
        assert len(a.get_recent_alerts()) == 2

    def test_cooldown_warning(self):
        a = CryptoAlerter()
        a.alert("WARNING", "m1", "same")
        a.alert("WARNING", "m2", "same")
        assert len(a.get_recent_alerts()) == 1

    def test_trade_executed(self):
        a = CryptoAlerter()
        a.trade_executed("BTCUSDT", "BUY", 0.1, 42000, "trend")
        assert "BUY" in a.get_recent_alerts()[0]["message"]

    def test_margin_level_warning(self):
        """margin_level_warning(margin_level, threshold=1.8) — no symbol param."""
        a = CryptoAlerter()
        a.margin_level_warning(1.4)
        alerts = a.get_recent_alerts()
        assert len(alerts) == 1
        assert "margin" in alerts[0]["message"].lower()

    def test_borrow_rate_alert(self):
        """borrow_rate_spike(symbol, current_rate, previous_rate, multiplier)."""
        a = CryptoAlerter()
        a.borrow_rate_spike("BTC", 0.015, 0.005, 3.0)
        alerts = a.get_recent_alerts()
        assert len(alerts) == 1
        assert "borrow" in alerts[0]["message"].lower() or "rate" in alerts[0]["message"].lower()

    def test_kill_switch(self):
        a = CryptoAlerter()
        a.kill_switch_triggered("daily_loss")
        assert a.get_recent_alerts()[0]["level"] == "CRITICAL"

    def test_telegram(self):
        mock = MagicMock()
        a = CryptoAlerter(telegram_bot=mock)
        a.alert("INFO", "test", "t")
        mock.send_message.assert_called_once()


class TestReconciliationV2:
    def test_no_broker(self):
        r = CryptoReconciliation()
        assert r.reconcile([], 15000)["ok"] is False

    def test_clean(self):
        """Reconciliation passes when exchange state matches local state."""
        b = MagicMock()
        b.get_positions.return_value = [
            {"symbol": "BTCUSDT", "asset_type": "CRYPTO_SPOT"},
        ]
        b.get_account_info.return_value = {
            "equity": 15000,
            "spot_usdt": 15000,
            "margin_usdt": 0,
            "earn_usdt": 0,
        }
        b.get_orders.return_value = []
        b.get_margin_account.return_value = {"margin_level": 2.5}
        r = CryptoReconciliation(broker=b)
        # Local position must specify wallet="spot" and symbol to match exchange
        result = r.reconcile([{"symbol": "BTCUSDT", "wallet": "spot"}], 15000)
        assert result["ok"]

    def test_orphan(self):
        b = MagicMock()
        b.get_positions.return_value = [
            {"symbol": "BTCUSDT", "asset_type": "CRYPTO_SPOT"},
            {"symbol": "ETHUSDT", "asset_type": "CRYPTO_MARGIN"},
        ]
        b.get_account_info.return_value = {
            "equity": 15000,
            "spot_usdt": 15000,
            "margin_usdt": 0,
            "earn_usdt": 0,
        }
        b.get_orders.return_value = []
        b.get_margin_account.return_value = {}
        r = CryptoReconciliation(broker=b)
        result = r.reconcile([{"symbol": "BTCUSDT", "wallet": "spot"}], 15000)
        assert any(d["type"] == "orphan" for d in result["divergences"])

    def test_balance_mismatch(self):
        b = MagicMock()
        b.get_positions.return_value = []
        b.get_account_info.return_value = {
            "equity": 15100,
            "spot_usdt": 15100,
            "margin_usdt": 0,
            "earn_usdt": 0,
        }
        b.get_orders.return_value = []
        b.get_margin_account.return_value = {}
        r = CryptoReconciliation(broker=b)
        result = r.reconcile([], 15000)
        assert any(d["type"] == "balance_mismatch" for d in result["divergences"])

    def test_margin_mode_check(self):
        """V2: reconciliation checks margin mode is ISOLATED."""
        b = MagicMock()
        b.get_positions.return_value = [
            {"symbol": "BTCUSDT", "asset_type": "CRYPTO_MARGIN", "margin_type": "CROSS"},
        ]
        b.get_account_info.return_value = {
            "equity": 15000,
            "spot_usdt": 15000,
            "margin_usdt": 0,
            "earn_usdt": 0,
        }
        b.get_orders.return_value = []
        b.get_margin_account.return_value = {}
        r = CryptoReconciliation(broker=b)
        result = r.reconcile([{"symbol": "BTCUSDT", "wallet": "margin"}], 15000)
        assert any(d["type"] == "wrong_margin_mode" for d in result["divergences"])

    def test_history(self):
        r = CryptoReconciliation()
        r._divergences = [{"type": "test"}] * 30
        assert len(r.get_history(10)) == 10
