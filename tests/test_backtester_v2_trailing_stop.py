from __future__ import annotations

import pandas as pd

from core.backtester_v2.engine import BacktesterV2
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import BacktestConfig, Bar, PortfolioState, Signal


class _OneShotStrategy(StrategyBase):
    def __init__(
        self,
        *,
        side: str,
        trailing_stop_pct: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None:
        self._side = side
        self._trailing_stop_pct = trailing_stop_pct
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._fired = False

    @property
    def name(self) -> str:
        return "oneshot_trailing"

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self._fired:
            return None
        self._fired = True
        return Signal(
            symbol=bar.symbol,
            side=self._side,
            strategy_name=self.name,
            stop_loss=self._stop_loss,
            take_profit=self._take_profit,
            trailing_stop_pct=self._trailing_stop_pct,
        )


def _config(df: pd.DataFrame) -> BacktestConfig:
    return BacktestConfig(
        data_sources={"TEST": df},
        brokers={"default": {"commission_per_share": 0.0, "slippage_bps": 0.0}},
        execution={"latency_ms": 0.0, "fill_ratio": 1.0},
    )


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="1D")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1000.0] * len(rows),
        },
        index=idx,
    )


def test_long_trailing_stop_ratcheted_then_gap_exit_next_bar():
    df = _bars([
        (100.0, 102.0, 99.0, 100.0),   # entry @ 100
        (101.0, 120.0, 100.0, 118.0),  # ratchet to 108, but no same-bar exit
        (107.0, 110.0, 100.0, 101.0),  # gap below trailing stop => fill @ open 107
    ])
    bt = BacktesterV2(_config(df), seed=1)
    strategy = _OneShotStrategy(side="BUY", trailing_stop_pct=0.10)
    result = bt.run([strategy], df.index[0], df.index[-1])

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["side"] == "SELL"
    assert trade["position_side"] == "LONG"
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 107.0
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl"] == 700.0


def test_short_trailing_stop_ratcheted_then_gap_exit_next_bar():
    df = _bars([
        (100.0, 101.0, 99.0, 100.0),  # short entry @ 100
        (99.0, 100.0, 80.0, 82.0),    # ratchet stop down to 88
        (89.0, 95.0, 85.0, 94.0),     # gap above trailing stop => cover @ open 89
    ])
    bt = BacktesterV2(_config(df), seed=1)
    strategy = _OneShotStrategy(side="SELL", trailing_stop_pct=0.10)
    result = bt.run([strategy], df.index[0], df.index[-1])

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["side"] == "BUY"
    assert trade["position_side"] == "SHORT"
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 89.0
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl"] == 1100.0


def test_fixed_stop_is_not_loosened_by_wider_trailing_candidate():
    df = _bars([
        (100.0, 102.0, 99.0, 100.0),  # entry @ 100, fixed stop = 95
        (101.0, 104.0, 96.0, 103.0),  # trailing candidate 93.6, fixed stop stays tighter
        (94.0, 96.0, 90.0, 92.0),     # stop hits at open 94 (gap through fixed stop 95)
    ])
    bt = BacktesterV2(_config(df), seed=1)
    strategy = _OneShotStrategy(
        side="BUY",
        trailing_stop_pct=0.10,
        stop_loss=95.0,
    )
    result = bt.run([strategy], df.index[0], df.index[-1])

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["exit_price"] == 94.0
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl"] == -600.0
