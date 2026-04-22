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
    """2 strats alpha pur encore en live_core (gold_trend_mgc downgrade
    paper_only le 2026-04-16 pour V1 SL/TP recalibration)."""
    assert is_strategy_live_allowed("cross_asset_momentum", "ibkr_futures") is True
    assert is_strategy_live_allowed("gold_oil_rotation", "ibkr_futures") is True
    # gold_trend_mgc transitionne en paper_only -> bloque en live
    assert is_strategy_live_allowed("gold_trend_mgc", "ibkr_futures") is False


def test_fx_carry_is_disabled():
    """FX doit etre disabled — ESMA EU leverage limits."""
    assert is_strategy_live_allowed("fx_carry_momentum_filter", "ibkr_fx") is False


def test_eu_gap_open_is_paper_only():
    """eu_gap_open a ete rejetee OOS, doit etre paper_only."""
    assert is_strategy_live_allowed("eu_gap_open", "ibkr_eu") is False


def test_alpaca_us_paper_only():
    assert is_strategy_live_allowed("us_stocks_daily", "alpaca_us") is False


def test_mcl_overnight_mon_trend10_is_paper_only():
    """T3-A1 INT-B 2026-04-18 promotion: paper_only avec caveats trigger
    shift + data source mismatch documentes. Ne doit PAS etre live_allowed."""
    assert is_strategy_live_allowed("mcl_overnight_mon_trend10", "ibkr_futures") is False
    # Entry existe et runtime_module correct
    entry = get_strategy_entry("mcl_overnight_mon_trend10", "ibkr_futures")
    assert entry is not None
    assert entry["status"] == "paper_only"
    assert entry["runtime_module"] == "strategies_v2.futures.mcl_overnight_mon_trend.MCLOvernightMonTrend"


def test_alt_rel_strength_14_60_7_is_paper_only():
    """T4-A2 INT-D 2026-04-18 promotion: paper_only avec runner atomic 6-leg.
    Ne doit PAS etre live_allowed tant que 30j paper obs + infra gaps fixed."""
    assert is_strategy_live_allowed("alt_rel_strength_14_60_7", "binance_crypto") is False
    entry = get_strategy_entry("alt_rel_strength_14_60_7", "binance_crypto")
    assert entry is not None
    assert entry["status"] == "paper_only"
    assert entry["runtime_module"] == "core.runtime.alt_rel_strength_runner.AltRelStrengthRunner"


def test_btc_asia_mes_leadlag_q70_v80_archived():
    """T3-A2 INT-B 2026-04-18 -> ARCHIVED 2026-04-22 (PO verdict Marc).
    Duplicate du q80_long_only, variante mode=both incompatible Binance France spot.
    Ne doit plus etre dans live_whitelist (ni live_allowed, ni paper_only)."""
    assert is_strategy_live_allowed("btc_asia_mes_leadlag_q70_v80", "binance_crypto") is False
    entry = get_strategy_entry("btc_asia_mes_leadlag_q70_v80", "binance_crypto")
    assert entry is None, "q70_v80 doit etre retire de live_whitelist (archive 2026-04-22)"


def test_us_sector_ls_40_5_is_paper_only():
    """T3-B1 INT-C 2026-04-18 promotion: paper_only log-only.
    Book alpaca_us paper-only. Ne doit PAS etre live_allowed."""
    assert is_strategy_live_allowed("us_sector_ls_40_5", "alpaca_us") is False
    entry = get_strategy_entry("us_sector_ls_40_5", "alpaca_us")
    assert entry is not None
    assert entry["status"] == "paper_only"
    assert entry["runtime_module"] == "strategies_v2.us.us_sector_ls"


def test_eu_relmom_40_3_is_paper_only():
    """T3-A3 INT-B 2026-04-18 promotion: paper_only log-only.
    Book ibkr_eu paper-only. Ne doit PAS etre live_allowed."""
    assert is_strategy_live_allowed("eu_relmom_40_3", "ibkr_eu") is False
    entry = get_strategy_entry("eu_relmom_40_3", "ibkr_eu")
    assert entry is not None
    assert entry["status"] == "paper_only"
    assert entry["runtime_module"] == "strategies_v2.eu.eu_relmom"


def test_unknown_strategy_blocked():
    """Une strategie hors whitelist doit etre bloquee."""
    assert is_strategy_live_allowed("fake_strategy_xyz", "ibkr_futures") is False
    assert is_strategy_live_allowed("fake_strategy_xyz") is False


def test_binance_core_strats_demoted_post_audit_2026_04_18():
    """P0.2 audit 2026-04-18: TOUTES les crypto live demoted en paper_only
    apres re-WF event-driven (wf_results.json original etait B&H BTC ajuste
    couts, pas un vrai backtest). Aucune crypto n'est live_allowed actuellement.
    """
    # btc_eth_dual_momentum: re-WF REJECTED Sharpe -6.08
    assert is_strategy_live_allowed("btc_eth_dual_momentum", "binance_crypto") is False
    # volatility_breakout: re-WF INSUFFICIENT_TRADES (0 signaux)
    assert is_strategy_live_allowed("volatility_breakout", "binance_crypto") is False


def test_binance_probation_strats_demoted_post_audit_2026_04_18():
    """P0.2 audit 2026-04-18: probation strats demoted en paper_only.
    liquidation_momentum/weekend_gap_reversal: NEEDS_RE_WF (kwargs simulator manquant).
    """
    assert is_strategy_live_allowed("liquidation_momentum", "binance_crypto") is False
    assert is_strategy_live_allowed("weekend_gap_reversal", "binance_crypto") is False


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
    assert "gold_oil_rotation" in ids
    # gold_trend_mgc downgrade en paper_only le 2026-04-16, plus dans live
    assert "gold_trend_mgc" not in ids


def test_list_live_strategies_all_books():
    live = list_live_strategies()
    # P0.2 audit 2026-04-18: TOUTES les crypto demoted en paper_only post re-WF.
    # Reste live: 2 futures live_core (CAM + GoldOilRotation). Total = 2.
    assert len(live) >= 2
    fx_entries = [e for e in live if e["_book"] == "ibkr_fx"]
    assert len(fx_entries) == 0  # FX disabled ESMA
    eu_entries = [e for e in live if e["_book"] == "ibkr_eu"]
    assert len(eu_entries) == 0  # EU paper_only
    crypto_entries = [e for e in live if e["_book"] == "binance_crypto"]
    assert len(crypto_entries) == 0  # P0.2 demote: toutes crypto paper_only
