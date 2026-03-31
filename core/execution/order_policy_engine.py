"""
Order Policy Engine — determine order type per strategy profile.

Trend strategies: LIMIT (pullback entry, patient)
MR strategies: LIMIT passive (fill at edge of range)
Liquidation strategies: MARKET (speed priority)

Fallback: MARKET if LIMIT not filled within timeout.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class OrderPolicy:
    order_type: Literal["MARKET", "LIMIT"]
    limit_offset_bps: float = 0  # offset from current price in bps
    timeout_seconds: float = 0   # fallback to MARKET after this
    priority: Literal["NORMAL", "HIGH", "URGENT"] = "NORMAL"
    max_slippage_bps: float = 10  # reject if slippage > this


# Strategy -> policy mapping
POLICY_MAP = {
    # Trend: patient entry via limit at pullback level
    "trend_short_v1": OrderPolicy(
        order_type="LIMIT",
        limit_offset_bps=5,    # 5 bps above current (for shorts)
        timeout_seconds=300,   # 5 min then MARKET
        priority="NORMAL",
        max_slippage_bps=15,
    ),
    # MR Scalp: passive limit, tight
    "mr_scalp_v1": OrderPolicy(
        order_type="LIMIT",
        limit_offset_bps=3,
        timeout_seconds=120,   # 2 min then MARKET
        priority="NORMAL",
        max_slippage_bps=8,
    ),
    # Liquidation: MARKET immediately, speed is alpha
    "liquidation_spike_v1": OrderPolicy(
        order_type="MARKET",
        limit_offset_bps=0,
        timeout_seconds=0,
        priority="URGENT",
        max_slippage_bps=20,
    ),
}

# Default for unknown strategies
DEFAULT_POLICY = OrderPolicy(
    order_type="MARKET",
    timeout_seconds=0,
    priority="NORMAL",
    max_slippage_bps=10,
)


def get_order_policy(strategy_name: str) -> OrderPolicy:
    """Get the order execution policy for a strategy."""
    return POLICY_MAP.get(strategy_name, DEFAULT_POLICY)


def compute_limit_price(
    current_price: float,
    direction: str,
    policy: OrderPolicy,
) -> float | None:
    """Compute limit price based on policy offset.

    Returns None if order_type is MARKET.
    """
    if policy.order_type == "MARKET":
        return None

    offset = current_price * policy.limit_offset_bps / 10000

    if direction in ("BUY", "LONG"):
        # Buy below current price
        return round(current_price - offset, 2)
    else:
        # Sell above current price
        return round(current_price + offset, 2)
