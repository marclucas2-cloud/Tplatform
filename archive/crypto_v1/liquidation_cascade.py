"""
STRAT-004 — Liquidation Cascade (Perp).

Edge: Microstructure — forced liquidations are market orders that push price
further in the same direction, creating predictable cascades.

Signal:
  1. Rapid move > 2% in 1h on BTC/ETH
  2. Liquidation volume > $50M in 1h
  3. OI drop > 5% in 1h
  4. Enter AFTER first wave (15-30 min after peak)
  5. Direction: follow the cascade (long shorts getting liquidated = long)

Filters:
  - Only BTC and ETH (most liquid)
  - Volume 1h > 3x 7d average
  - RSI not extreme (15-85 range)
  - No double exposure with trend strategy

Exit:
  - Stop: 1x ATR(14) on 1h
  - TP: 2x ATR(14) — 2:1 ratio
  - Max holding: 24h

Allocation: 15% of crypto capital
Leverage: 3x max (short trades, tight stops)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Liquidation Cascade",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.15,
    "max_leverage": 3,
    "market_type": "futures",
    "timeframe": "5m",
    "frequency": "5m",
}

# Parameters
MOVE_THRESHOLD_PCT = 0.02           # 2% move in 1h
LIQUIDATION_VOLUME_MIN = 50_000_000  # $50M
OI_DROP_MIN_PCT = 0.05              # 5% OI drop
VOLUME_SPIKE_MULT = 3.0             # 3x average
RSI_MIN = 15
RSI_MAX = 85
ATR_PERIOD = 14
SL_ATR_MULT = 1.0                  # 1x ATR stop
TP_ATR_MULT = 2.0                  # 2x ATR target (2:1 ratio)
MAX_HOLDING_HOURS = 24
COOLDOWN_BARS = 6                  # 30 min cooldown after entry (5m bars)
MAX_CONCURRENT = 1                 # Max 1 cascade trade at a time


def detect_cascade(
    price_change_1h: float,
    liquidation_volume_1h: float,
    oi_change_1h: float,
    volume_ratio: float,
    rsi: float,
) -> tuple[bool, str]:
    """Detect if a liquidation cascade is occurring.

    Returns:
        (is_cascade, direction "LONG" or "SHORT" or "")
    """
    # Check magnitude
    if abs(price_change_1h) < MOVE_THRESHOLD_PCT:
        return False, ""

    if liquidation_volume_1h < LIQUIDATION_VOLUME_MIN:
        return False, ""

    if abs(oi_change_1h) < OI_DROP_MIN_PCT:
        return False, ""

    if volume_ratio < VOLUME_SPIKE_MULT:
        return False, ""

    # RSI filter (don't enter at extremes — cascade may be over)
    if rsi < RSI_MIN or rsi > RSI_MAX:
        return False, ""

    # Direction: follow the cascade
    if price_change_1h < 0:
        # Price dropping + longs liquidated → SHORT
        return True, "SHORT"
    else:
        # Price rising + shorts liquidated → LONG
        return True, "LONG"


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for liquidation cascade trading.

    kwargs:
      - price_change_1h: 1h price change (decimal)
      - liquidation_volume_1h: liquidation volume in USD
      - oi_change_1h: OI change (decimal, e.g. -0.05)
      - volume_ratio: current vs 7d avg volume
      - rsi: RSI(14)
      - atr: ATR(14) on 1h candles
      - trend_positions: list of trend strategy positions (avoid double exposure)
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # Check exit
    if has_position:
        pos = positions[0]
        if hasattr(pos, "entry_time") and hasattr(candle.get("timestamp", None), "timestamp"):
            holding_hours = (candle["timestamp"] - pos.entry_time).total_seconds() / 3600
            if holding_hours >= MAX_HOLDING_HOURS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding",
                    "strategy": "liquidation_cascade",
                }
        return None

    if has_position:
        return None

    price_change_1h = kwargs.get("price_change_1h", 0)
    liquidation_volume = kwargs.get("liquidation_volume_1h", 0)
    oi_change = kwargs.get("oi_change_1h", 0)
    volume_ratio = kwargs.get("volume_ratio", 1)
    rsi = kwargs.get("rsi", 50)
    atr = kwargs.get("atr", 0)

    if atr <= 0:
        return None

    # Check for double exposure with trend
    trend_positions = kwargs.get("trend_positions", [])

    is_cascade, direction = detect_cascade(
        price_change_1h, liquidation_volume, oi_change, volume_ratio, rsi
    )

    if not is_cascade:
        return None

    # Avoid same-direction double exposure with trend
    for tp in trend_positions:
        if hasattr(tp, "direction"):
            tp_dir = "LONG" if tp.direction > 0 else "SHORT"
            if tp_dir == direction:
                return None

    price = candle.get("close", 0)
    if price <= 0:
        return None

    if direction == "LONG":
        sl = price - SL_ATR_MULT * atr
        tp = price + TP_ATR_MULT * atr
        return {
            "action": "BUY",
            "pct": 0.05,  # 5% of capital
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "futures",
            "strategy": "liquidation_cascade",
        }
    else:
        sl = price + SL_ATR_MULT * atr
        tp = price - TP_ATR_MULT * atr
        return {
            "action": "SELL",
            "pct": 0.05,
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "futures",
            "strategy": "liquidation_cascade",
        }
