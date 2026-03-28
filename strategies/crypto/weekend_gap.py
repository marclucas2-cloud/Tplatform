"""
STRAT-008 — Weekend Gap Reversal (Spot Only).

Edge: Crypto markets trade 24/7 but institutional and retail activity drops
significantly during weekends. Weekend selloffs (Friday 22:00 → Sunday
22:00 UTC) of -3% to -8% on BTC tend to mean-revert during the Monday
session when TradFi reopens and institutional buyers return.

This exploits the "weekend dip" anomaly — a microstructure pattern caused
by thin weekend liquidity amplifying selling pressure, which reverses when
normal liquidity returns.

Signal:
  IF BTC weekend return (Fri 22:00 → Sun 22:00 UTC) < -3%:
    BUY Sunday evening (spot)
  IF weekend return < -8%:
    NO TRADE — possible real crash, not a liquidity artefact
  EXIT:
    Price returns to Friday 22:00 level (gap fill) OR after 48h
  SL: -5%
  Max 1 trade per weekend

Allocation: 10% of crypto capital
Leverage: none (spot only)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Weekend Gap Reversal",
    "id": "STRAT-008",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1h",
    "frequency": "weekly",  # evaluated Sunday evening
    "max_trades_per_weekend": 1,
}

# ── Weekend window definition (UTC) ────────────────────────────────────
# Friday 22:00 UTC → Sunday 22:00 UTC
FRIDAY_CLOSE_HOUR = 22
SUNDAY_ENTRY_HOUR = 22

# ── Signal thresholds ──────────────────────────────────────────────────
WEEKEND_DIP_MIN = -0.03      # -3% minimum dip to trigger
WEEKEND_DIP_CRASH = -0.08    # -8% or worse = real crash, skip
SL_PCT = -0.05               # -5% stop loss from entry
MAX_HOLDING_HOURS = 48        # exit after 48h max


def compute_weekend_return(
    friday_price: float,
    sunday_price: float,
) -> float | None:
    """Compute weekend return from Friday 22:00 to Sunday 22:00 UTC.

    Args:
        friday_price: BTC price at Friday 22:00 UTC
        sunday_price: BTC price at Sunday 22:00 UTC

    Returns:
        Weekend return as decimal (e.g., -0.04 = -4%), or None if invalid
    """
    if friday_price <= 0 or sunday_price <= 0:
        return None
    return (sunday_price / friday_price) - 1


def is_sunday_entry_window(timestamp: pd.Timestamp) -> bool:
    """Check if we're in the Sunday evening entry window (21:00-23:00 UTC).

    Args:
        timestamp: current UTC timestamp

    Returns:
        True if in entry window
    """
    if not hasattr(timestamp, "dayofweek"):
        return False
    # Sunday = 6
    if timestamp.dayofweek != 6:
        return False
    # Window: 21:00-23:00 UTC (around 22:00)
    return 21 <= timestamp.hour <= 23


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate weekend gap reversal signal.

    Args:
        candle: latest closed 1h candle
        state: {positions, capital, equity, i}

    Kwargs:
        friday_close_price: BTC price at Friday 22:00 UTC
        is_sunday_evening: True if current time is Sunday ~22:00 UTC
        traded_this_weekend: True if already traded this weekend
        df_full: full 1h DataFrame (optional, for indicator computation)

    Returns:
        Signal dict or None
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0
    price = candle.get("close", 0)

    if price <= 0:
        return None

    # ── Exit checks for existing position ───────────────────────────────
    if has_position:
        pos = positions[0]
        friday_price = kwargs.get("friday_close_price", 0)

        # Gap fill exit: price returned to Friday level
        if friday_price > 0 and price >= friday_price:
            return {
                "action": "CLOSE",
                "reason": "gap_fill_complete",
                "friday_price": round(friday_price, 2),
                "current_price": round(price, 2),
                "strategy": "weekend_gap",
            }

        # Stop loss
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            unrealized = (price / pos.entry_price) - 1
            if unrealized < SL_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "stop_loss_5pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "weekend_gap",
                }

        # Max holding time
        ts = candle.get("timestamp", None)
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_hours = (
                    pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)
                ).total_seconds() / 3600
            except Exception:
                holding_hours = 0
            if holding_hours >= MAX_HOLDING_HOURS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_48h",
                    "strategy": "weekend_gap",
                }

        return None

    # ── Entry evaluation (Sunday evening only) ──────────────────────────
    is_sunday = kwargs.get("is_sunday_evening", False)
    ts = candle.get("timestamp", None)
    if not is_sunday:
        # Also check from timestamp
        if ts is not None:
            try:
                if not is_sunday_entry_window(pd.Timestamp(ts)):
                    return None
            except Exception:
                return None
        else:
            return None

    # Already traded this weekend?
    if kwargs.get("traded_this_weekend", False):
        return None

    # Need Friday close price
    friday_price = kwargs.get("friday_close_price", 0)
    if friday_price <= 0:
        return None

    # Compute weekend return
    weekend_return = compute_weekend_return(friday_price, price)
    if weekend_return is None:
        return None

    # ── Filter: skip if crash (< -8%) ───────────────────────────────────
    if weekend_return < WEEKEND_DIP_CRASH:
        return None  # Real crash, not a liquidity artefact

    # ── Entry: buy if dip is -3% to -8% ─────────────────────────────────
    if weekend_return < WEEKEND_DIP_MIN:
        sl = price * (1 + SL_PCT)  # -5% from entry
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"],
            "stop_loss": sl,
            "take_profit": friday_price,  # target = gap fill
            "leverage": 1,
            "market_type": "spot",
            "strategy": "weekend_gap",
            "weekend_data": {
                "friday_close": round(friday_price, 2),
                "sunday_price": round(price, 2),
                "weekend_return_pct": round(weekend_return * 100, 2),
                "gap_to_fill_pct": round((friday_price / price - 1) * 100, 2),
            },
        }

    return None
