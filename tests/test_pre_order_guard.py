"""Tests pre_order_guard — Phase 2.1 enforcement."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.governance.pre_order_guard import pre_order_guard, GuardError


def test_unknown_book_rejected():
    with pytest.raises(GuardError) as exc:
        pre_order_guard(book="nonexistent_book", strategy_id="anything")
    assert "unknown" in str(exc.value).lower()


def test_disabled_book_rejected():
    """ibkr_fx is disabled (ESMA) -> any order rejected."""
    with pytest.raises(GuardError) as exc:
        pre_order_guard(
            book="ibkr_fx", strategy_id="fx_carry_momentum_filter",
            paper_mode=False,
        )
    assert "disabled" in str(exc.value).lower()


def test_paper_only_book_rejected_in_live_mode():
    """ibkr_eu is paper_only -> live order rejected."""
    with pytest.raises(GuardError) as exc:
        pre_order_guard(
            book="ibkr_eu", strategy_id="eu_gap_open",
            paper_mode=False,
        )
    assert "live" in str(exc.value).lower() or "paper_only" in str(exc.value).lower()


def test_paper_only_book_allowed_in_paper_mode():
    """ibkr_eu paper_only -> paper order allowed."""
    # Should not raise
    pre_order_guard(
        book="ibkr_eu", strategy_id="eu_gap_open",
        paper_mode=True,
    )


def test_live_book_strategy_not_whitelisted_rejected():
    """binance_crypto live_allowed but unknown strategy -> rejected."""
    with pytest.raises(GuardError) as exc:
        pre_order_guard(
            book="binance_crypto", strategy_id="fake_strategy_xyz",
            paper_mode=False,
        )
    # rejected via whitelist check
    assert "non autorisee" in str(exc.value).lower() or "whitelist" in str(exc.value).lower()


def test_live_book_live_strategy_allowed():
    """binance_crypto + btc_eth_dual_momentum (live_core) -> allowed."""
    # Should not raise
    pre_order_guard(
        book="binance_crypto", strategy_id="btc_eth_dual_momentum",
        paper_mode=False,
    )


def test_paper_only_strategy_in_live_mode_rejected():
    """gold_trend_mgc demote paper_only -> live order rejected."""
    with pytest.raises(GuardError):
        pre_order_guard(
            book="ibkr_futures", strategy_id="gold_trend_mgc",
            paper_mode=False,
        )


def test_disabled_strategy_in_live_mode_rejected():
    """btc_dominance_v2 disabled -> rejected even in live_allowed book."""
    with pytest.raises(GuardError):
        pre_order_guard(
            book="binance_crypto", strategy_id="btc_dominance_rotation_v2",
            paper_mode=False,
        )


def test_empty_book_rejected():
    with pytest.raises(GuardError):
        pre_order_guard(book="", strategy_id="x")


def test_empty_strategy_rejected():
    with pytest.raises(GuardError):
        pre_order_guard(book="binance_crypto", strategy_id="")


def test_bypass_for_test_only_in_pytest():
    """_bypass_for_test=True doit etre OK en pytest, raise sinon."""
    # In pytest env -> OK
    pre_order_guard(book="x", strategy_id="y", _bypass_for_test=True)


def test_books_registry_load_fail_closed(tmp_path, monkeypatch):
    """Si books_registry.yaml introuvable, raise GuardError (fail-closed)."""
    from core.governance import pre_order_guard as guard_mod
    monkeypatch.setattr(guard_mod, "BOOKS_REGISTRY_PATH",
                        tmp_path / "nonexistent.yaml")
    # Reset cache to force reload
    monkeypatch.setattr(guard_mod, "_books_cache", None)
    monkeypatch.setattr(guard_mod, "_books_cache_mtime", 0)
    with pytest.raises(GuardError) as exc:
        pre_order_guard(book="binance_crypto", strategy_id="anything")
    assert "books_registry" in str(exc.value).lower() or "not found" in str(exc.value).lower()
