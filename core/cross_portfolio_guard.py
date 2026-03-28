"""
Guard #15 — Cross-portfolio correlation monitor (IBKR + Binance).

Checks combined directional exposure every 4h.
Alert at > 120% combined net long, critical at > 150%.

BTC-SPY correlation history:
  - Normal:     ~0.3
  - Correction: ~0.5
  - Crash:      ~0.8+ (everything correlates in panic)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WARNING_THRESHOLD = 1.20   # 120% combined net long
CRITICAL_THRESHOLD = 1.50  # 150% combined net long


def check_combined_exposure(
    ibkr_long: float,
    ibkr_short: float,
    ibkr_capital: float,
    crypto_long: float,
    crypto_short: float,
    crypto_capital: float,
) -> dict:
    """Check combined directional exposure across IBKR and Binance.

    Args:
        ibkr_long/short: IBKR long/short exposure in USD
        ibkr_capital: IBKR capital
        crypto_long/short: Crypto long/short exposure in USD
        crypto_capital: Crypto capital

    Returns:
        dict with level, combined_pct, message
    """
    total_capital = ibkr_capital + crypto_capital
    if total_capital <= 0:
        return {"level": "OK", "combined_pct": 0, "message": "no capital"}

    ibkr_net = ibkr_long - ibkr_short
    crypto_net = crypto_long - crypto_short
    combined_net = ibkr_net + crypto_net
    combined_pct = combined_net / total_capital

    result = {
        "combined_net_usd": round(combined_net, 2),
        "combined_pct": round(combined_pct * 100, 1),
        "ibkr_net_pct": round(ibkr_net / ibkr_capital * 100, 1) if ibkr_capital > 0 else 0,
        "crypto_net_pct": round(crypto_net / crypto_capital * 100, 1) if crypto_capital > 0 else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if combined_pct > CRITICAL_THRESHOLD:
        result["level"] = "CRITICAL"
        result["message"] = (
            f"Combined exposure {combined_pct*100:.0f}% > {CRITICAL_THRESHOLD*100:.0f}%. "
            f"Reduce positions in one or both portfolios."
        )
        logger.critical(result["message"])
    elif combined_pct > WARNING_THRESHOLD:
        result["level"] = "WARNING"
        result["message"] = (
            f"Combined exposure {combined_pct*100:.0f}% > {WARNING_THRESHOLD*100:.0f}%. "
            f"Both portfolios heavily net long — crash risk."
        )
        logger.warning(result["message"])
    else:
        result["level"] = "OK"
        result["message"] = f"Combined exposure {combined_pct*100:.0f}% — within limits"

    return result
