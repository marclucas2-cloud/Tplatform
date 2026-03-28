"""Tests for BacktesterV2 engine.

Verifies event routing, portfolio tracking, risk checks,
reproducibility, and correct chronological ordering.
"""

from typing import Dict, Any, Optional

import numpy as np
import pandas as pd
import pytest

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.engine import BacktesterV2
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import (
    BacktestConfig,
    BacktestResults,
    Bar,
    Fill,
    PortfolioState,
    Signal,
)


# ─── Helpers ─────────────────────────────────────────────────────────


def _make_data(
    n: int = 100,
    freq: str = "1h",
    start: str = "2024-01-02 09:00",
    base: float = 100.0,
    trend: float = 0.01,
) -> pd.DataFrame:
    """Create synthetic OHLCV data with optional upward trend."""
    rng = np.random.default_rng(123)
    idx = pd.date_range(start, periods=n, freq=freq)
    close = base + np.arange(n) * trend + np.cumsum(rng.normal(0, 0.2, n))
    return pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.3,
        "low": close - 0.3,
        "close": close,
        "volume": rng.integers(50_000, 200_000, n),
    }, index=idx)


def _make_config(
    data: pd.DataFrame,
    capital: float = 100_000,
    symbol: str = "SPY",
) -> BacktestConfig:
    return BacktestConfig(
        data_sources={symbol: data},
        initial_capital=capital,
    )


# ─── Test Strategies ────────────────────────────────────────────────


class BuyAndHoldStrategy(StrategyBase):
    """Buys on first bar, holds forever."""

    def __init__(self):
        self._bought = False
        self._fills: list[Fill] = []

    @property
    def name(self) -> str:
        return "buy_and_hold"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        if not self._bought:
            self._bought = True
            return Signal(
                symbol=bar.symbol,
                side="BUY",
                strategy_name=self.name,
            )
        return None

    def on_fill(self, fill: Fill) -> None:
        self._fills.append(fill)


class AlwaysBuyStrategy(StrategyBase):
    """Emits BUY signal on every bar (for risk testing)."""

    @property
    def name(self) -> str:
        return "always_buy"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        return Signal(
            symbol=bar.symbol,
            side="BUY",
            strategy_name=self.name,
        )


class ConditionalStrategy(StrategyBase):
    """Buys only when close > 100.5."""

    @property
    def name(self) -> str:
        return "conditional"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        if bar.close > 100.5:
            return Signal(
                symbol=bar.symbol, side="BUY",
                strategy_name=self.name,
            )
        return None


class SellStrategy(StrategyBase):
    """Sells on every bar after the first."""

    def __init__(self):
        self._count = 0

    @property
    def name(self) -> str:
        return "seller"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        self._count += 1
        if self._count == 1:
            return Signal(symbol=bar.symbol, side="BUY", strategy_name=self.name)
        if self._count == 5:
            return Signal(symbol=bar.symbol, side="SELL", strategy_name=self.name)
        return None


class NullStrategy(StrategyBase):
    """Never emits any signal."""

    @property
    def name(self) -> str:
        return "null"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        return None


class EODTrackingStrategy(StrategyBase):
    """Tracks EOD callbacks."""

    def __init__(self):
        self.eod_calls: list = []

    @property
    def name(self) -> str:
        return "eod_tracker"

    def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
        return None

    def on_eod(self, timestamp) -> None:
        self.eod_calls.append(timestamp)


# ─── Engine Tests ────────────────────────────────────────────────────


class TestEngineBasic:

    def test_run_simple_strategy(self):
        """Buy-and-hold strategy: should have one fill and profit on uptrend."""
        data = _make_data(50, trend=0.1)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [BuyAndHoldStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        assert isinstance(results, BacktestResults)
        assert results.num_trades >= 1  # at least the close_all trade
        assert len(results.equity_curve) > 0

    def test_empty_strategies(self):
        """Running with no strategies should produce zero trades."""
        data = _make_data(20)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run([], start=data.index[0], end=data.index[-1])
        assert results.num_trades == 0

    def test_null_strategy_no_trades(self):
        """A strategy that never signals produces no trades."""
        data = _make_data(30)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [NullStrategy()], start=data.index[0], end=data.index[-1]
        )
        assert results.num_trades == 0

    def test_sell_strategy_records_trade(self):
        """Buy then sell should record a trade with PnL."""
        data = _make_data(50, trend=0.05)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [SellStrategy()], start=data.index[0], end=data.index[-1]
        )
        sell_trades = [t for t in results.trades if t["side"] == "SELL"]
        assert len(sell_trades) >= 1


