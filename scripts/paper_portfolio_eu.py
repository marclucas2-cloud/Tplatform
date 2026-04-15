#!/usr/bin/env python3
"""DEPRECATED SHIM — this file was renamed to live_portfolio_eu.py.

Reason: the previous name "paper_portfolio_eu" was misleading because the
actual content routes LIVE orders to IBKR port 4002. Renamed 2026-04-15
during P0.3 live hardening to eliminate operator confusion.

This shim re-exports everything from the new module with a DeprecationWarning
so existing imports keep working until all callers are migrated.

Migration path:
  old: from scripts.paper_portfolio_eu import foo
  new: from scripts.live_portfolio_eu import foo
"""
from __future__ import annotations

import warnings

warnings.warn(
    "scripts.paper_portfolio_eu is deprecated; use scripts.live_portfolio_eu "
    "(the script routes LIVE orders, not paper). Imports still work but will "
    "be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the new location
from scripts.live_portfolio_eu import *  # noqa: F401,F403,E402
from scripts.live_portfolio_eu import (  # noqa: F401,E402
    run_intraday_eu,
    load_strategies_from_yaml,
    compute_eu_allocations,
    is_strategy_active,
    signal_bce_momentum_drift,
    signal_auto_sector_german,
    signal_brent_lag_play,
    signal_eu_close_us_afternoon,
    signal_eu_gap_open,
    check_circuit_breaker_eu,
    check_kill_switch_eu,
    close_eu_positions,
    execute_eu_signals,
    log_strategy_daily_pnl_eu,
    save_state,
    SIGNAL_DISPATCH,
    STATE_FILE,
)
