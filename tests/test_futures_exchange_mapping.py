"""Tests P0 fix 2026-04-23: _make_future_contract exchange mapping correct.

Bug : futures_runner.py hardcodait `exchange="CME"` pour tous symboles.
MCL doit etre NYMEX, MGC doit etre COMEX. Sinon IBKR renvoie
"No security definition has been found" (observe 9x depuis 20/04/2026).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """ib_insync Future() requires an asyncio event loop in thread.
    Tests fresh-thread n'en ont pas par defaut (Python 3.14+).
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield


class TestFuturesExchangeMap:
    """Teste le dict _FUTURES_EXCHANGE_MAP directement (sans Future())."""

    def test_map_mcl_nymex(self):
        from core.worker.cycles.futures_runner import _FUTURES_EXCHANGE_MAP
        assert _FUTURES_EXCHANGE_MAP["MCL"] == "NYMEX"

    def test_map_mgc_comex(self):
        from core.worker.cycles.futures_runner import _FUTURES_EXCHANGE_MAP
        assert _FUTURES_EXCHANGE_MAP["MGC"] == "COMEX"

    def test_map_mes_mnq_m2k_cme(self):
        from core.worker.cycles.futures_runner import _FUTURES_EXCHANGE_MAP
        for sym in ("MES", "MNQ", "M2K", "ES", "NQ"):
            assert _FUTURES_EXCHANGE_MAP[sym] == "CME", f"{sym} should be CME"

    def test_map_cl_gc_full_contracts(self):
        from core.worker.cycles.futures_runner import _FUTURES_EXCHANGE_MAP
        assert _FUTURES_EXCHANGE_MAP["CL"] == "NYMEX"
        assert _FUTURES_EXCHANGE_MAP["GC"] == "COMEX"

    def test_map_vix_cfe(self):
        from core.worker.cycles.futures_runner import _FUTURES_EXCHANGE_MAP
        assert _FUTURES_EXCHANGE_MAP["VIX"] == "CFE"


class TestMakeFutureContract:
    """Teste la factory _make_future_contract (cree Future avec bon exchange)."""

    def test_mcl_is_nymex(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        fut = _make_future_contract("MCL")
        assert fut.symbol == "MCL"
        assert fut.exchange == "NYMEX"
        assert fut.currency == "USD"

    def test_mgc_is_comex(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        assert _make_future_contract("MGC").exchange == "COMEX"

    def test_mes_is_cme(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        assert _make_future_contract("MES").exchange == "CME"

    def test_mib_estx50_eur_currency_auto(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        assert _make_future_contract("MIB").currency == "EUR"
        assert _make_future_contract("ESTX50").currency == "EUR"

    def test_unknown_symbol_fallback_cme(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        assert _make_future_contract("UNKNOWN").exchange == "CME"

    def test_lowercase_symbol_normalized(self):
        from core.worker.cycles.futures_runner import _make_future_contract
        assert _make_future_contract("mcl").exchange == "NYMEX"


class TestNoHardcodedCMEForMCL:
    """Non-regression: plus d'occurrence de IbFuture(sym, exchange=\"CME\")
    pour les usages de contrats dans futures_runner.
    """

    def test_runner_no_hardcoded_cme_mcl_pattern(self):
        src = (ROOT_PATH / "core" / "worker" / "cycles" / "futures_runner.py").read_text(
            encoding="utf-8",
        )
        # Pattern de bug a exclure completement
        forbidden = 'IbFuture(sym, exchange="CME")'
        assert forbidden not in src, (
            f"Legacy pattern {forbidden} encore present. "
            f"Utiliser _make_future_contract(sym) qui route par symbol."
        )
        forbidden2 = 'IbFuture(pos_sym, exchange="CME")'
        assert forbidden2 not in src, (
            f"Legacy pattern {forbidden2} encore present. "
            f"Utiliser _make_future_contract(pos_sym) qui route par symbol."
        )
