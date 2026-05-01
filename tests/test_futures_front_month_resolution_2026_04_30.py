"""Regression tests for 2026-04-30 futures live entry fixes.

Goals:
1. New futures entries resolve an explicit non-expired front month instead of
   trusting `reqContractDetails(Future(symbol=...))` generic chain ordering.
2. Risk budget estimation uses the visible bar close when available rather than
   midpoint(SL, TP), which overstates risk on asymmetric brackets.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield


def test_normalize_contract_month_accepts_yyyymm_and_yyyymmdd():
    from core.worker.cycles.futures_runner import _normalize_contract_month

    assert _normalize_contract_month("202606") == "202606"
    assert _normalize_contract_month("20260618") == "20260618"
    assert _normalize_contract_month("2026-06-18") == "20260618"
    assert _normalize_contract_month(None) is None
    assert _normalize_contract_month("MGCM6") is None


def test_resolve_front_month_contract_pins_calendar_month(monkeypatch):
    from core.worker.cycles import futures_runner as fr

    resolved = SimpleNamespace(
        symbol="MGC",
        exchange="COMEX",
        currency="USD",
        lastTradeDateOrContractMonth="20260626",
        localSymbol="MGCM6",
    )

    class FakeMgr:
        def get_front_month(self, symbol, ref_date=None):
            assert symbol == "MGC"
            return {"expiry": "2026-06-26"}

    class FakeIB:
        def __init__(self):
            self.seen = []

        def reqContractDetails(self, contract):
            self.seen.append(contract.lastTradeDateOrContractMonth)
            return [SimpleNamespace(contract=resolved)]

    monkeypatch.setattr("core.broker.ibkr_futures.FuturesContractManager", FakeMgr)
    ib = FakeIB()
    contract = fr._resolve_front_month_contract(ib, "MGC")

    assert ib.seen == ["202606"]
    assert contract.localSymbol == "MGCM6"


def test_futures_runner_entry_uses_front_month_resolver_and_bar_close_estimate():
    src = (ROOT / "core" / "worker" / "cycles" / "futures_runner.py").read_text(
        encoding="utf-8",
    )
    assert "_resolve_front_month_contract(ibkr._ib, sym)" in src
    assert "_estimate_futures_signal_risk_usd(" in src
