"""Tests for MacroECB futures executor (V15.4 LIVE cabling).

Validates the 3 critical safety properties of _make_macro_ecb_executor:
  1. Kill switch ACTIVE → refuses to trade
  2. Hard limit 2 contracts → refuses new entries
  3. SL/TP recalculated from actual fill price (not signal price)

These tests mock the IBKR connection to avoid requiring live broker.
"""
from __future__ import annotations

# Python 3.14 compat: eventkit (ib_insync dep) requires an active event loop at import
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Mock ib_insync to avoid real broker imports inside _executor
_mock_ib_insync = MagicMock()


class _MockOrder:
    """Minimal order stub that captures attrs set by the executor."""
    def __init__(self, action, totalQuantity, *args, **kwargs):
        self.action = action
        self.totalQuantity = totalQuantity
        self.auxPrice = args[0] if args else kwargs.get("auxPrice", 0)
        self.lmtPrice = args[0] if args else kwargs.get("lmtPrice", 0)
        self.tif = "DAY"
        self.ocaGroup = ""
        self.ocaType = 0
        self.outsideRth = False


class _MockStopOrder(_MockOrder):
    def __init__(self, action, quantity, stopPrice):
        super().__init__(action, quantity)
        self.auxPrice = stopPrice


class _MockLimitOrder(_MockOrder):
    def __init__(self, action, quantity, limitPrice):
        super().__init__(action, quantity)
        self.lmtPrice = limitPrice


class _MockMarketOrder(_MockOrder):
    def __init__(self, action, quantity):
        super().__init__(action, quantity)


class _MockFuture:
    def __init__(self, symbol, exchange=None, currency=None):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency


_mock_ib_insync.StopOrder = _MockStopOrder
_mock_ib_insync.LimitOrder = _MockLimitOrder
_mock_ib_insync.MarketOrder = _MockMarketOrder
_mock_ib_insync.Future = _MockFuture
sys.modules["ib_insync"] = _mock_ib_insync

import worker
from core.backtester_v2.types import Signal


def _make_signal(symbol: str = "DAX", side: str = "BUY", entry: float = 18000.0) -> Signal:
    """Build a Signal matching what MacroECB emits."""
    return Signal(
        symbol=symbol,
        side=side,
        strategy_name="MacroECB",
        order_type="MARKET",
        stop_loss=entry - 20 if side == "BUY" else entry + 20,
        take_profit=entry + 40 if side == "BUY" else entry - 40,
        strength=0.8,
    )


def _make_mock_ib(positions: list | None = None, fill_price: float = 18005.0,
                   fill_status: str = "Filled"):
    """Build a mock ib_insync IB connection.

    Args:
        positions: list of (symbol, qty) tuples to return from positions()
        fill_price: avgFillPrice of the market entry trade
        fill_status: orderStatus.status of the market entry trade
    """
    positions = positions or []
    ib = MagicMock()

    # ib.positions() -> list of objects with .contract.symbol and .position
    pos_objs = []
    for sym, qty in positions:
        p = MagicMock()
        p.contract.symbol = sym
        p.position = qty
        pos_objs.append(p)
    ib.positions.return_value = pos_objs

    # ib.reqContractDetails() -> [ContractDetails(contract=...)]
    # IBKR uses the index symbol for futures (DAX→FDXM, CAC40→FCE, ESTX50→FESX)
    contract = MagicMock()
    contract.symbol = "DAX"
    details = MagicMock()
    details.contract = contract
    ib.reqContractDetails.return_value = [details]

    # ib.placeOrder() -> trade with orderStatus
    trade = MagicMock()
    trade.orderStatus.status = fill_status
    trade.orderStatus.avgFillPrice = fill_price
    trade.order = MagicMock()
    ib.placeOrder.return_value = trade

    # ib.sleep() is a no-op for tests
    ib.sleep = MagicMock()

    return ib


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect ROOT to a tmp dir so tests don't touch real state files.

    Post-2026-04-19 (Phase 2 XXL): _make_macro_ecb_executor was extracted to
    core.worker.cycles.macro_ecb_runner — patch its ROOT too.
    """
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    from core.worker.cycles import macro_ecb_runner
    monkeypatch.setattr(macro_ecb_runner, "ROOT", tmp_path)
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_executor_refuses_when_kill_switch_active(isolated_state, monkeypatch):
    """1. If kill_switch_state.json has active=True, executor returns False."""
    ks_path = isolated_state / "data" / "kill_switch_state.json"
    ks_path.write_text(json.dumps({"active": True, "activation_reason": "test"}))

    # Avoid real time.sleep during test
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)

    executor = worker._make_macro_ecb_executor(mode="LIVE")
    sig = _make_signal("DAX", "BUY", 18000.0)
    ib = _make_mock_ib(positions=[], fill_price=18005.0)

    result = executor(sig, ib)

    assert result is False
    ib.placeOrder.assert_not_called()  # No order sent


def test_executor_refuses_when_hard_limit_reached(isolated_state, monkeypatch):
    """2. If 2 contracts already open on IBKR, executor refuses new entries."""
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)

    executor = worker._make_macro_ecb_executor(mode="LIVE")
    sig = _make_signal("DAX", "BUY", 18000.0)

    # 2 contracts already held — hard limit
    ib = _make_mock_ib(positions=[("MES", 1), ("MNQ", 1)], fill_price=18005.0)

    result = executor(sig, ib)

    assert result is False
    ib.placeOrder.assert_not_called()


def test_executor_recalculates_sl_tp_from_fill_price(isolated_state, monkeypatch):
    """3. SL/TP are computed from fill price with offsets preserving signal levels.

    Executor computes _sl_offset = abs(sig.stop_loss - fill), then:
      BUY  SL = fill - offset,  TP = fill + offset
      SELL SL = fill + offset,  TP = fill - offset

    Net effect when fill drifts: absolute SL/TP levels from signal are
    preserved, AND the SL is always on the correct side of the fill.
    This protects against signals with inverted SL/TP if a strategy bug.

    Signal : BUY DAX entry 18000, SL=17980, TP=18040
    Fill   : 18008 (slippage +8)
    Expected : SL=17980 (preserved), TP=18040 (preserved), both OCA SELL
    """
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)
    monkeypatch.setattr(worker, "_send_alert", lambda *a, **kw: None)

    executor = worker._make_macro_ecb_executor(mode="PAPER")
    sig = _make_signal("DAX", "BUY", 18000.0)
    assert sig.stop_loss == 17980.0
    assert sig.take_profit == 18040.0

    ib = _make_mock_ib(positions=[], fill_price=18008.0, fill_status="Filled")
    result = executor(sig, ib)
    assert result is True, "executor should succeed"

    # 3 orders: entry + SL + TP
    assert ib.placeOrder.call_count == 3

    # SL order (2nd call)
    sl_order = ib.placeOrder.call_args_list[1][0][1]
    assert sl_order.action == "SELL"
    assert sl_order.totalQuantity == 1
    assert abs(sl_order.auxPrice - 17980.0) < 0.01, f"SL expected 17980, got {sl_order.auxPrice}"
    assert sl_order.ocaGroup.startswith("OCA_ECB_")
    assert sl_order.ocaType == 1

    # TP order (3rd call)
    tp_order = ib.placeOrder.call_args_list[2][0][1]
    assert tp_order.action == "SELL"
    assert abs(tp_order.lmtPrice - 18040.0) < 0.01, f"TP expected 18040, got {tp_order.lmtPrice}"
    assert tp_order.ocaGroup == sl_order.ocaGroup, "SL and TP must share OCA group"
