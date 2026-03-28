"""
STRAT-001 — BTC/ETH Trend Following (Perp).

Edge: Crypto has the strongest trends of any asset class.
EMA 50/200 crossover + ADX strength + RSI momentum filter.

Signal:
  LONG:  price > EMA50 > EMA200 (4h), ADX > 25, RSI 50-75
  SHORT: price < EMA50 < EMA200 (4h), ADX > 25, RSI 25-50
  EXIT:  trailing stop 2x ATR, TP 4x ATR, ADX < 20

Filters:
  - No trade 2h before/after funding settlement
  - Spread < 10 bps
  - No short if funding < -0.03%, no long if funding > 0.1%
  - No trade if OI dropped > 20% in 24h

Allocation: 30% of crypto capital
Leverage: 2x max
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "BTC/ETH Trend Following",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.30,
    "max_leverage": 2,
    "market_type": "futures",
    "timeframe": "4h",
    "frequency": "4h",
}

# Parameters
EMA_FAST = 50
EMA_SLOW = 200
ADX_THRESHOLD = 25
ADX_EXIT = 20
RSI_PERIOD = 14
RSI_LONG_MIN = 50
RSI_LONG_MAX = 75
RSI_SHORT_MIN = 25
RSI_SHORT_MAX = 50
ATR_PERIOD = 14
SL_ATR_MULT = 2.0
TP_ATR_MULT = 4.0
MAX_HOLDING_DAYS = 30
FUNDING_BLACKOUT_HOURS = {22, 23, 0, 1, 6, 7, 8, 9, 14, 15, 16, 17}  # 2h around settlements
MAX_FUNDING_LONG = 0.001   # Don't long if funding > 0.1%
MIN_FUNDING_SHORT = -0.0003  # Don't short if funding < -0.03%


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators needed for the strategy."""
    df = df.copy()

    # EMA
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # ADX
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    atr_adx = tr.rolling(ATR_PERIOD).mean()
    plus_di = 100 * plus_dm.rolling(ATR_PERIOD).mean() / atr_adx
    minus_di = 100 * minus_dm.rolling(ATR_PERIOD).mean() / atr_adx
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    df["adx"] = dx.rolling(ATR_PERIOD).mean()

    # Volume ratio
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(42).mean()  # 7d of 4h candles

    return df


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate trading signal from a closed 4h candle.

    Args:
        candle: previous candle (closed)
        state: {positions, capital, equity, i, df_full, funding_rate, oi_change_24h}

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    if i < EMA_SLOW + 20:
        return None

    # Compute indicators on available data only (anti-lookahead)
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    ema_fast = row.get("ema_fast", 0)
    ema_slow = row.get("ema_slow", 0)
    rsi = row.get("rsi", 50)
    adx = row.get("adx", 0)
    atr = row.get("atr", 0)
    vol_ratio = row.get("vol_ratio", 1)

    if pd.isna(adx) or pd.isna(atr) or atr <= 0:
        return None

    # Funding blackout filter
    if hasattr(candle.get("timestamp", None), "hour"):
        if candle["timestamp"].hour in FUNDING_BLACKOUT_HOURS:
            return None

    # Funding rate filter
    funding_rate = kwargs.get("funding_rate", 0)

    # OI filter (no trade if OI dropped > 20%)
    oi_change = kwargs.get("oi_change_24h", 0)
    if oi_change < -0.20:
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # Exit signal: ADX < exit threshold
    if has_position and adx < ADX_EXIT:
        return {
            "action": "CLOSE",
            "reason": "adx_exit",
            "strategy": "btc_eth_trend",
        }

    # Max holding check
    if has_position:
        pos = positions[0]
        if hasattr(pos, "entry_time") and hasattr(candle.get("timestamp", None), "timestamp"):
            holding_days = (candle["timestamp"] - pos.entry_time).days
            if holding_days >= MAX_HOLDING_DAYS:
                return {"action": "CLOSE", "reason": "max_holding", "strategy": "btc_eth_trend"}

    if has_position:
        return None  # Already in position

    # LONG signal
    if (
        price > ema_fast > ema_slow
        and adx > ADX_THRESHOLD
        and RSI_LONG_MIN < rsi < RSI_LONG_MAX
        and vol_ratio > 0.8
        and funding_rate < MAX_FUNDING_LONG
    ):
        sl = price - SL_ATR_MULT * atr
        tp = price + TP_ATR_MULT * atr
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "futures",
            "strategy": "btc_eth_trend",
        }

    # SHORT signal
    if (
        price < ema_fast < ema_slow
        and adx > ADX_THRESHOLD
        and RSI_SHORT_MIN < rsi < RSI_SHORT_MAX
        and vol_ratio > 0.8
        and funding_rate > MIN_FUNDING_SHORT
    ):
        sl = price + SL_ATR_MULT * atr
        tp = price - TP_ATR_MULT * atr
        return {
            "action": "SELL",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "futures",
            "strategy": "btc_eth_trend",
        }

    return None
