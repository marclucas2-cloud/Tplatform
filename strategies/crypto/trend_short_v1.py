"""
STRAT-009 — Trend Following Short (BEAR regime core strategy).

Edge: In crypto bear markets, BTC/ETH trend down for weeks/months. This
strategy captures sustained downtrends via EMA alignment + pullback entry.
Uses margin isolated short (Binance France legal, no perp).

Signal (1H timeframe):
  SHORT entry:
    price < EMA50(1H)
    EMA50 < EMA200
    pullback to EMA50: distance < 1 ATR (entry at resistance)
  EXIT:
    trailing stop: 2 ATR from entry
    TP: trailing (ride the trend)
    max holding: 14 days

Params: 3 only (EMA50, EMA200, ATR multiplier) — anti-overfitting.

Allocation: 15% of crypto capital
Leverage: 2x max (margin isolated)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Trend Short BTC",
    "id": "STRAT-009",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.15,
    "max_leverage": 2,
    "market_type": "margin",
    "timeframe": "1h",
    "frequency": "1h",
    "max_positions": 1,
}

# 3 params only
EMA_FAST = 50
EMA_SLOW = 200
ATR_PERIOD = 14
SL_ATR_MULT = 2.0
PULLBACK_ATR_MULT = 1.0  # entry zone: price within 1 ATR of EMA50
MAX_HOLDING_BARS = 336    # 14 days * 24h


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA50, EMA200, ATR on 1H OHLCV."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # ATR
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": (df["high"] - df["close"].shift(1)).abs(),
        "lc": (df["low"] - df["close"].shift(1)).abs(),
    })
    df["atr"] = tr.max(axis=1).rolling(ATR_PERIOD).mean()

    return df


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate SHORT signal on EMA alignment + pullback."""
    df_full = kwargs.get("df_full")
    if df_full is None or len(df_full) < EMA_SLOW + 10:
        return None

    df = compute_indicators(df_full)
    i = len(df) - 1
    row = df.iloc[i]

    price = float(row["close"])
    ema_fast = float(row["ema_fast"])
    ema_slow = float(row["ema_slow"])
    atr = float(row["atr"])

    if np.isnan(ema_fast) or np.isnan(ema_slow) or np.isnan(atr) or atr <= 0:
        return None

    # Trend alignment: bearish
    if ema_fast >= ema_slow:
        return None
    if price >= ema_fast:
        return None

    # Pullback: price within 1 ATR of EMA50 (from below)
    distance = ema_fast - price
    if distance > atr * PULLBACK_ATR_MULT:
        return None  # Too far from EMA50, trend already extended

    # Check we're not already positioned
    positions = state.get("positions", [])
    for p in positions:
        sym = p.get("symbol", "")
        if "BTC" in sym and float(p.get("qty", 0)) < 0:
            return None  # Already short BTC

    sl = price + atr * SL_ATR_MULT
    tp = price - atr * 4  # 4 ATR target (trend capture)

    return {
        "action": "SELL",
        "direction": "SHORT",
        "symbol": "BTCUSDC",
        "price": price,
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "market_type": "margin",
        "strategy": "trend_short_v1",
        "confidence": 0.7,
        "reason": f"EMA50={ema_fast:.0f}<EMA200={ema_slow:.0f}, pullback {distance:.0f}<ATR {atr:.0f}",
    }
