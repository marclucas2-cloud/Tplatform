"""Core data types for BacktesterV2.

All dataclasses and enums used across the engine. Immutable where possible
to prevent accidental mutation during backtests.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


class EventType(enum.Enum):
    """Event types processed by the engine, in priority order."""

    MARKET_DATA = 1
    SIGNAL = 2
    ORDER = 3
    FILL = 4
    FUNDING = 5
    BORROW_INTEREST = 6
    SWAP = 7
    EOD = 8
    REBALANCE = 9
    MARGIN_CHECK = 10
    ROLL = 11
    CIRCUIT_BREAKER = 12


@dataclass(frozen=True)
class Event:
    """A single event in the simulation timeline."""

    timestamp: pd.Timestamp
    type: EventType
    data: Any


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. Always represents a CLOSED candle."""

    symbol: str
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    """Trading signal emitted by a strategy."""

    symbol: str
    side: str  # "BUY" or "SELL"
    strategy_name: str
    order_type: str = "MARKET"  # MARKET, LIMIT
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strength: float = 1.0


@dataclass
class Order:
    """Order to be sent to the execution simulator."""

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    order_type: str  # MARKET, LIMIT
    timestamp: pd.Timestamp = field(default_factory=pd.Timestamp.now)
    strategy: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    broker: str = "default"
    limit_price: Optional[float] = None


@dataclass(frozen=True)
class Fill:
    """Execution report from the simulator."""

    order: Order
    price: float
    quantity: float
    commission: float
    slippage_bps: float
    latency_ms: float
    timestamp: pd.Timestamp
    rejected: bool = False
    reason: str = ""


@dataclass
class MarketState:
    """Snapshot of market conditions for a single symbol."""

    symbol: str
    mid_price: float
    bid: float
    ask: float
    spread_bps: float
    vol_1h: float
    vol_1d: float
    vol_30d: float
    current_volume: float
    avg_volume: float
    adv_20d: float
    asset_class: str
    hour: int
    is_open: bool


@dataclass
class PortfolioState:
    """Current portfolio snapshot passed to strategies."""

    equity: float
    cash: float
    positions: Dict[str, float] = field(default_factory=dict)
    exposure_long: float = 0.0
    exposure_short: float = 0.0
    drawdown_pct: float = 0.0
    margin_used: float = 0.0


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    data_sources: Dict[str, pd.DataFrame] = field(default_factory=dict)
    initial_capital: float = 100_000.0
    risk_limits: Dict[str, Any] = field(default_factory=lambda: {
        "max_position_pct": 0.10,
        "max_drawdown_pct": 0.15,
        "max_exposure_pct": 1.0,
    })
    brokers: Dict[str, Any] = field(default_factory=lambda: {
        "default": {"commission_per_share": 0.005, "slippage_bps": 2.0},
    })
    asset_classes: List[str] = field(default_factory=lambda: ["equity"])
    execution: Dict[str, Any] = field(default_factory=lambda: {
        "latency_ms": 1.0,
        "fill_ratio": 1.0,
    })


@dataclass
class BacktestResults:
    """Backtest output with lazy metric computation."""

    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    initial_capital: float = 100_000.0

    # Computed by finalize()
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_return: float = 0.0
    num_trades: int = 0

    def finalize(self) -> "BacktestResults":
        """Compute all summary metrics from trades and equity curve.

        Returns:
            self, for chaining.
        """
        self.num_trades = len(self.trades)

        if not self.equity_curve:
            return self

        eq = pd.DataFrame(self.equity_curve)
        eq["equity"] = eq["equity"].astype(float)

        # Total return
        self.total_return = (
            (eq["equity"].iloc[-1] / self.initial_capital) - 1.0
        )

        # Max drawdown
        peak = eq["equity"].cummax()
        dd = (eq["equity"] - peak) / peak
        self.max_drawdown = abs(dd.min()) if len(dd) > 0 else 0.0

        # Sharpe ratio (annualized, assuming hourly bars)
        returns = eq["equity"].pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            # Detect frequency from timestamps
            if "timestamp" in eq.columns and len(eq) > 1:
                median_delta = pd.to_datetime(eq["timestamp"]).diff().median()
                hours = max(median_delta.total_seconds() / 3600, 0.25)
                periods_per_year = 252 * 6.5 / hours  # trading hours
            else:
                periods_per_year = 252 * 6.5  # default hourly
            self.sharpe = float(
                returns.mean() / returns.std() * math.sqrt(periods_per_year)
            )
        else:
            self.sharpe = 0.0

        # Win rate and profit factor from trades
        if self.trades:
            pnls = [t.get("pnl", 0.0) for t in self.trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            self.win_rate = len(wins) / len(pnls) if pnls else 0.0
            gross_profit = sum(wins) if wins else 0.0
            gross_loss = abs(sum(losses)) if losses else 0.0
            self.profit_factor = (
                gross_profit / gross_loss if gross_loss > 0 else float("inf")
            )

        return self
