"""Tests for core/governance/live_whitelist.py — P1.1 live hardening."""
from __future__ import annotations

import pytest

from core.governance.live_whitelist import (
    is_strategy_live_allowed,
    load_live_whitelist,
    list_live_strategies,
    get_strategy_entry,
    get_live_whitelist_version,
    LIVE_STATUSES,
    BLOCK_STATUSES,
)


def test_whitelist_loads():
    data = load_live_whitelist()
    assert data is not None
    assert "metadata" in data
    assert data["metadata"]["version"] >= 1


def test_whitelist_version_string():
    v = get_live_whitelist_version()
    assert v != "error"
    assert v != "unknown"


def test_ibkr_futures_live_strats_allowed():
    """3 strats alpha pur doivent etre live_core autorisees."""
    assert is_strategy_live_allowed("cross_asset_momentum", "ibkr_futures") is True
    assert is_strategy_live_allowed("gold_trend_mgc", "ibkr_futures") is True
    assert is_strategy_live_allowed("gold_oil_rotation", "ibkr_futures") is True


def test_fx_carry_is_disabled():
    """FX doit etre disabled — ESMA EU leverage limits."""
    assert is_strategy_live_allowed("fx_carry_momentum_filter", "ibkr_fx") is False


def test_eu_gap_open_is_paper_only():
    """eu_gap_open a ete rejetee OOS, doit etre paper_only."""
    assert is_strategy_live_allowed("eu_gap_open", "ibkr_eu") is False


def test_alpaca_us_paper_only():
    assert is_strategy_live_allowed("us_stocks_daily", "alpaca_us") is False


def test_unknown_strategy_blocked():
    """Une strategie hors whitelist doit etre bloquee."""
    assert is_strategy_live_allowed("fake_strategy_xyz", "ibkr_futures") is False
    assert is_strategy_live_allowed("fake_strategy_xyz") is False


def test_binance_core_strats_allowed():
    """Crypto core strats doivent etre live_core."""
    assert is_strategy_live_allowed("btc_eth_dual_momentum", "binance_crypto") is True
    assert is_strategy_live_allowed("volatility_breakout", "binance_crypto") is True


def test_binance_probation_strats_allowed():
    """Probation est toujours live_allowed (mais traceable)."""
    assert is_strategy_live_allowed("liquidation_momentum", "binance_crypto") is True
    assert is_strategy_live_allowed("weekend_gap_reversal", "binance_crypto") is True


def test_wrong_book_blocks():
    """Meme un strategy_id valide doit etre bloque si le book ne matche pas."""
    assert is_strategy_live_allowed("cross_asset_momentum", "binance_crypto") is False
    assert is_strategy_live_allowed("btc_eth_dual_momentum", "ibkr_futures") is False


def test_get_strategy_entry():
    entry = get_strategy_entry("cross_asset_momentum", "ibkr_futures")
    assert entry is not None
    assert entry["strategy_id"] == "cross_asset_momentum"
    assert entry["status"] == "live_core"
    assert entry["_book"] == "ibkr_futures"
    assert "wf_source" in entry


def test_list_live_strategies_futures():
    live = list_live_strategies("ibkr_futures")
    ids = {e["strategy_id"] for e in live}
    assert "cross_asset_momentum" in ids
    assert "gold_trend_mgc" in ids
    assert "gold_oil_rotation" in ids


def test_list_live_strategies_all_books():
    live = list_live_strategies()
    # 3 futures + 11 crypto live = 14 expected
    assert len(live) >= 14
    fx_entries = [e for e in live if e["_book"] == "ibkr_fx"]
    assert len(fx_entries) == 0  # FX disabled
    eu_entries = [e for e in live if e["_book"] == "ibkr_eu"]
    assert len(eu_entries) == 0  # EU paper_only
