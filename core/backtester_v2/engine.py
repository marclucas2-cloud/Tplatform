"""BacktesterV2 — Main event-driven backtesting engine.

Orchestrates event generation, routing, portfolio tracking, and
strategy execution with strict anti-lookahead guarantees.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.engine_helpers import (
    close_all_positions,
    get_drawdown,
    get_equity,
    get_exposure,
    get_portfolio_state,
    load_market_events,
    record_equity,
    schedule_periodic_events,
)
from core.backtester_v2.event_queue import EventQueue
from core.backtester_v2.execution import ExecutionSimulator
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import (
    BacktestConfig,
    BacktestResults,
    Bar,
    Event,
    EventType,
    Fill,
    Order,
    PortfolioState,
    Signal,
)

logger = logging.getLogger(__name__)


class BacktesterV2:
    """Event-driven backtesting engine with anti-lookahead DataFeed.

    Args:
        config: Full backtest configuration.
        seed: Random seed for reproducible execution simulation.
    """

    def __init__(self, config: BacktestConfig, seed: int = 42) -> None:
        self._config = config
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._feed = DataFeed(config.data_sources)
        self._queue = EventQueue()
        self._exec = ExecutionSimulator(config, self._rng)

        # Portfolio state
        self._cash: float = config.initial_capital
        self._positions: Dict[str, float] = {}  # symbol -> signed qty
        self._avg_costs: Dict[str, float] = {}  # symbol -> avg entry price
        self._peak_equity: float = config.initial_capital

        # Results
        self._results = BacktestResults(initial_capital=config.initial_capital)
        self._daily_pnl: float = 0.0

    def run(
        self,
        strategies: List[StrategyBase],
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> BacktestResults:
        """Run the backtest over the given period.

        Args:
            strategies: List of strategies to run.
            start: Start timestamp (inclusive).
            end: End timestamp (inclusive).

        Returns:
            BacktestResults with all metrics computed.
        """
        # Reset RNG for reproducibility
        self._rng = np.random.default_rng(self._seed)
        self._exec = ExecutionSimulator(self._config, self._rng)

        load_market_events(self._queue, self._config, start, end)
        schedule_periodic_events(self._queue, start, end)

        event_count = 0
        while not self._queue.is_empty():
            event = self._queue.pop()
            if event.timestamp < start or event.timestamp > end:
                continue
            self._handle_event(event, strategies)
            event_count += 1

        close_all_positions(self)

        logger.info(
            "Backtest complete: %d events, %d trades",
            event_count, len(self._results.trades),
        )
        return self._results.finalize()

    def _handle_event(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Route event to the appropriate handler."""
        handlers = {
            EventType.MARKET_DATA: self._on_market_data,
            EventType.SIGNAL: self._on_signal,
            EventType.ORDER: self._on_order,
            EventType.FILL: self._on_fill,
            EventType.EOD: self._on_eod,
            EventType.BORROW_INTEREST: self._on_interest,
            EventType.SWAP: self._on_swap,
        }
        handler = handlers.get(event.type)
        if handler:
            handler(event, strategies)

    def _on_market_data(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Handle market data: update feed, mark-to-market, poll strategies."""
        bar: Bar = event.data
        feed_ts = event.timestamp + pd.Timedelta(nanoseconds=1)
        self._feed.set_timestamp(feed_ts)
        record_equity(self, event.timestamp)

        portfolio_state = get_portfolio_state(self)
        for strategy in strategies:
            signal = strategy.on_bar(bar, portfolio_state)
            if signal is not None:
                self._queue.push(Event(
                    timestamp=event.timestamp,
                    type=EventType.SIGNAL,
                    data=signal,
                ))

    def _on_signal(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Convert signal to order after risk checks."""
        signal: Signal = event.data
        risk_limits = self._config.risk_limits
        equity = get_equity(self)

        max_pos_pct = risk_limits.get("max_position_pct", 0.10)
        position_value = max_pos_pct * equity

        dd = get_drawdown(self)
        if dd >= risk_limits.get("max_drawdown_pct", 0.15):
            return

        max_exp = risk_limits.get("max_exposure_pct", 1.0)
        current_exp = get_exposure(self) / equity if equity > 0 else 0
        if current_exp >= max_exp:
            return

        bar = self._feed.get_latest_bar(signal.symbol)
        if bar is None:
            return
        price = bar.close
        quantity = int(position_value / price) if price > 0 else 0
        if quantity <= 0:
            return

        order = Order(
            symbol=signal.symbol, side=signal.side,
            quantity=float(quantity), order_type=signal.order_type,
            timestamp=event.timestamp, strategy=signal.strategy_name,
            stop_loss=signal.stop_loss, take_profit=signal.take_profit,
        )
        self._queue.push(Event(
            timestamp=event.timestamp, type=EventType.ORDER, data=order,
        ))

    def _on_order(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Simulate order execution."""
        order: Order = event.data
        bar = self._feed.get_latest_bar(order.symbol)
        if bar is None:
            return
        fill = self._exec.simulate_fill(order, bar)
        self._queue.push(Event(
            timestamp=event.timestamp, type=EventType.FILL, data=fill,
        ))

    def _on_fill(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Update portfolio on fill, notify strategy."""
        fill: Fill = event.data
        if fill.rejected:
            return

        symbol = fill.order.symbol
        signed_qty = fill.quantity if fill.order.side == "BUY" else -fill.quantity
        old_qty = self._positions.get(symbol, 0.0)
        new_qty = old_qty + signed_qty

        if fill.order.side == "BUY":
            self._cash -= fill.price * fill.quantity + fill.commission
            if old_qty >= 0:
                old_cost = self._avg_costs.get(symbol, 0.0) * old_qty
                self._avg_costs[symbol] = (
                    (old_cost + fill.price * fill.quantity) / max(new_qty, 1e-10)
                )
        else:
            self._cash += fill.price * fill.quantity - fill.commission
            if old_qty > 0:
                entry = self._avg_costs.get(symbol, fill.price)
                pnl = (fill.price - entry) * min(abs(signed_qty), old_qty)
                pnl -= fill.commission
                self._results.trades.append({
                    "symbol": symbol, "side": fill.order.side,
                    "entry_price": entry, "exit_price": fill.price,
                    "quantity": abs(fill.quantity), "pnl": round(pnl, 4),
                    "commission": fill.commission,
                    "strategy": fill.order.strategy,
                    "timestamp": str(event.timestamp),
                })

        if abs(new_qty) < 1e-10:
            self._positions.pop(symbol, None)
            self._avg_costs.pop(symbol, None)
        else:
            self._positions[symbol] = new_qty

        for s in strategies:
            if s.name == fill.order.strategy:
                s.on_fill(fill)

    def _on_eod(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """End-of-day processing."""
        for s in strategies:
            s.on_eod(event.timestamp)
        self._daily_pnl = 0.0
        record_equity(self, event.timestamp)

    def _on_interest(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Apply borrow interest on short positions."""
        daily_rate = 0.02 / 252
        for symbol, qty in self._positions.items():
            if qty < 0:
                bar = self._feed.get_latest_bar(symbol)
                if bar:
                    self._cash -= abs(qty) * bar.close * daily_rate

    def _on_swap(
        self, event: Event, strategies: List[StrategyBase]
    ) -> None:
        """Apply FX swap costs for overnight positions."""
        pass
