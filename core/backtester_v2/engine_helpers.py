"""Helper functions for BacktesterV2 engine.

Extracted to keep engine.py under 200 lines. These are stateless
functions that operate on the engine's internal state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from core.backtester_v2.types import Bar, Event, EventType, PortfolioState

if TYPE_CHECKING:
    from core.backtester_v2.engine import BacktesterV2
    from core.backtester_v2.event_queue import EventQueue
    from core.backtester_v2.types import BacktestConfig


def load_market_events(
    queue: EventQueue,
    config: BacktestConfig,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    """Create MARKET_DATA events from all data sources.

    Args:
        queue: Event queue to populate.
        config: Backtest config with data_sources.
        start: Period start.
        end: Period end.
    """
    for symbol, df in config.data_sources.items():
        mask = (df.index >= start) & (df.index <= end)
        for ts, row in df.loc[mask].iterrows():
            bar = Bar(
                symbol=symbol,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            queue.push(Event(
                timestamp=ts, type=EventType.MARKET_DATA, data=bar,
            ))


def schedule_periodic_events(
    queue: EventQueue,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    """Schedule EOD, INTEREST, and SWAP events for each business day.

    Args:
        queue: Event queue to populate.
        start: Period start.
        end: Period end.
    """
    dates = pd.bdate_range(start.normalize(), end.normalize())
    for date in dates:
        eod_ts = date.replace(hour=16, minute=0)
        if start <= eod_ts <= end:
            for etype in (EventType.EOD, EventType.BORROW_INTEREST, EventType.SWAP):
                queue.push(Event(timestamp=eod_ts, type=etype, data=None))


def get_equity(engine: BacktesterV2) -> float:
    """Compute current equity = cash + market value of positions.

    Args:
        engine: BacktesterV2 instance.

    Returns:
        Current portfolio equity.
    """
    equity = engine._cash
    for symbol, qty in engine._positions.items():
        bar = engine._feed.get_latest_bar(symbol)
        if bar:
            equity += qty * bar.close
    return equity


def get_drawdown(engine: BacktesterV2) -> float:
    """Compute current drawdown from peak equity.

    Args:
        engine: BacktesterV2 instance.

    Returns:
        Drawdown as a fraction (0.0 to 1.0).
    """
    equity = get_equity(engine)
    engine._peak_equity = max(engine._peak_equity, equity)
    if engine._peak_equity == 0:
        return 0.0
    return (engine._peak_equity - equity) / engine._peak_equity


def get_exposure(engine: BacktesterV2) -> float:
    """Compute total gross exposure.

    Args:
        engine: BacktesterV2 instance.

    Returns:
        Total absolute notional exposure.
    """
    exposure = 0.0
    for symbol, qty in engine._positions.items():
        bar = engine._feed.get_latest_bar(symbol)
        if bar:
            exposure += abs(qty) * bar.close
    return exposure


def get_portfolio_state(engine: BacktesterV2) -> PortfolioState:
    """Build current portfolio state snapshot.

    Args:
        engine: BacktesterV2 instance.

    Returns:
        PortfolioState with all current metrics.
    """
    equity = get_equity(engine)
    long_exp = 0.0
    short_exp = 0.0
    for symbol, qty in engine._positions.items():
        bar = engine._feed.get_latest_bar(symbol)
        if bar:
            val = qty * bar.close
            if val > 0:
                long_exp += val
            else:
                short_exp += abs(val)
    return PortfolioState(
        equity=equity,
        cash=engine._cash,
        positions=dict(engine._positions),
        exposure_long=long_exp,
        exposure_short=short_exp,
        drawdown_pct=get_drawdown(engine),
        margin_used=0.0,
    )


def record_equity(engine: BacktesterV2, timestamp: pd.Timestamp) -> None:
    """Record equity curve point.

    Args:
        engine: BacktesterV2 instance.
        timestamp: Current simulation timestamp.
    """
    engine._results.equity_curve.append({
        "timestamp": str(timestamp),
        "equity": round(get_equity(engine), 2),
    })


def close_all_positions(engine: BacktesterV2) -> None:
    """Close remaining positions at last known price.

    Args:
        engine: BacktesterV2 instance.
    """
    for symbol in list(engine._positions.keys()):
        bar = engine._feed.get_latest_bar(symbol)
        if bar is None:
            continue
        qty = engine._positions[symbol]
        entry = engine._avg_costs.get(symbol, bar.close)
        pnl = (bar.close - entry) * qty
        engine._results.trades.append({
            "symbol": symbol,
            "side": "CLOSE",
            "position_side": "LONG" if qty > 0 else "SHORT",
            "entry_price": entry, "exit_price": bar.close,
            "quantity": abs(qty), "pnl": round(pnl, 4),
            "commission": 0.0, "strategy": "close_all",
            "timestamp": str(engine._feed.timestamp),
            "exit_reason": "end_of_data",
        })
        engine._cash += qty * bar.close
    engine._positions.clear()
    engine._avg_costs.clear()
    engine._protective_exits.clear()
