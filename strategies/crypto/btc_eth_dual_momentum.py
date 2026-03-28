"""
STRAT-001 — BTC/ETH Dual Momentum (Margin + Spot, NO perp).

Edge: Crypto exhibits the strongest trends of any asset class. BTC and ETH
are sufficiently decorrelated in regimes that we can be long one and short
the other simultaneously. Margin borrow replaces perpetual shorts — more
expensive (borrow interest) but legal in France and avoids funding rate
complexity.

Signal:
  LONG (spot buy):
    close > EMA50(4h), EMA20 > EMA50, ADX > 25, RSI 45-75,
    volume > 1.2x 7d avg, borrow_rate < 0.05%/day (short side cost check)
  SHORT (margin borrow + sell):
    close < EMA50(4h), EMA20 < EMA50, ADX > 25, RSI 25-55,
    borrow_rate < 0.08%/day
  EXIT:
    trailing stop 2x ATR, SL 2.5x ATR, max 21 days holding
    if borrow_rate spikes > 0.1%/day → close shorts immediately

Can be long BTC + short ETH simultaneously (or vice versa).

Allocation: 20% of crypto capital
Leverage: 2x max (margin)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "BTC/ETH Dual Momentum",
    "id": "STRAT-001",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.20,
    "max_leverage": 2,
    "market_type": "margin",  # margin for shorts, spot for longs
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 2,  # can be long BTC + short ETH simultaneously
}

# ── EMA parameters ──────────────────────────────────────────────────────
EMA_FAST = 20
EMA_SLOW = 50

# ── ADX / RSI ───────────────────────────────────────────────────────────
ADX_PERIOD = 14
ADX_THRESHOLD = 25
RSI_PERIOD = 14
RSI_LONG_MIN = 45
RSI_LONG_MAX = 75
RSI_SHORT_MIN = 25
RSI_SHORT_MAX = 55

# ── ATR / stops ─────────────────────────────────────────────────────────
ATR_PERIOD = 14
SL_ATR_MULT = 2.5
TRAILING_ATR_MULT = 2.0
MAX_HOLDING_DAYS = 21

# ── Volume filter ───────────────────────────────────────────────────────
VOLUME_RATIO_MIN = 1.2  # vs 7d average (42 candles at 4h)
VOLUME_AVG_WINDOW = 42  # 7 days of 4h candles

# ── Borrow rate thresholds (daily rate as decimal) ──────────────────────
BORROW_RATE_MAX_LONG_SIDE = 0.0005   # 0.05%/day — cost check for hedge
BORROW_RATE_MAX_SHORT = 0.0008       # 0.08%/day — max to open short
BORROW_RATE_EMERGENCY = 0.001        # 0.10%/day — force close shorts


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA, RSI, ADX, ATR, and volume ratio on 4h OHLCV data."""
    df = df.copy()

    # EMAs
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # ADX
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_smooth = tr.rolling(ADX_PERIOD).mean()
    plus_di = 100.0 * plus_dm.rolling(ADX_PERIOD).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.rolling(ADX_PERIOD).mean() / atr_smooth.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    df["adx"] = dx.rolling(ADX_PERIOD).mean()

    # Volume ratio vs 7d average
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(VOLUME_AVG_WINDOW).mean()

    return df


def _check_borrow_emergency(state: dict, borrow_rate: float) -> dict | None:
    """If borrow rate spikes above emergency threshold, close all shorts."""
    positions = state.get("positions", [])
    for pos in positions:
        direction = getattr(pos, "direction", None)
        if direction is not None and direction < 0:  # short position
            if borrow_rate > BORROW_RATE_EMERGENCY:
                return {
                    "action": "CLOSE",
                    "reason": "borrow_rate_emergency",
                    "borrow_rate_daily": borrow_rate,
                    "strategy": "btc_eth_dual_momentum",
                }
    return None


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate trading signal from a closed 4h candle.

    Args:
        candle: latest closed candle with OHLCV + timestamp
        state: {positions, capital, equity, i, df_full}

    Kwargs:
        df_full: full DataFrame for indicator computation
        borrow_rate: current daily borrow rate for the symbol (decimal)
        symbol: which symbol is being evaluated (BTCUSDT or ETHUSDT)

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    if i < EMA_SLOW + ADX_PERIOD + 10:
        return None

    borrow_rate = kwargs.get("borrow_rate", 0.0)

    # Emergency borrow rate check on existing shorts
    emergency = _check_borrow_emergency(state, borrow_rate)
    if emergency is not None:
        return emergency

    # Compute indicators (anti-lookahead: only use data up to index i)
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    ema_fast = row.get("ema_fast", np.nan)
    ema_slow = row.get("ema_slow", np.nan)
    rsi = row.get("rsi", np.nan)
    adx = row.get("adx", np.nan)
    atr = row.get("atr", np.nan)
    vol_ratio = row.get("vol_ratio", np.nan)

    if any(pd.isna(v) for v in [ema_fast, ema_slow, rsi, adx, atr, vol_ratio]):
        return None
    if atr <= 0:
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # ── Exit checks for existing position ───────────────────────────────
    if has_position:
        pos = positions[0]

        # Max holding check
        ts = candle.get("timestamp", None)
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_days = (pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)).days
            except Exception:
                holding_days = 0
            if holding_days >= MAX_HOLDING_DAYS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_21d",
                    "strategy": "btc_eth_dual_momentum",
                }

        return None  # Already in position, trailing stop managed externally

    # ── No existing position — evaluate entry ───────────────────────────

    # LONG signal (spot buy)
    if (
        price > ema_slow
        and ema_fast > ema_slow
        and adx > ADX_THRESHOLD
        and RSI_LONG_MIN < rsi < RSI_LONG_MAX
        and vol_ratio > VOLUME_RATIO_MIN
    ):
        sl = price - SL_ATR_MULT * atr
        trailing = TRAILING_ATR_MULT * atr
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "trailing_stop_atr": trailing,
            "leverage": 1,  # spot long = no leverage
            "market_type": "spot",
            "strategy": "btc_eth_dual_momentum",
            "indicators": {
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "adx": round(adx, 1),
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
                "vol_ratio": round(vol_ratio, 2),
            },
        }

    # SHORT signal (margin borrow + sell)
    if (
        price < ema_slow
        and ema_fast < ema_slow
        and adx > ADX_THRESHOLD
        and RSI_SHORT_MIN < rsi < RSI_SHORT_MAX
        and borrow_rate < BORROW_RATE_MAX_SHORT
    ):
        sl = price + SL_ATR_MULT * atr
        trailing = TRAILING_ATR_MULT * atr
        return {
            "action": "SELL",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "trailing_stop_atr": trailing,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "margin",  # borrow + sell
            "strategy": "btc_eth_dual_momentum",
            "borrow_rate_daily": borrow_rate,
            "indicators": {
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "adx": round(adx, 1),
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
            },
        }

    return None
