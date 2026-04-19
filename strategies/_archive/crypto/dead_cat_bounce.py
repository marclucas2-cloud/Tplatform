"""
STRAT-013 — Dead Cat Bounce Fade (4H).

Edge: In bear markets, sharp dumps are followed by reflexive bounces that
trap longs. These bounces fail at resistance (EMA50/100) and resume the
downtrend. We short the bounce after it shows exhaustion.

Signal:
  SHORT entry:
    Price below EMA100 (bear trend confirmed)
    RSI > 55 after being < 30 in last 20 bars (bounce from oversold)
    Volume declining on bounce (weak hands buying)
  EXIT:
    SL: above recent swing high (1.5x ATR)
    TP: 2x ATR (continuation of downtrend)
    Max holding: 5 days (30 bars)

3 params: EMA period, RSI bounce threshold, volume decline lookback.
Allocation: 12%
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Dead Cat Bounce Fade",
    "id": "STRAT-013",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.12,
    "max_leverage": 2,
    "market_type": "margin",
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 1,
}

EMA_PERIOD = 100
RSI_PERIOD = 14
RSI_OVERSOLD = 30      # Must have been oversold recently
RSI_BOUNCE = 55        # Current RSI shows bounce
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.0
LOOKBACK_BARS = 20     # Check for recent oversold


def _compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    df = kwargs.get("df_full")
    if df is None or len(df) < EMA_PERIOD + 30:
        return None

    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    df["rsi"] = _compute_rsi(df["close"], RSI_PERIOD)

    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": (df["high"] - df["close"].shift(1)).abs(),
        "lc": (df["low"] - df["close"].shift(1)).abs(),
    })
    df["atr"] = tr.max(axis=1).rolling(ATR_PERIOD).mean()

    i = len(df) - 1
    row = df.iloc[i]
    price = float(row["close"])
    ema = float(row["ema"])
    rsi = float(row["rsi"])
    atr = float(row["atr"])

    if any(np.isnan(x) for x in [ema, rsi, atr]) or atr <= 0:
        return None

    # Bear trend: price below EMA100
    if price >= ema:
        return None

    # Current RSI shows bounce
    if rsi < RSI_BOUNCE:
        return None

    # Recent oversold: RSI was < 30 in last 20 bars
    recent_rsi = df["rsi"].iloc[max(0, i-LOOKBACK_BARS):i]
    was_oversold = (recent_rsi < RSI_OVERSOLD).any()
    if not was_oversold:
        return None

    # Volume declining on bounce: last 3 bars volume < 3 bars before that
    if i >= 6:
        recent_vol = df["volume"].iloc[i-3:i].mean()
        prior_vol = df["volume"].iloc[i-6:i-3].mean()
        if prior_vol > 0 and recent_vol >= prior_vol:
            return None  # Volume not declining = real buying, not dead cat

    # Check no existing position
    for p in state.get("positions", []):
        if "BTC" in p.get("symbol", ""):
            return None

    sl = price + atr * SL_ATR_MULT
    tp = price - atr * TP_ATR_MULT

    return {
        "action": "SELL", "direction": "SHORT", "symbol": "BTCUSDC",
        "price": price, "stop_loss": round(sl, 2), "take_profit": round(tp, 2),
        "market_type": "margin", "strategy": "dead_cat_bounce",
        "confidence": 0.7,
        "reason": f"DCB: price={price:.0f}<EMA100={ema:.0f}, RSI={rsi:.0f} (was<30), vol declining",
    }
