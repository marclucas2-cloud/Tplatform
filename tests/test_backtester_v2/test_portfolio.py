"""Tests for PortfolioTracker — positions, equity, P&L, drawdown."""

import pytest
import pandas as pd

from core.backtester_v2.types import Bar, Event, EventType, Fill, Order
from core.backtester_v2.portfolio_tracker import PortfolioTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(s: str = "2025-06-15 10:00") -> pd.Timestamp:
    return pd.Timestamp(s, tz="US/Eastern")


def _bar(symbol: str = "AAPL", close: float = 150.0,
         high: float = 152.0, low: float = 148.0,
         ts: str = "2025-06-15 10:00") -> Bar:
    return Bar(symbol=symbol, timestamp=_ts(ts),
               open=close, high=high, low=low, close=close, volume=1000)


def _order(symbol: str = "AAPL", side: str = "BUY", qty: float = 10,
           strategy: str = "test_strat", sl: float = None,
           tp: float = None) -> Order:
    return Order(symbol=symbol, side=side, quantity=qty,
                 order_type="MARKET", timestamp=_ts(),
                 strategy=strategy, stop_loss=sl, take_profit=tp)


def _fill(order: Order, price: float = 150.0, commission: float = 0.05,
          ts: str = "2025-06-15 10:00") -> Fill:
    return Fill(order=order, price=price, quantity=order.quantity,
                commission=commission, slippage_bps=0.0, latency_ms=1.0,
                timestamp=_ts(ts))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_initial_state(self):
        pt = PortfolioTracker(initial_capital=50_000.0)
        assert pt.cash == 50_000.0
        assert pt.get_equity() == 50_000.0
        assert len(pt.positions) == 0
        assert len(pt.get_trade_log()) == 0
        assert len(pt.get_equity_curve()) == 0

    def test_initial_state_default(self):
        pt = PortfolioTracker()
        assert pt.cash == 100_000.0
        state = pt.get_state()
        assert state.equity == 100_000.0
        assert state.drawdown_pct == 0.0


class TestOpenPositions:
    def test_buy_position(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10)
        fill = _fill(order, price=150.0, commission=0.05)
        pt.apply_fill(fill)

        assert "AAPL" in pt.positions
        pos = pt.positions["AAPL"]
        assert pos.side == 1
        assert pos.qty == 10
        assert pos.entry_price == 150.0
        assert pt.cash == 100_000.0 - 0.05  # only commission deducted

    def test_sell_position(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="SELL", qty=5)
        fill = _fill(order, price=200.0, commission=0.025)
        pt.apply_fill(fill)

        pos = pt.positions["AAPL"]
        assert pos.side == -1
        assert pos.qty == 5
        assert pos.entry_price == 200.0

    def test_rejected_fill_ignored(self):
        pt = PortfolioTracker(100_000.0)
        order = _order()
        fill = Fill(order=order, price=150.0, quantity=10,
                    commission=0.0, slippage_bps=0.0, latency_ms=0.0,
                    timestamp=_ts(), rejected=True, reason="margin")
        pt.apply_fill(fill)
        assert len(pt.positions) == 0
        assert pt.cash == 100_000.0


class TestMarkToMarket:
    def test_mark_to_market(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10)
        fill = _fill(order, price=150.0, commission=0.05)
        pt.apply_fill(fill)

        bar = _bar(close=155.0, high=156.0, low=149.0)
        pt.mark_to_market(bar)

        pos = pt.positions["AAPL"]
        assert pos.unrealized_pnl == pytest.approx(50.0)  # (155-150)*10
        assert pt.get_equity() == pytest.approx(100_000.0 - 0.05 + 50.0)

    def test_mark_to_market_short(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="SELL", qty=5)
        fill = _fill(order, price=200.0, commission=0.025)
        pt.apply_fill(fill)

        bar = _bar(close=195.0, high=201.0, low=194.0)
        pt.mark_to_market(bar)

        pos = pt.positions["AAPL"]
        # Short: side=-1, (195-200)*5 * -1 = +25
        assert pos.unrealized_pnl == pytest.approx(25.0)

    def test_equity_curve_updated(self):
        pt = PortfolioTracker(100_000.0)
        bar = _bar(close=150.0)
        pt.mark_to_market(bar)
        assert len(pt.get_equity_curve()) == 1
        assert pt.get_equity_curve()[0]["equity"] == 100_000.0


class TestStopLoss:
    def test_stop_loss_triggered_long(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10, sl=145.0)
        fill = _fill(order, price=150.0)
        pt.apply_fill(fill)

        # Bar low hits SL
        bar = _bar(close=146.0, high=151.0, low=144.0)
        events = pt.check_stops(bar)

        assert len(events) == 1
        assert events[0].type == EventType.FILL
        assert events[0].data.price == 145.0
        assert events[0].data.reason == "SL"

    def test_stop_loss_triggered_short(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="SELL", qty=5, sl=210.0)
        fill = _fill(order, price=200.0)
        pt.apply_fill(fill)

        # Bar high hits SL for short
        bar = _bar(close=208.0, high=211.0, low=205.0)
        events = pt.check_stops(bar)

        assert len(events) == 1
        assert events[0].data.price == 210.0
        assert events[0].data.reason == "SL"

    def test_stop_loss_not_triggered(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10, sl=140.0)
        fill = _fill(order, price=150.0)
        pt.apply_fill(fill)

        bar = _bar(close=148.0, high=151.0, low=147.0)
        events = pt.check_stops(bar)
        assert len(events) == 0