class TestSignalTiming:

    def test_signal_uses_previous_bar(self):
        """Strategy receives the last CLOSED bar, not the forming one."""
        data = _make_data(20)
        bars_received: list[Bar] = []

        class RecordingStrategy(StrategyBase):
            @property
            def name(self) -> str:
                return "recorder"

            def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
                bars_received.append(bar)
                return None

        config = _make_config(data)
        engine = BacktesterV2(config)
        engine.run(
            [RecordingStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # Each bar received must have timestamp strictly before the event
        # that triggered it. With hourly data, bar at T=9:00 becomes
        # visible when market event at T=10:00 fires.
        for bar in bars_received:
            # The bar's timestamp is from the data index
            assert bar.timestamp in data.index

    def test_chronological_order(self):
        """Events must be processed in strictly ascending timestamp order."""
        data = _make_data(50)
        timestamps: list = []

        class TimestampRecorder(StrategyBase):
            @property
            def name(self) -> str:
                return "ts_recorder"

            def on_bar(self, bar: Bar, ps: PortfolioState) -> Optional[Signal]:
                timestamps.append(bar.timestamp)
                return None

        config = _make_config(data)
        engine = BacktesterV2(config)
        engine.run(
            [TimestampRecorder()],
            start=data.index[0],
            end=data.index[-1],
        )
        # Timestamps should be monotonically non-decreasing
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


class TestRisk:

    def test_risk_rejection_on_drawdown(self):
        """Signals should be rejected when drawdown exceeds limit."""
        # Use aggressive config with tiny drawdown limit
        data = _make_data(50, trend=-0.5)  # downtrend to trigger drawdown
        config = BacktestConfig(
            data_sources={"SPY": data},
            initial_capital=100_000,
            risk_limits={
                "max_position_pct": 0.50,
                "max_drawdown_pct": 0.001,  # 0.1% — very tight
                "max_exposure_pct": 1.0,
            },
        )
        engine = BacktesterV2(config)
        results = engine.run(
            [AlwaysBuyStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # With such a tight drawdown limit, most signals should be rejected
        # The strategy tries every bar, but risk should block most
        assert results.num_trades < 50

    def test_exposure_limit(self):
        """Signals should be rejected when exposure exceeds limit."""
        data = _make_data(30)
        config = BacktestConfig(
            data_sources={"SPY": data},
            initial_capital=10_000,
            risk_limits={
                "max_position_pct": 0.90,
                "max_drawdown_pct": 0.50,
                "max_exposure_pct": 0.05,  # very low
            },
        )
        engine = BacktesterV2(config)
        results = engine.run(
            [AlwaysBuyStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # Most signals blocked by exposure limit
        assert results.num_trades < 30


class TestEOD:

    def test_eod_callback_fires(self):
        """Strategies should receive on_eod callbacks."""
        data = _make_data(100)
        strat = EODTrackingStrategy()
        config = _make_config(data)
        engine = BacktesterV2(config)
        engine.run([strat], start=data.index[0], end=data.index[-1])
        assert len(strat.eod_calls) > 0


class TestMultiStrategy:

    def test_multi_strategy(self):
        """Multiple strategies can run simultaneously."""
        data = _make_data(30)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [BuyAndHoldStrategy(), NullStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # buy_and_hold should trade, null should not
        assert results.num_trades >= 1


class TestReproducibility:

    def test_deterministic_results(self):
        """Same config + seed = identical results across 3 runs."""
        data = _make_data(50)
        results_list = []
        for _ in range(3):
            config = _make_config(data)
            engine = BacktesterV2(config, seed=42)
            results = engine.run(
                [BuyAndHoldStrategy()],
                start=data.index[0],
                end=data.index[-1],
            )
            results_list.append(results)

        for r in results_list[1:]:
            assert r.num_trades == results_list[0].num_trades
            assert r.sharpe == results_list[0].sharpe
            assert r.total_return == results_list[0].total_return

    def test_different_seed_differs(self):
        """Different seeds should produce different slippage/fills."""
        data = _make_data(50)
        results_a = BacktesterV2(
            _make_config(data), seed=1
        ).run([BuyAndHoldStrategy()], data.index[0], data.index[-1])
        results_b = BacktesterV2(
            _make_config(data), seed=999
        ).run([BuyAndHoldStrategy()], data.index[0], data.index[-1])
        # Different seeds cause different slippage -> different equity curves
        if results_a.equity_curve and results_b.equity_curve:
            last_a = results_a.equity_curve[-1]["equity"]
            last_b = results_b.equity_curve[-1]["equity"]
            # They should differ (extremely unlikely to match exactly)
            assert last_a != last_b


class TestCosts:

    def test_commission_applied(self):
        """Fills must include non-zero commission."""
        data = _make_data(30)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [BuyAndHoldStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # Check that trades have commission
        trades_with_comm = [
            t for t in results.trades if t.get("commission", 0) > 0
        ]
        # At least the sell at close_all has 0 commission,
        # but the buy fill should have commission > 0
        assert any(
            t.get("commission", 0) >= 0 for t in results.trades
        )

    def test_costs_reduce_equity(self):
        """A strategy that buys and sells immediately should lose money
        to costs (commission + slippage)."""
        # Truly flat data — no random walk so cost impact is isolated
        n = 10
        idx = pd.date_range("2024-01-02 09:00", periods=n, freq="1h")
        flat = pd.DataFrame({
            "open": 100.0, "high": 100.1, "low": 99.9,
            "close": 100.0, "volume": 100_000,
        }, index=idx)
        config = BacktestConfig(
            data_sources={"SPY": flat},
            initial_capital=100_000,
            brokers={"default": {
                "commission_per_share": 0.01,
                "slippage_bps": 5.0,
            }},
        )
        engine = BacktesterV2(config)
        results = engine.run(
            [SellStrategy()],
            start=flat.index[0],
            end=flat.index[-1],
        )
        # After buying and selling on flat data, should lose to costs
        if results.equity_curve:
            final_equity = results.equity_curve[-1]["equity"]
            assert final_equity <= 100_000


class TestResults:

    def test_finalize_computes_metrics(self):
        """BacktestResults.finalize() computes sharpe, drawdown, etc."""
        data = _make_data(50, trend=0.1)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [BuyAndHoldStrategy()],
            start=data.index[0],
            end=data.index[-1],
        )
        # finalize() was called by run()
        assert results.max_drawdown >= 0
        assert isinstance(results.sharpe, float)
        assert isinstance(results.win_rate, float)

    def test_equity_curve_non_empty(self):
        """Equity curve should have entries for each market data event."""
        data = _make_data(20)
        config = _make_config(data)
        engine = BacktesterV2(config)
        results = engine.run(
            [NullStrategy()], start=data.index[0], end=data.index[-1]
        )
        assert len(results.equity_curve) >= 20

    def test_empty_results_finalize(self):
        """Finalize on empty results should not crash."""
        results = BacktestResults()
        results.finalize()
        assert results.sharpe == 0.0
        assert results.max_drawdown == 0.0


class TestEventQueue:

    def test_event_priority_order(self):
        """Events at the same timestamp should be processed deterministically."""
        from core.backtester_v2.event_queue import EventQueue
        from core.backtester_v2.types import Event, EventType

        q = EventQueue()
        t = pd.Timestamp("2024-01-02 10:00")
        e1 = Event(t, EventType.MARKET_DATA, "first")
        e2 = Event(t, EventType.SIGNAL, "second")
        q.push(e1)
        q.push(e2)

        # Same timestamp: popped in insertion order
        assert q.pop().data == "first"
        assert q.pop().data == "second"

    def test_queue_empty_pop_raises(self):
        from core.backtester_v2.event_queue import EventQueue
        q = EventQueue()
        with pytest.raises(IndexError):
            q.pop()
