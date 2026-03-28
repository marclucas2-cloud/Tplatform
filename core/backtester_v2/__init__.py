"""BacktesterV2 — Event-driven backtesting engine with anti-lookahead protection."""

__version__ = "2.0.0"

from core.backtester_v2.types import (
    EventType, Event, Bar, Signal, Order, Fill,
    MarketState, PortfolioState, BacktestConfig, BacktestResults,
)
from core.backtester_v2.event_queue import EventQueue
from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.engine import BacktesterV2
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.walk_forward import (
    WalkForwardEngine, WFConfig, WFWindowResult, WFResult,
)
from core.backtester_v2.monte_carlo import MonteCarloEngine, MCResult

__all__ = [
    "EventType", "Event", "Bar", "Signal", "Order", "Fill",
    "MarketState", "PortfolioState", "BacktestConfig", "BacktestResults",
    "EventQueue", "DataFeed", "BacktesterV2", "StrategyBase",
    "WalkForwardEngine", "WFConfig", "WFWindowResult", "WFResult",
    "MonteCarloEngine", "MCResult",
]