class TestTakeProfit:
    def test_take_profit_triggered_long(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10, tp=160.0)
        fill = _fill(order, price=150.0)
        pt.apply_fill(fill)

        bar = _bar(close=159.0, high=161.0, low=155.0)
        events = pt.check_stops(bar)

        assert len(events) == 1
        assert events[0].data.price == 160.0
        assert events[0].data.reason == "TP"

    def test_take_profit_triggered_short(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="SELL", qty=5, tp=190.0)
        fill = _fill(order, price=200.0)
        pt.apply_fill(fill)

        # Bar low hits TP for short
        bar = _bar(close=191.0, high=195.0, low=189.0)
        events = pt.check_stops(bar)

        assert len(events) == 1
        assert events[0].data.price == 190.0
        assert events[0].data.reason == "TP"

    def test_sl_takes_priority_over_tp(self):
        """When both SL and TP trigger on the same bar, SL wins."""
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=10, sl=145.0, tp=160.0)
        fill = _fill(order, price=150.0)
        pt.apply_fill(fill)

        # Both triggered: low < SL and high > TP
        bar = _bar(close=155.0, high=162.0, low=143.0)
        events = pt.check_stops(bar)

        assert len(events) == 1
        assert events[0].data.reason == "SL"


class TestEquityAndDrawdown:
    def test_equity_calculation(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=100)
        fill = _fill(order, price=100.0, commission=0.50)
        pt.apply_fill(fill)

        bar = _bar(close=105.0, high=106.0, low=99.0)
        pt.mark_to_market(bar)

        expected = 100_000.0 - 0.50 + (105.0 - 100.0) * 100
        assert pt.get_equity() == pytest.approx(expected)

    def test_drawdown_tracking(self):
        pt = PortfolioTracker(100_000.0)
        order = _order(side="BUY", qty=100)
        fill = _fill(order, price=100.0, commission=0.0)
        pt.apply_fill(fill)

        # Price goes up -> peak rises
        bar1 = _bar(close=110.0, high=111.0, low=99.0, ts="2025-06-15 10:00")
        pt.mark_to_market(bar1)
        peak_after_up = pt.peak_equity
        assert peak_after_up > 100_000.0

        # Price drops -> drawdown
        bar2 = _bar(close=95.0, high=110.0, low=94.0, ts="2025-06-15 11:00")
        pt.mark_to_market(bar2)

        state = pt.get_state()
        assert state.drawdown_pct > 0.0
        assert pt.peak_equity == peak_after_up  # peak not updated


class TestClosePositionPnl:
    def test_close_position_pnl(self):
        pt = PortfolioTracker(100_000.0)

        # Open long
        buy_order = _order(side="BUY", qty=10)
        buy_fill = _fill(buy_order, price=150.0, commission=0.05)
        pt.apply_fill(buy_fill)
        assert "AAPL" in pt.positions

        # Close long
        sell_order = _order(side="SELL", qty=10)
        sell_fill = _fill(sell_order, price=160.0, commission=0.05,
                          ts="2025-06-15 11:00")
        pt.apply_fill(sell_fill)

        assert "AAPL" not in pt.positions
        trades = pt.get_trade_log()
        assert len(trades) == 1
        assert trades[0]["pnl"] == pytest.approx(100.0)  # (160-150)*10
        assert trades[0]["side"] == "LONG"
        # Cash = initial - commission_buy + pnl - commission_sell
        assert pt.cash == pytest.approx(100_000.0 - 0.05 + 100.0 - 0.05)


class TestMultiplePositions:
    def test_multiple_positions(self):
        pt = PortfolioTracker(100_000.0)

        # Open AAPL long
        fill1 = _fill(_order("AAPL", "BUY", 10), price=150.0, commission=0.05)
        pt.apply_fill(fill1)

        # Open MSFT short
        msft_order = _order("MSFT", "SELL", 5, strategy="short_strat")
        fill2 = _fill(msft_order, price=300.0, commission=0.025)
        pt.apply_fill(fill2)

        assert len(pt.positions) == 2
        assert pt.positions["AAPL"].side == 1
        assert pt.positions["MSFT"].side == -1

        # Mark AAPL
        pt.mark_to_market(_bar("AAPL", close=155.0, high=156.0, low=149.0))
        # Mark MSFT
        pt.mark_to_market(_bar("MSFT", close=295.0, high=301.0, low=294.0))

        state = pt.get_state()
        assert state.exposure_long > 0
        assert state.exposure_short > 0
        assert state.equity == pytest.approx(
            100_000.0 - 0.05 - 0.025 + 50.0 + 25.0  # AAPL +50, MSFT +25
        )

    def test_no_position_no_stops(self):
        pt = PortfolioTracker(100_000.0)
        bar = _bar(close=150.0)
        events = pt.check_stops(bar)
        assert events == []
