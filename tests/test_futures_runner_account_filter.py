"""Tests pour _filter_positions_by_account + _get_canonical_ibkr_account.

Regression bug 2026-04-20: position MES -1 sur compte paper DUP573894
bloquait 7 paper strats via 'IBKR real position exists'. Fix filtre les
positions hors du compte canonique live.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.worker.cycles.futures_runner import (
    _filter_positions_by_account,
    _get_canonical_ibkr_account,
)


def _mk_position(account: str, symbol: str, qty: float):
    """Build a minimal Position-like namespace matching ib_insync shape."""
    return SimpleNamespace(
        account=account,
        position=qty,
        contract=SimpleNamespace(symbol=symbol),
    )


class TestFilterPositionsByAccount:

    def test_filter_excludes_other_accounts(self):
        """Positions hors canonical_account doivent etre exclues."""
        positions = [
            _mk_position("U25023333", "MCL", 1),
            _mk_position("DUP573894", "MES", -1),  # bloquait 7 paper strats
            _mk_position("U25023333", "MGC", 2),
        ]
        result = _filter_positions_by_account(positions, "U25023333")
        assert result == {"MCL": 1, "MGC": 2}
        assert "MES" not in result

    def test_zero_positions_excluded(self):
        """abs(position) == 0 -> exclude."""
        positions = [
            _mk_position("U25023333", "MCL", 0),
            _mk_position("U25023333", "MGC", 1),
        ]
        result = _filter_positions_by_account(positions, "U25023333")
        assert result == {"MGC": 1}

    def test_none_canonical_falls_back_to_historical(self):
        """Si canonical_account is None -> pas de filtre, garder historique."""
        positions = [
            _mk_position("U25023333", "MCL", 1),
            _mk_position("DUP573894", "MES", -1),
        ]
        result = _filter_positions_by_account(positions, None)
        # Sans filtre, les 2 sont la
        assert result == {"MCL": 1, "MES": -1}

    def test_empty_positions(self):
        assert _filter_positions_by_account([], "U25023333") == {}
        assert _filter_positions_by_account([], None) == {}


class TestGetCanonicalIbkrAccount:

    def test_env_override_wins(self, monkeypatch):
        """IBKR_LIVE_ACCOUNT env override tout."""
        monkeypatch.setenv("IBKR_LIVE_ACCOUNT", "U25023333")
        ib = SimpleNamespace(managedAccounts=lambda: ["DUP573894"])
        assert _get_canonical_ibkr_account(ib) == "U25023333"

    def test_fallback_to_managed_accounts_first(self, monkeypatch):
        """Sans env, utilise managedAccounts()[0]."""
        monkeypatch.delenv("IBKR_LIVE_ACCOUNT", raising=False)
        ib = SimpleNamespace(managedAccounts=lambda: ["U25023333", "DUP"])
        assert _get_canonical_ibkr_account(ib) == "U25023333"

    def test_returns_none_if_no_managed_accounts(self, monkeypatch):
        """Aucun env, aucun managedAccounts -> None (caller garde historique)."""
        monkeypatch.delenv("IBKR_LIVE_ACCOUNT", raising=False)
        ib = SimpleNamespace(managedAccounts=lambda: [])
        assert _get_canonical_ibkr_account(ib) is None

    def test_handles_managedaccounts_exception(self, monkeypatch):
        """Exception dans managedAccounts() -> None, pas de crash."""
        monkeypatch.delenv("IBKR_LIVE_ACCOUNT", raising=False)
        def _broken():
            raise RuntimeError("broker offline")
        ib = SimpleNamespace(managedAccounts=_broken)
        assert _get_canonical_ibkr_account(ib) is None

    def test_handles_no_managedaccounts_method(self, monkeypatch):
        """Objet sans methode managedAccounts -> None."""
        monkeypatch.delenv("IBKR_LIVE_ACCOUNT", raising=False)
        ib = SimpleNamespace()  # no managedAccounts attr
        assert _get_canonical_ibkr_account(ib) is None
