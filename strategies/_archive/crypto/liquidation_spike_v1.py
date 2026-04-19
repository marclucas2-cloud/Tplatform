"""
STRAT-011 — Liquidation / Panic Spike (counter-trend on forced sellers).

Edge: Crypto liquidation cascades create massive forced-selling that overshoots
fair value. When a candle has extreme range (>2.5x ATR) + volume spike (>2x avg)
+ long wick (rejection), the overshoot reverts within 1-4 hours. Counter-trade
the panic.

Signal (1H timeframe, checked every 15m for speed):
  LONG after crash:
    candle range > 2.5x ATR(14)
    volume > 2x 24h avg
    lower wick > 60% of candle range (rejection)
  SHORT after spike:
    candle range > 2.5x ATR(14)
    volume > 2x 24h avg
    upper wick > 60% of candle range (rejection)
  EXIT:
    TP: 0.5-1.5%
    SL: 0.7%
    Max holding: 4 hours

Params: 3 only (ATR mult, volume mult, wick ratio) — anti-overfitting.

Allocation: 10% of crypto capital
Leverage: 1x (spot only, conservative — panic = high risk)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Liquidation Spike",
    "id": "STRAT-011",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1h",
    "frequency": "15m",  # Check every 15m for fast reaction
    "max_positions": 1,
}

# 3 params
ATR_PERIOD = 14
RANGE_ATR_MULT = 2.5    # candle range must be 2.5x ATR
VOLUME_MULT = 2.0        # volume must be 2x 24h average
WICK_RATIO = 0.60        # wick must be >60% of candle range

TP_PCT = 0.01            # 1% take profit
SL_PCT = 0.007           # 0.7% stop loss


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Detect liquidation spike and counter-trade."""
    df_full = kwargs.get("df_full")
    if df_full is None or len(df_full) < ATR_PERIOD + 30:
        return None

    df = df_full.copy()

    # ATR
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": (df["high"] - df["close"].shift(1)).abs(),
        "lc": (df["low"] - df["close"].shift(1)).abs(),
    })
    df["atr"] = tr.max(axis=1).rolling(ATR_PERIOD).mean()

    # Volume average (24 bars for 1h TF)
    df["vol_avg"] = df["volume"].rolling(24).mean()

    i = len(df) - 1
    row = df.iloc[i]

    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    vol = float(row["volume"])
    atr = float(row["atr"])
    vol_avg = float(row["vol_avg"])

    if np.isnan(atr) or atr <= 0 or np.isnan(vol_avg) or vol_avg <= 0:
        return None

    candle_range = h - l
    if candle_range <= 0:
        return None

    # Check range spike
    if candle_range < atr * RANGE_ATR_MULT:
        return None

    # Check volume spike
    if vol < vol_avg * VOLUME_MULT:
        return None

    # Check no existing position
    positions = state.get("positions", [])
    for p in positions:
        if "BTC" in p.get("symbol", ""):
            return None

    # Wick analysis
    body_top = max(o, c)
    body_bottom = min(o, c)
    upper_wick = h - body_top
    lower_wick = body_bottom - l

    # CRASH (lower wick > 60% of range) -> LONG (reversal)
    if lower_wick / candle_range > WICK_RATIO:
        sl = c * (1 - SL_PCT)
        tp = c * (1 + TP_PCT)
        return {
            "action": "BUY",
            "direction": "LONG",
            "symbol": "BTCUSDC",
            "price": c,
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "market_type": "spot",
            "strategy": "liquidation_spike_v1",
            "confidence": min(candle_range / (atr * 3), 1.0),
            "reason": f"CRASH spike range={candle_range:.0f} > {atr*RANGE_ATR_MULT:.0f}, vol={vol/vol_avg:.1f}x, lower_wick={lower_wick/candle_range:.0%}",
        }

    # SPIKE UP (upper wick > 60% of range) -> SHORT (reversal)
    if upper_wick / candle_range > WICK_RATIO:
        sl = c * (1 + SL_PCT)
        tp = c * (1 - TP_PCT)
        return {
            "action": "SELL",
            "direction": "SHORT",
            "symbol": "BTCUSDC",
            "price": c,
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "market_type": "margin",
            "strategy": "liquidation_spike_v1",
            "confidence": min(candle_range / (atr * 3), 1.0),
            "reason": f"SPIKE UP range={candle_range:.0f} > {atr*RANGE_ATR_MULT:.0f}, vol={vol/vol_avg:.1f}x, upper_wick={upper_wick/candle_range:.0%}",
        }

    return None
