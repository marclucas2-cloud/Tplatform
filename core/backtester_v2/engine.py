"""BacktesterV2 — Main event-driven backtesting engine.

Orchestrates event generation, routing, portfolio tracking, and
strategy execution with strict anti-lookahead guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Dict, List

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
    Signal,
)

logger = logging.getLogger(__name__)


@dataclass
class _ProtectiveExitState:
    """Runtime stop / take-profit / trailing state for one open symbol."""

    strategy: str
    side: int  # +1 long, -1 short
    base_stop_loss: float | None = None
    active_stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop_pct: float | None = None
    high_watermark: float | None = None
    low_watermark: float | None = None


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
        self._protective_exits: Dict[str, _ProtectiveExitState] = {}
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
        self._process_protective_exits(bar, strategies)
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
            trailing_stop_pct=signal.trailing_stop_pct,
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
        side_sign = 1 if fill.order.side == "BUY" else -1
        signed_qty = fill.quantity if side_sign == 1 else -fill.quantity
        old_qty = self._positions.get(symbol, 0.0)
        new_qty = old_qty + signed_qty

        if side_sign == 1:
            self._cash -= fill.price * fill.quantity + fill.commission
        else:
            self._cash += fill.price * fill.quantity - fill.commission

        old_side = 0
        if old_qty > 0:
            old_side = 1
        elif old_qty < 0:
            old_side = -1

        # Open / add in the same direction.
        if old_side == 0 or old_side == side_sign:
            old_abs = abs(old_qty)
            total_qty = old_abs + fill.quantity
            entry = self._avg_costs.get(symbol, fill.price)
            self._avg_costs[symbol] = (
                (entry * old_abs + fill.price * fill.quantity) / max(total_qty, 1e-10)
            )
            self._positions[symbol] = new_qty
            self._upsert_protective_state(symbol, fill, side_sign)
        else:
            closing_qty = min(abs(old_qty), fill.quantity)
            entry = self._avg_costs.get(symbol, fill.price)
            commission_close = fill.commission * (closing_qty / max(fill.quantity, 1e-10))
            pnl = (fill.price - entry) * closing_qty * old_side
            pnl -= commission_close
            self._results.trades.append({
                "symbol": symbol,
                "side": fill.order.side,
                "position_side": "LONG" if old_side == 1 else "SHORT",
                "entry_price": entry,
                "exit_price": fill.price,
                "quantity": abs(closing_qty),
                "pnl": round(pnl, 4),
                "commission": round(commission_close, 4),
                "strategy": fill.order.strategy,
                "timestamp": str(event.timestamp),
                "exit_reason": fill.reason or "signal",
            })

            residual_old = abs(old_qty) - closing_qty
            opening_qty = fill.quantity - closing_qty

            if residual_old <= 1e-10:
                self._positions.pop(symbol, None)
                self._avg_costs.pop(symbol, None)
                self._protective_exits.pop(symbol, None)
            else:
                self._positions[symbol] = old_side * residual_old

            if opening_qty > 1e-10:
                flipped_qty = side_sign * opening_qty
                self._positions[symbol] = flipped_qty
                self._avg_costs[symbol] = fill.price
                self._upsert_protective_state(symbol, fill, side_sign)

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

    def _upsert_protective_state(self, symbol: str, fill: Fill, side_sign: int) -> None:
        """Create or refresh protective exits for an open position."""
        trailing_pct = fill.order.trailing_stop_pct
        base_stop = fill.order.stop_loss
        active_stop = base_stop
        high_watermark = None
        low_watermark = None

        if trailing_pct is not None and trailing_pct > 0:
            if side_sign == 1:
                trailing_stop = fill.price * (1.0 - trailing_pct)
                active_stop = max(x for x in [base_stop, trailing_stop] if x is not None)
                high_watermark = fill.price
            else:
                trailing_stop = fill.price * (1.0 + trailing_pct)
                active_stop = min(x for x in [base_stop, trailing_stop] if x is not None)
                low_watermark = fill.price

        state = self._protective_exits.get(symbol)
        if state is None or state.side != side_sign:
            self._protective_exits[symbol] = _ProtectiveExitState(
                strategy=fill.order.strategy,
                side=side_sign,
                base_stop_loss=base_stop,
                active_stop_loss=active_stop,
                take_profit=fill.order.take_profit,
                trailing_stop_pct=trailing_pct,
                high_watermark=high_watermark,
                low_watermark=low_watermark,
            )
            return

        if base_stop is not None:
            state.base_stop_loss = base_stop
        if fill.order.take_profit is not None:
            state.take_profit = fill.order.take_profit
        if trailing_pct is not None:
            state.trailing_stop_pct = trailing_pct
        if state.side == 1:
            state.high_watermark = max(state.high_watermark or fill.price, fill.price)
        else:
            state.low_watermark = min(state.low_watermark or fill.price, fill.price)
        if active_stop is not None:
            if state.active_stop_loss is None:
                state.active_stop_loss = active_stop
            elif state.side == 1:
                state.active_stop_loss = max(state.active_stop_loss, active_stop)
            else:
                state.active_stop_loss = min(state.active_stop_loss, active_stop)

    def _process_protective_exits(
        self, bar: Bar, strategies: List[StrategyBase]
    ) -> None:
        """Handle fixed and trailing exits before polling strategies.

        Conservative trailing semantics:
        - the active stop used for this bar comes from prior bars
        - the trailing watermark is updated only after this bar survives
        This avoids optimistic intrabar path assumptions from the same OHLC bar.
        """
        state = self._protective_exits.get(bar.symbol)
        qty = self._positions.get(bar.symbol, 0.0)
        if state is None or abs(qty) < 1e-10:
            return

        close_side = "SELL" if qty > 0 else "BUY"
        stop_price = state.active_stop_loss
        if stop_price is not None:
            triggered = (qty > 0 and bar.low <= stop_price) or (
                qty < 0 and bar.high >= stop_price
            )
            if triggered:
                fill_price = min(stop_price, bar.open) if qty > 0 else max(stop_price, bar.open)
                self._emit_protective_fill(
                    symbol=bar.symbol,
                    side=close_side,
                    quantity=abs(qty),
                    price=fill_price,
                    timestamp=bar.timestamp,
                    strategy=state.strategy,
                    reason="stop_loss",
                    strategies=strategies,
                )
                return

        tp_price = state.take_profit
        if tp_price is not None:
            triggered = (qty > 0 and bar.high >= tp_price) or (
                qty < 0 and bar.low <= tp_price
            )
            if triggered:
                fill_price = max(tp_price, bar.open) if qty > 0 else min(tp_price, bar.open)
                self._emit_protective_fill(
                    symbol=bar.symbol,
                    side=close_side,
                    quantity=abs(qty),
                    price=fill_price,
                    timestamp=bar.timestamp,
                    strategy=state.strategy,
                    reason="take_profit",
                    strategies=strategies,
                )
                return

        trailing_pct = state.trailing_stop_pct
        if trailing_pct is None or trailing_pct <= 0:
            return

        if qty > 0:
            state.high_watermark = max(state.high_watermark or bar.close, bar.high)
            candidate = state.high_watermark * (1.0 - trailing_pct)
            floor = state.base_stop_loss if state.base_stop_loss is not None else candidate
            state.active_stop_loss = max(
                x for x in [state.active_stop_loss, candidate, floor] if x is not None
            )
        else:
            state.low_watermark = min(state.low_watermark or bar.close, bar.low)
            candidate = state.low_watermark * (1.0 + trailing_pct)
            ceiling = state.base_stop_loss if state.base_stop_loss is not None else candidate
            state.active_stop_loss = min(
                x for x in [state.active_stop_loss, candidate, ceiling] if x is not None
            )

    def _emit_protective_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        timestamp: pd.Timestamp,
        strategy: str,
        reason: str,
        strategies: List[StrategyBase],
    ) -> None:
        """Create and apply a synthetic protective fill."""
        broker_cfg = self._config.brokers.get("default", {})
        commission = broker_cfg.get("commission_per_share", 0.005) * abs(quantity)
        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="STOP",
            timestamp=timestamp,
            strategy=strategy,
        )
        fill = Fill(
            order=order,
            price=round(price, 6),
            quantity=quantity,
            commission=round(commission, 4),
            slippage_bps=0.0,
            latency_ms=0.0,
            timestamp=timestamp,
            rejected=False,
            reason=reason,
        )
        self._on_fill(
            Event(timestamp=timestamp, type=EventType.FILL, data=fill),
            strategies,
        )
