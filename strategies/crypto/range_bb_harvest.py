"""
STRAT-014 — Range Trading Bollinger Harvest (4H).

Edge: 70% of bear market time is sideways (chop). BBands on 4H capture
the range extremes. Mean reversion at BB edges works when ADX is low
(no trend). TF 4H keeps trades rare enough for costs.

Signal:
  LONG: Close < lower BB AND ADX < 20 (range, not trend)
  SHORT: Close > upper BB AND ADX < 20
  EXIT:
    TP: middle BB (SMA20)
    SL: 1.5x distance to BB edge
    Max holding: 3 days (18 bars)

3 params: BB period, ADX threshold, SL multiplier.
Allocation: 10%
"""
from __future__ import annotations
import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Range BB Harvest",
    "id": "STRAT-014",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1.5,
    "market_type": "spot",
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 1,
}

BB_PERIOD = 20
BB_STD = 2.0
ADX_PERIOD = 14
ADX_MAX = 20  # Only trade when ADX < 20 (range-bound)
SL_MULT = 1.5


def _compute_adx(df, period=14):
    plus_dm = df["high"].diff().clip(lower=0)
    minus_dm = (-df["low"].diff()).clip(lower=0)
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": (df["high"] - df["close"].shift(1)).abs(),
        "lc": (df["low"] - df["close"].shift(1)).abs(),
    }).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    df = kwargs.get("df_full")
    if df is None or len(df) < BB_PERIOD + ADX_PERIOD + 20:
        return None

    df = df.copy()
    df["sma"] = df["close"].rolling(BB_PERIOD).mean()
    df["std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["sma"] + BB_STD * df["std"]
    df["bb_lower"] = df["sma"] - BB_STD * df["std"]
    df["adx"] = _compute_adx(df, ADX_PERIOD)

    i = len(df) - 1
    row = df.iloc[i]
    price = float(row["close"])
    bb_upper = float(row["bb_upper"])
    bb_lower = float(row["bb_lower"])
    sma = float(row["sma"])
    adx = float(row["adx"])

    if any(np.isnan(x) for x in [bb_upper, bb_lower, sma, adx]):
        return None

    # Range-bound filter
    if adx >= ADX_MAX:
        return None

    # Check no existing position
    for p in state.get("positions", []):
        if "BTC" in p.get("symbol", ""):
            return None

    # LONG at lower BB
    if price < bb_lower:
        sl_dist = sma - price
        sl = price - sl_dist * SL_MULT
        return {
            "action": "BUY", "direction": "LONG", "symbol": "BTCUSDC",
            "price": price, "stop_loss": round(sl, 2), "take_profit": round(sma, 2),
            "market_type": "spot", "strategy": "range_bb_harvest",
            "confidence": min((bb_lower - price) / (bb_upper - bb_lower) * 2, 1.0),
            "reason": f"Range LONG: price={price:.0f}<BB_low={bb_lower:.0f}, ADX={adx:.1f}<{ADX_MAX}, TP=SMA={sma:.0f}",
        }

    # SHORT at upper BB
    if price > bb_upper:
        sl_dist = price - sma
        sl = price + sl_dist * SL_MULT
        return {
            "action": "SELL", "direction": "SHORT", "symbol": "BTCUSDC",
            "price": price, "stop_loss": round(sl, 2), "take_profit": round(sma, 2),
            "market_type": "margin", "strategy": "range_bb_harvest",
            "confidence": min((price - bb_upper) / (bb_upper - bb_lower) * 2, 1.0),
            "reason": f"Range SHORT: price={price:.0f}>BB_up={bb_upper:.0f}, ADX={adx:.1f}<{ADX_MAX}, TP=SMA={sma:.0f}",
        }

    return None
