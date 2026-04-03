"""PortfolioTracker — Track positions, equity, P&L, drawdown during backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from core.backtester_v2.types import (
    Bar,
    Event,
    EventType,
    Fill,
    Order,
    PortfolioState,
)


@dataclass
class Position:
    """Internal position record.

    Attributes:
        symbol: Instrument ticker.
        side: +1 for long, -1 for short.
        qty: Unsigned quantity held.
        entry_price: Average fill price.
        entry_time: Timestamp of opening fill.
        strategy: Strategy name that generated the signal.
        sl: Stop-loss price, or None.
        tp: Take-profit price, or None.
        unrealized_pnl: Current mark-to-market P&L.
    """

    symbol: str
    side: int  # +1 long, -1 short
    qty: float
    entry_price: float
    entry_time: pd.Timestamp
    strategy: str = ""
    sl: float | None = None
    tp: float | None = None
    unrealized_pnl: float = 0.0


class PortfolioTracker:
    """Track positions, equity, P&L and drawdown throughout a backtest.

    Args:
        initial_capital: Starting cash balance.
    """

    def __init__(self, initial_capital: float = 100_000.0) -> None:
        self.initial_capital: float = initial_capital
        self.cash: float = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Dict[str, Any]] = []
        self.trade_log: List[Dict[str, Any]] = []
        self.peak_equity: float = initial_capital
        self._last_prices: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def mark_to_market(self, bar: Bar) -> None:
        """Update unrealized P&L for the position matching *bar.symbol*.

        Also records a point on the equity curve and updates peak/drawdown.

        Args:
            bar: Closed OHLCV bar used for marking.
        """
        self._last_prices[bar.symbol] = bar.close

        if bar.symbol in self.positions:
            pos = self.positions[bar.symbol]
            pos.unrealized_pnl = (
                pos.side * (bar.close - pos.entry_price) * pos.qty
            )

        equity = self.get_equity()
        self.peak_equity = max(self.peak_equity, equity)
        self.equity_curve.append(
            {"timestamp": bar.timestamp, "equity": equity}
        )

    def apply_fill(self, fill: Fill) -> None:
        """Process a fill event, opening or closing a position.

        Args:
            fill: Execution report from the simulator.
        """
        if fill.rejected:
            return

        symbol = fill.order.symbol
        side_int = 1 if fill.order.side == "BUY" else -1

        # Deduct commission from cash
        self.cash -= fill.commission

        if symbol in self.positions:
            pos = self.positions[symbol]
            # Closing trade (opposite side)
            if pos.side != side_int:
                realized_pnl = pos.side * (fill.price - pos.entry_price) * fill.quantity
                self.cash += realized_pnl
                self._log_trade(pos, fill, realized_pnl)
                remaining = pos.qty - fill.quantity
                if remaining <= 1e-9:
                    del self.positions[symbol]
                else:
                    pos.qty = remaining
                    pos.unrealized_pnl = (
                        pos.side * (self._last_prices.get(symbol, fill.price) - pos.entry_price) * pos.qty
                    )
            else:
                # Adding to existing position (same side)
                total_qty = pos.qty + fill.quantity
                pos.entry_price = (
                    (pos.entry_price * pos.qty + fill.price * fill.quantity) / total_qty
                )
                pos.qty = total_qty
        else:
            # Open new position
            self.positions[symbol] = Position(
                symbol=symbol,
                side=side_int,
                qty=fill.quantity,
                entry_price=fill.price,
                entry_time=fill.timestamp,
                strategy=fill.order.strategy,
                sl=fill.order.stop_loss,
                tp=fill.order.take_profit,
            )

    def check_stops(self, bar: Bar) -> List[Event]:
        """Check if SL/TP are hit for positions on *bar.symbol*.

        Uses bar high/low to detect stop triggers within the candle.

        Args:
            bar: Current bar to check against.

        Returns:
            List of FILL events for triggered stops.
        """
        events: List[Event] = []
        pos = self.positions.get(bar.symbol)
        if pos is None:
            return events

        close_side = "SELL" if pos.side == 1 else "BUY"

        # Stop-loss check
        if pos.sl is not None:
            triggered = (pos.side == 1 and bar.low <= pos.sl) or (
                pos.side == -1 and bar.high >= pos.sl
            )
            if triggered:
                events.append(self._make_stop_fill(pos, bar, pos.sl, close_side, "SL"))
                return events  # SL takes priority, skip TP

        # Take-profit check
        if pos.tp is not None:
            triggered = (pos.side == 1 and bar.high >= pos.tp) or (
                pos.side == -1 and bar.low <= pos.tp
            )
            if triggered:
                events.append(self._make_stop_fill(pos, bar, pos.tp, close_side, "TP"))

        return events

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def get_state(self) -> PortfolioState:
        """Return current portfolio snapshot.

        Returns:
            PortfolioState with equity, cash, positions, exposures, drawdown.
        """
        equity = self.get_equity()
        positions_map = {s: p.side * p.qty for s, p in self.positions.items()}
        long_exp = sum(
            p.qty * self._last_prices.get(p.symbol, p.entry_price)
            for p in self.positions.values() if p.side == 1
        )
        short_exp = sum(
            p.qty * self._last_prices.get(p.symbol, p.entry_price)
            for p in self.positions.values() if p.side == -1
        )
        dd = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0.0

        return PortfolioState(
            equity=equity,
            cash=self.cash,
            positions=positions_map,
            exposure_long=long_exp,
            exposure_short=short_exp,
            drawdown_pct=dd,
        )

    def get_equity(self) -> float:
        """Return total equity (cash + unrealized P&L).

        Returns:
            Current portfolio value.
        """
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        return self.cash + unrealized

    def get_equity_curve(self) -> List[Dict[str, Any]]:
        """Return the full equity curve recorded so far.

        Returns:
            List of {timestamp, equity} dicts.
        """
        return list(self.equity_curve)

    def get_trade_log(self) -> List[Dict[str, Any]]:
        """Return the full trade log.

        Returns:
            List of trade dicts with entry/exit info and P&L.
        """
        return list(self.trade_log)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _log_trade(self, pos: Position, fill: Fill, realized_pnl: float) -> None:
        """Append a completed trade to the log."""
        self.trade_log.append({
            "symbol": pos.symbol,
            "side": "LONG" if pos.side == 1 else "SHORT",
            "qty": fill.quantity,
            "entry_price": pos.entry_price,
            "exit_price": fill.price,
            "entry_time": pos.entry_time,
            "exit_time": fill.timestamp,
            "strategy": pos.strategy,
            "pnl": realized_pnl,
            "commission": fill.commission,
        })

    def _make_stop_fill(
        self, pos: Position, bar: Bar, price: float, side: str, reason: str,
    ) -> Event:
        """Create a FILL event for a triggered stop."""
        order = Order(
            symbol=pos.symbol,
            side=side,
            quantity=pos.qty,
            order_type="STOP",
            timestamp=bar.timestamp,
            strategy=pos.strategy,
        )
        fill = Fill(
            order=order,
            price=price,
            quantity=pos.qty,
            commission=0.0,
            slippage_bps=0.0,
            latency_ms=0.0,
            timestamp=bar.timestamp,
            reason=reason,
        )
        return Event(timestamp=bar.timestamp, type=EventType.FILL, data=fill)
