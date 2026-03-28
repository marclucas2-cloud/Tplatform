"""
STRAT-002 — Funding Rate Arbitrage (Perp + Spot).

Edge: Structural — retail traders are systematically net long in bull markets,
pushing funding rates positive. We short the perp and long the spot to collect
funding while remaining delta-neutral.

Signal Entry:
  - Funding rate > 0.05% (annualized ~65%)
  - Predicted next funding > 0.03%
  - Positive funding for >= 3 consecutive periods (24h)
  - Spread spot-perp < 0.1%

Signal Exit:
  - Funding rate < 0.01%
  - OR funding rate negative
  - OR unrealized loss > 2%
  - OR holding > 14 days

Allocation: 20% of crypto capital
Leverage: 1x (delta-neutral)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Funding Rate Arbitrage",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    "allocation_pct": 0.20,
    "max_leverage": 1,
    "market_type": "both",  # spot + futures
    "timeframe": "8h",
    "frequency": "8h",
}

# Parameters
FUNDING_ENTRY_MIN = 0.0005        # 0.05% per 8h
FUNDING_PREDICTED_MIN = 0.0003    # 0.03% predicted
FUNDING_CONSECUTIVE_MIN = 3       # 3 periods (24h)
SPREAD_MAX_PCT = 0.001            # 0.1% max spread
FUNDING_EXIT_MIN = 0.0001         # 0.01% exit threshold
MAX_UNREALIZED_LOSS_PCT = 0.02    # 2% max loss
MAX_HOLDING_DAYS = 14
MIN_VOLUME_24H = 100_000_000     # $100M
MIN_OI = 50_000_000              # $50M
MAX_SPREAD_BPS = 5


def check_entry_conditions(
    funding_history: list[float],
    current_funding: float,
    predicted_funding: float,
    spot_perp_spread: float,
    volume_24h: float,
    open_interest: float,
    spread_bps: float,
) -> tuple[bool, str]:
    """Check if entry conditions are met for funding arb.

    Returns:
        (should_enter, reason)
    """
    # Volume filter
    if volume_24h < MIN_VOLUME_24H:
        return False, f"volume {volume_24h:.0f} < {MIN_VOLUME_24H}"

    # OI filter
    if open_interest < MIN_OI:
        return False, f"OI {open_interest:.0f} < {MIN_OI}"

    # Spread filter
    if spread_bps > MAX_SPREAD_BPS:
        return False, f"spread {spread_bps} bps > {MAX_SPREAD_BPS}"

    # Funding rate checks
    if current_funding < FUNDING_ENTRY_MIN:
        return False, f"funding {current_funding:.6f} < {FUNDING_ENTRY_MIN}"

    if predicted_funding < FUNDING_PREDICTED_MIN:
        return False, f"predicted {predicted_funding:.6f} < {FUNDING_PREDICTED_MIN}"

    # Consecutive positive funding
    if len(funding_history) < FUNDING_CONSECUTIVE_MIN:
        return False, "insufficient funding history"

    recent = funding_history[-FUNDING_CONSECUTIVE_MIN:]
    if not all(f > 0 for f in recent):
        return False, "funding not consistently positive"

    # Spot-perp spread
    if abs(spot_perp_spread) > SPREAD_MAX_PCT:
        return False, f"spot-perp spread {spot_perp_spread:.4f} > {SPREAD_MAX_PCT}"

    return True, "all conditions met"


def check_exit_conditions(
    current_funding: float,
    unrealized_pnl_pct: float,
    holding_days: float,
) -> tuple[bool, str]:
    """Check if exit conditions are met.

    Returns:
        (should_exit, reason)
    """
    if current_funding < FUNDING_EXIT_MIN:
        return True, "funding_below_threshold"

    if current_funding < 0:
        return True, "funding_negative"

    if unrealized_pnl_pct < -MAX_UNREALIZED_LOSS_PCT:
        return True, "max_loss_exceeded"

    if holding_days >= MAX_HOLDING_DAYS:
        return True, "max_holding"

    return False, ""


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for funding rate arbitrage.

    Expected kwargs:
      - funding_rate: current funding rate
      - predicted_funding: next predicted rate
      - funding_history: list of recent rates
      - spot_price: current spot price
      - perp_price: current perp mark price
      - volume_24h: 24h volume in USD
      - open_interest: OI in USD
      - spread_bps: bid-ask spread in bps
    """
    positions = state.get("positions", [])
    capital = state.get("capital", 10_000)

    current_funding = kwargs.get("funding_rate", 0)
    predicted_funding = kwargs.get("predicted_funding", 0)
    funding_history = kwargs.get("funding_history", [])
    spot_price = kwargs.get("spot_price", candle.get("close", 0))
    perp_price = kwargs.get("perp_price", candle.get("close", 0))
    volume_24h = kwargs.get("volume_24h", 0)
    open_interest = kwargs.get("open_interest", 0)
    spread_bps = kwargs.get("spread_bps", 0)

    has_position = len(positions) > 0

    # Check exit
    if has_position:
        pos = positions[0]
        unrealized_pct = 0
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            current_price = candle.get("close", pos.entry_price)
            unrealized_pct = (current_price - pos.entry_price) / pos.entry_price * pos.direction

        holding_days = 0
        if hasattr(pos, "entry_time") and hasattr(candle.get("timestamp", None), "timestamp"):
            holding_days = (candle["timestamp"] - pos.entry_time).days

        should_exit, reason = check_exit_conditions(
            current_funding, unrealized_pct, holding_days
        )
        if should_exit:
            return {
                "action": "CLOSE",
                "reason": reason,
                "strategy": "funding_arb",
            }
        return None

    # Check entry
    spot_perp_spread = (perp_price - spot_price) / spot_price if spot_price > 0 else 0

    should_enter, reason = check_entry_conditions(
        funding_history, current_funding, predicted_funding,
        spot_perp_spread, volume_24h, open_interest, spread_bps,
    )

    if should_enter:
        # SHORT perp (collect funding) — spot hedge handled externally
        position_capital = capital * STRATEGY_CONFIG["allocation_pct"]
        qty = position_capital / perp_price if perp_price > 0 else 0

        return {
            "action": "SELL",  # Short perp
            "qty": qty,
            "stop_loss": perp_price * 1.05,  # -5% stop
            "leverage": 1,
            "market_type": "futures",
            "strategy": "funding_arb",
            # Spot hedge order
            "_hedge": {
                "action": "BUY",
                "qty": qty,
                "market_type": "spot",
            },
        }

    return None


def estimate_annual_yield(avg_funding_rate: float) -> float:
    """Estimate annualized yield from funding arb.

    Args:
        avg_funding_rate: average 8h funding rate (e.g. 0.0005 = 0.05%)

    Returns:
        Annualized yield (e.g. 0.65 = 65%)
    """
    # 3 funding periods per day * 365 days
    return avg_funding_rate * 3 * 365
