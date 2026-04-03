"""Fix #3: Minimum Position Size Filter — skip positions too small to be profitable.

Placed AFTER Kelly sizing, BEFORE broker submit.
If position < minimum -> log SKIP instead of submitting a micro-order.

A $56 trade on BTC with 0.075% commission = $0.08 round-trip commission.
With 2% TP, that's $1.12 profit - $0.08 = $1.04 net.
Not worth the execution risk and complexity.
"""

import logging
from typing import Any

logger = logging.getLogger("signal_funnel")

# Minimum viable position sizes by asset class (in USD)
MIN_POSITION_USD = {
    "crypto_spot": 50,       # Binance min $10, but $50 for viable PnL
    "crypto_margin": 100,    # Margin = leverage, needs more base capital
    "crypto": 50,            # Default crypto
    "fx": 200,               # FX micro lot = $1K notional, $200 margin
    "eu_equity": 200,        # 1 share min on most EU stocks
    "us_equity": 100,        # Fractional shares, but $100 for viable PnL
    "futures": 0,            # 1 contract minimum, no size issue
    "default": 100,
}


def is_position_viable(
    size_usd: float,
    asset_class: str,
    strategy: str = "",
) -> tuple[bool, str]:
    """Check if position is large enough to be worth trading.

    Returns:
        (True, "") if viable
        (False, reason) if too small
    """
    min_size = MIN_POSITION_USD.get(asset_class, MIN_POSITION_USD["default"])

    if size_usd < min_size:
        reason = (
            f"Position ${size_usd:.0f} below minimum ${min_size} "
            f"for {asset_class}"
        )
        logger.info(
            "FUNNEL|%s|min_size|SKIP|size=$%.0f|min=$%d|class=%s",
            strategy, size_usd, min_size, asset_class,
        )
        return False, reason

    return True, ""


def get_minimum_size(asset_class: str) -> float:
    """Get minimum position size for an asset class."""
    return MIN_POSITION_USD.get(asset_class, MIN_POSITION_USD["default"])


def adjust_or_skip(
    raw_size_usd: float,
    asset_class: str,
    strategy: str = "",
) -> float:
    """Return the size if viable, 0.0 if not.

    Usage:
        size = adjust_or_skip(kelly_size, "crypto", "btc_eth_dual_momentum")
        if size == 0:
            # Skip this trade
            continue
    """
    viable, reason = is_position_viable(raw_size_usd, asset_class, strategy)
    if not viable:
        return 0.0
    return raw_size_usd
