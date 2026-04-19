"""Broker contract validators + ContractRunner regression tests.

Phase 4 XXL plan: contracts existed but had no tests, no wiring. This locks
the safety net so we know the contracts catch the failure modes they target.
"""
from __future__ import annotations

import pytest

from core.broker.contracts.alpaca_contracts import AlpacaContract
from core.broker.contracts.binance_contracts import BinanceContract
from core.broker.contracts.contract_runner import ContractRunner
from core.broker.contracts.ibkr_contracts import IBKRContract


# ---------------------------------------------------------------------------
# Binance contracts
# ---------------------------------------------------------------------------

class TestBinanceContracts:
    def test_account_balance_ok(self):
        resp = {
            "balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.0"},
                {"asset": "USDT", "free": "10000", "locked": "0"},
            ],
            "canTrade": True,
            "canWithdraw": True,
        }
        ok, msg = BinanceContract.account_balance(resp)
        assert ok, msg

    def test_account_balance_missing_keys(self):
        resp = {"balances": []}
        ok, msg = BinanceContract.account_balance(resp)
        assert not ok and "Missing" in msg

    def test_account_balance_non_numeric(self):
        resp = {
            "balances": [{"asset": "BTC", "free": "not-a-number", "locked": "0"}],
            "canTrade": True, "canWithdraw": True,
        }
        ok, msg = BinanceContract.account_balance(resp)
        assert not ok and "non-numeric" in msg.lower()

    def test_order_response_ok(self):
        resp = {
            "symbol": "BTCUSDT", "orderId": 12345, "status": "FILLED",
            "type": "MARKET", "side": "BUY", "executedQty": "0.1",
            "cummulativeQuoteQty": "5000",
        }
        ok, msg = BinanceContract.order_response(resp)
        assert ok, msg

    def test_klines_ok(self):
        resp = [[1, "100", "110", "90", "105", "1000", 2, "5000", 10, "500", "2500", "0"]]
        ok, _ = BinanceContract.klines(resp)
        assert ok

    def test_klines_too_few_columns(self):
        resp = [[1, "100", "110"]]  # only 3 elements
        ok, msg = BinanceContract.klines(resp)
        assert not ok and "elements" in msg

    def test_margin_account_ok(self):
        resp = {
            "marginLevel": "5.0",
            "totalAssetOfBtc": "0.5",
            "totalLiabilityOfBtc": "0.1",
        }
        ok, _ = BinanceContract.margin_account(resp)
        assert ok


# ---------------------------------------------------------------------------
# IBKR contracts
# ---------------------------------------------------------------------------

class TestIBKRContracts:
    def test_account_info_ok(self):
        resp = {"equity": 9900.0, "cash": 5000.0}
        ok, _ = IBKRContract.account_info(resp)
        assert ok

    def test_account_info_missing(self):
        ok, msg = IBKRContract.account_info({"cash": 1000})
        assert not ok and "equity" in msg

    def test_positions_list_ok(self):
        resp = [{"symbol": "MES", "qty": 1}, {"symbol": "MGC", "qty": -2}]
        ok, _ = IBKRContract.positions_list(resp)
        assert ok

    def test_positions_list_not_a_list(self):
        ok, msg = IBKRContract.positions_list({"positions": []})
        assert not ok and "list" in msg.lower()


# ---------------------------------------------------------------------------
# Alpaca contracts
# ---------------------------------------------------------------------------

class TestAlpacaContracts:
    def test_account_ok(self):
        resp = {"equity": 100_000.0, "cash": 50_000.0}
        ok, _ = AlpacaContract.account(resp)
        assert ok

    def test_order_ok(self):
        resp = {"id": "abc", "status": "filled", "symbol": "AAPL", "side": "buy", "qty": "10"}
        ok, _ = AlpacaContract.order(resp)
        assert ok


# ---------------------------------------------------------------------------
# ContractRunner: tolerance + escalation
# ---------------------------------------------------------------------------

class TestContractRunnerTolerance:
    def test_single_violation_warns_not_critical(self):
        alerts: list[tuple[str, str]] = []
        runner = ContractRunner(alert_callback=lambda msg, lvl: alerts.append((msg, lvl)))

        runner.validate(
            "binance", "account", {},
            contract_fn=lambda r: (False, "missing keys"),
        )
        assert len(alerts) == 1
        assert alerts[0][1] == "warning"

    def test_three_consecutive_violations_critical(self):
        alerts: list[tuple[str, str]] = []
        runner = ContractRunner(alert_callback=lambda msg, lvl: alerts.append((msg, lvl)))

        for _ in range(3):
            runner.validate(
                "binance", "account", {},
                contract_fn=lambda r: (False, "missing keys"),
            )
        # Last alert should be critical
        assert alerts[-1][1] == "critical"
        assert "CONTRACT CRITICAL" in alerts[-1][0]

    def test_pass_resets_consecutive_count(self):
        alerts: list[tuple[str, str]] = []
        runner = ContractRunner(alert_callback=lambda msg, lvl: alerts.append((msg, lvl)))
        # 2 fails
        for _ in range(2):
            runner.validate("binance", "account", {}, lambda r: (False, "X"))
        # 1 success — should reset counter
        runner.validate("binance", "account", {"ok": 1}, lambda r: (True, "OK"))
        # 2 more fails — should still be at 2/3, not escalate
        for _ in range(2):
            runner.validate("binance", "account", {}, lambda r: (False, "Y"))
        # No critical alert in the post-reset failures
        post_reset_alerts = [a for a in alerts if a[1] == "critical"]
        assert post_reset_alerts == []

    def test_is_contract_healthy_after_violations(self):
        runner = ContractRunner()
        runner.validate("binance", "account", {}, lambda r: (False, "X"))
        runner.validate("binance", "account", {}, lambda r: (False, "X"))
        # 2 violations -> still healthy (threshold is 3)
        assert runner.is_contract_healthy("binance") is True
        runner.validate("binance", "account", {}, lambda r: (False, "X"))
        # 3 violations -> NOT healthy
        assert runner.is_contract_healthy("binance") is False

    def test_metrics_callback_invoked_on_pass_and_fail(self):
        metrics: list[tuple[str, float, dict]] = []
        runner = ContractRunner(
            metrics_callback=lambda name, val, tags: metrics.append((name, val, tags)),
        )
        runner.validate("binance", "account", {}, lambda r: (True, "OK"))
        runner.validate("binance", "account", {}, lambda r: (False, "X"))
        names = [m[0] for m in metrics]
        assert any("contract_ok" in n for n in names)
        assert any("contract_violation" in n for n in names)
