"""BacktesterV2 — Event-driven backtesting engine with anti-lookahead protection."""

__version__ = "2.0.0"

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.engine import BacktesterV2
from core.backtester_v2.event_queue import EventQueue
from core.backtester_v2.monte_carlo import MCResult, MonteCarloEngine
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import (
    BacktestConfig,
    BacktestResults,
    Bar,
    Event,
    EventType,
    Fill,
    MarketState,
    Order,
    PortfolioState,
    Signal,
)
from core.backtester_v2.walk_forward import (
    WalkForwardEngine,
    WFConfig,
    WFResult,
    WFWindowResult,
)

__all__ = [
    "BacktestConfig",
    "BacktestResults",
    "BacktesterV2",
    "Bar",
    "DataFeed",
    "Event",
    "EventQueue",
    "EventType",
    "Fill",
    "MCResult",
    "MarketState",
    "MonteCarloEngine",
    "Order",
    "PortfolioState",
    "Signal",
    "StrategyBase",
    "WFConfig",
    "WFResult",
    "WFWindowResult",
    "WalkForwardEngine",
]
