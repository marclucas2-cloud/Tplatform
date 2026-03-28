"""Tests for CryptoBacktester V2 (margin-aware) — 22 tests."""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

from core.crypto.backtest_engine import (
    CryptoBacktester, CryptoPosition, SlippageModel,
    CommissionModel, CryptoWalkForward,
)


@pytest.fixture
def sample_data():
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=1000, freq="1h", tz=timezone.utc)
    close = 40000 + np.cumsum(np.random.randn(1000) * 100)
    return pd.DataFrame({
        "timestamp": dates,
        "open": close - np.random.rand(1000) * 50,
        "high": close + np.abs(np.random.randn(1000)) * 100,
        "low": close - np.abs(np.random.randn(1000)) * 100,
        "close": close,
        "volume": np.random.rand(1000) * 1e6 + 1e5,
    })


def simple_strategy(candle, state, **kwargs):
    i = state.get("i", 0)
    if i < 5:
        return None
    positions = state.get("positions", [])
    if len(positions) > 0:
        if candle["close"] < candle["open"]:
            return {"action": "CLOSE", "reason": "bearish", "strategy": "test"}
        return None
    if candle["close"] > candle["open"]:
        return {"action": "BUY", "pct": 0.10, "stop_loss": candle["close"] * 0.95, "take_profit": candle["close"] * 1.10, "strategy": "test"}
    return None


class TestSlippageModelV2:
    def test_btc_lowest(self):
        assert SlippageModel.estimate("BTCUSDT", 10000) < SlippageModel.estimate("AVAXUSDT", 10000)

    def test_spot_higher_than_before(self):
        """V2: spot slippage is slightly higher than perp."""
        s = SlippageModel.estimate("BTCUSDT", 10000)
        assert s > 0.0001  # At least 1 bps

    def test_larger_order(self):
        assert SlippageModel.estimate("BTCUSDT", 1_000_000) > SlippageModel.estimate("BTCUSDT", 10_000)

    def test_tier2_classification(self):
        for sym in ["SOLUSDT", "BNBUSDT", "XRPUSDT"]:
            assert SlippageModel.estimate(sym, 10000) > 0


class TestCommissionModelV2:
    def test_spot_margin_same_rate(self):
        """V2: spot and margin have same 0.1% commission (not 0.02% like futures)."""
        s = CommissionModel.calculate(10000, "spot", "taker")
        m = CommissionModel.calculate(10000, "margin", "taker")
        assert s == m

    def test_commission_is_01pct(self):
        """Spot/margin commission = 0.1%."""
        c = CommissionModel.calculate(10000, "spot", "taker")
        assert abs(c - 10) < 1  # 0.1% of 10K = $10

    def test_proportional(self):
        c1 = CommissionModel.calculate(10000, "spot")
        c2 = CommissionModel.calculate(20000, "spot")
        assert abs(c2 - 2 * c1) < 0.01


class TestCryptoPositionV2:
    def test_long_spot(self):
        pos = CryptoPosition("BTCUSDT", 1, 0.1, 40000, datetime.now(timezone.utc))
        assert pos.is_long is True
        assert pos.notional == 4000

    def test_short_margin(self):
        pos = CryptoPosition("BTCUSDT", -1, 0.1, 40000, datetime.now(timezone.utc), market_type="margin", is_margin_borrow=True, borrowed_amount=0.1)
        assert pos.is_long is False
        assert pos.is_margin_borrow is True

    def test_borrow_cost_tracking(self):
        pos = CryptoPosition("BTCUSDT", -1, 0.1, 40000, datetime.now(timezone.utc), is_margin_borrow=True)
        assert pos.total_borrow_cost == 0  # Initially zero


class TestBacktesterV2:
    def test_run_produces_trades(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, simple_strategy, symbol="BTCUSDT")
        assert result["n_trades"] > 0

    def test_empty_data(self):
        bt = CryptoBacktester()
        assert bt.run(pd.DataFrame(), simple_strategy)["n_trades"] == 0

    def test_costs_realistic_v2(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, simple_strategy, symbol="BTCUSDT")
        assert result["total_commissions"] > 0
        assert result["total_borrow_cost"] >= 0

    def test_final_equity(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, simple_strategy, symbol="BTCUSDT")
        assert 100 < result["final_equity"] < 1_000_000

    def test_max_positions(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000, max_positions=1)
        def multi_buy(c, s, **kw):
            if not s.get("positions") and s.get("i", 0) > 5:
                return {"action": "BUY", "pct": 0.05, "strategy": "test"}
            return None
        result = bt.run(sample_data, multi_buy, symbol="BTCUSDT")
        for eq in result["equity_curve"]:
            assert eq["positions"] <= 1

    def test_no_signal(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, lambda c, s, **kw: None, symbol="BTCUSDT")
        assert result["n_trades"] == 0
        assert result["final_equity"] == 15000

    def test_sharpe(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, simple_strategy, symbol="BTCUSDT")
        assert isinstance(result["sharpe"], (int, float))

    def test_drawdown(self, sample_data):
        bt = CryptoBacktester(initial_capital=15000)
        result = bt.run(sample_data, simple_strategy, symbol="BTCUSDT")
        assert result["max_drawdown_pct"] <= 0


class TestWalkForwardV2:
    def test_insufficient_data(self):
        wf = CryptoWalkForward()
        df = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=10, freq="1h", tz=timezone.utc), "open": range(10), "high": range(1, 11), "low": range(10), "close": range(10), "volume": [100]*10})
        assert wf.validate(df, simple_strategy)["verdict"] == "REJECTED"

    def test_empty(self):
        assert CryptoWalkForward().validate(pd.DataFrame(), simple_strategy)["verdict"] == "REJECTED"
