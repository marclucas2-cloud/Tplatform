"""
STRAT-006 — BTC Dominance Rotation (Spot).

Edge: BTC dominance is cyclical and trends.
When dominance rises → capital flows from alts to BTC → hold BTC.
When dominance falls → "alt season" → hold altcoin basket.

Signal:
  - BTC dominance EMA(7) > EMA(21) → 100% BTC spot
  - BTC dominance EMA(7) < EMA(21) → 50% ETH + 50% top 3 altcoins
  - Dead zone: 48-52% dominance → no trade (too uncertain)
  - Rebalance weekly

Allocation: 10% of crypto capital
Leverage: none (spot only)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "BTC Dominance Rotation",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1d",
    "frequency": "weekly",
}

# Parameters
EMA_FAST = 7
EMA_SLOW = 21
DEAD_ZONE_LOW = 48.0     # Don't trade if dominance 48-52%
DEAD_ZONE_HIGH = 52.0
ALT_BASKET = ["ETHUSDT", "SOLUSDT", "BNBUSDT"]
BTC_WEIGHT = 1.0          # 100% BTC when dominance rising
ALT_WEIGHTS = {"ETHUSDT": 0.50, "SOLUSDT": 0.25, "BNBUSDT": 0.25}


class DominanceRegime:
    BTC = "BTC_SEASON"
    ALT = "ALT_SEASON"
    NEUTRAL = "NEUTRAL"


def detect_dominance_regime(
    dominance_series: pd.Series,
) -> str:
    """Detect dominance regime from BTC dominance time series.

    Args:
        dominance_series: BTC dominance % values (e.g. 55.0, 48.3)

    Returns:
        DominanceRegime
    """
    if len(dominance_series) < EMA_SLOW + 5:
        return DominanceRegime.NEUTRAL

    ema_fast = dominance_series.ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = dominance_series.ewm(span=EMA_SLOW, adjust=False).mean()

    current_dom = dominance_series.iloc[-1]
    fast_val = ema_fast.iloc[-1]
    slow_val = ema_slow.iloc[-1]

    # Dead zone filter
    if DEAD_ZONE_LOW < current_dom < DEAD_ZONE_HIGH:
        return DominanceRegime.NEUTRAL

    if fast_val > slow_val:
        return DominanceRegime.BTC
    elif fast_val < slow_val:
        return DominanceRegime.ALT
    else:
        return DominanceRegime.NEUTRAL


def generate_rotation_signals(
    regime: str,
    current_positions: dict[str, float],  # {symbol: weight}
    capital: float,
) -> list[dict]:
    """Generate rotation signals based on dominance regime.

    Args:
        regime: current DominanceRegime
        current_positions: {symbol: current_weight}
        capital: total capital for this strategy

    Returns:
        List of signal dicts
    """
    signals = []

    if regime == DominanceRegime.NEUTRAL:
        # Close everything in dead zone
        for symbol in current_positions:
            signals.append({
                "action": "CLOSE",
                "symbol": symbol,
                "reason": "dead_zone",
                "strategy": "btc_dominance",
            })
        return signals

    # Target allocation
    if regime == DominanceRegime.BTC:
        targets = {"BTCUSDT": BTC_WEIGHT}
    else:  # ALT season
        targets = dict(ALT_WEIGHTS)

    # Close positions not in target
    for symbol in current_positions:
        if symbol not in targets:
            signals.append({
                "action": "CLOSE",
                "symbol": symbol,
                "reason": "rotation",
                "strategy": "btc_dominance",
            })

    # Open new positions
    for symbol, weight in targets.items():
        if symbol not in current_positions:
            notional = capital * weight
            signals.append({
                "action": "BUY",
                "symbol": symbol,
                "notional": notional,
                "market_type": "spot",
                "strategy": "btc_dominance",
            })

    return signals


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Simplified signal for single-asset backtesting.

    kwargs:
      - dominance_series: BTC dominance time series
      - is_rebalance_day: True on Sundays
      - current_asset: which asset is being backtested
    """
    if not kwargs.get("is_rebalance_day", False):
        return None

    dominance_series = kwargs.get("dominance_series")
    if dominance_series is None:
        return None

    regime = detect_dominance_regime(dominance_series)
    positions = state.get("positions", [])
    has_position = len(positions) > 0
    current_asset = kwargs.get("current_asset", "BTCUSDT")

    if regime == DominanceRegime.NEUTRAL:
        if has_position:
            return {"action": "CLOSE", "reason": "dead_zone", "strategy": "btc_dominance"}
        return None

    if regime == DominanceRegime.BTC:
        if current_asset == "BTCUSDT":
            if not has_position:
                return {
                    "action": "BUY",
                    "pct": STRATEGY_CONFIG["allocation_pct"],
                    "market_type": "spot",
                    "strategy": "btc_dominance",
                }
        else:
            if has_position:
                return {"action": "CLOSE", "reason": "btc_season", "strategy": "btc_dominance"}

    elif regime == DominanceRegime.ALT:
        if current_asset in ALT_WEIGHTS:
            if not has_position:
                weight = ALT_WEIGHTS.get(current_asset, 0.25)
                return {
                    "action": "BUY",
                    "pct": STRATEGY_CONFIG["allocation_pct"] * weight,
                    "market_type": "spot",
                    "strategy": "btc_dominance",
                }
        elif current_asset == "BTCUSDT":
            if has_position:
                return {"action": "CLOSE", "reason": "alt_season", "strategy": "btc_dominance"}

    return None
