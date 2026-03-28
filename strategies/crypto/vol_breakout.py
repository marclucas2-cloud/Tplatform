"""
STRAT-004 — Volatility Breakout (Margin Long/Short).

Edge: Crypto volatility clusters — periods of low volatility (compression)
are reliably followed by explosive moves. When vol_7d/vol_30d drops below
0.5, the market is coiling. We enter on the breakout with confirmation from
volume and ADX, then ride the expansion with a trailing stop.

This is the Binance France version: shorts via margin borrow instead of
perpetual futures.

Signal:
  Detection: vol_7d / vol_30d < 0.5 (compression)
  LONG breakout: close > high_7d + 0.3 * ATR(14)
  SHORT breakout: close < low_7d - 0.3 * ATR(14) (margin borrow)
  Confirmation:
    - volume > 2x avg_7d
    - breakout holds for 2 consecutive 4h candles
    - ADX crosses above 20

Exit:
  - Trailing stop 2x ATR
  - Exit if vol_7d/vol_30d > 1.2 (expansion complete)
  - Max 14 days holding

Allocation: 10% of crypto capital
Leverage: 2x max (margin)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Volatility Breakout",
    "id": "STRAT-004",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 2,
    "market_type": "margin",  # margin for shorts, spot for longs
    "timeframe": "4h",
    "frequency": "4h",
}

# ── Volatility regime detection ─────────────────────────────────────────
VOL_COMPRESSION_RATIO = 0.5   # vol_7d/vol_30d < 0.5 = compression
VOL_EXPANSION_EXIT = 1.2      # vol_7d/vol_30d > 1.2 = expansion done
VOL_PERIOD_SHORT = 7           # days
VOL_PERIOD_LONG = 30           # days

# ── Breakout parameters ─────────────────────────────────────────────────
RANGE_LOOKBACK_DAYS = 7
RANGE_LOOKBACK_CANDLES = 42    # 7 days * 6 candles/day (4h)
BREAKOUT_ATR_BUFFER = 0.3     # close > high_7d + 0.3 * ATR
ATR_PERIOD = 14
VOLUME_SPIKE_MULT = 2.0        # volume > 2x avg_7d
CONFIRMATION_CANDLES = 2       # breakout must hold 2 candles

# ── ADX confirmation ────────────────────────────────────────────────────
ADX_PERIOD = 14
ADX_BREAKOUT_THRESHOLD = 20    # ADX must cross above 20

# ── Exit / risk ─────────────────────────────────────────────────────────
TRAILING_ATR_MULT = 2.0
MAX_HOLDING_DAYS = 14

# ── Borrow rate limit for shorts ────────────────────────────────────────
BORROW_RATE_MAX_SHORT = 0.001  # 0.1%/day


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute volatility ratio, range, ATR, ADX, volume ratio."""
    df = df.copy()

    # Returns and volatility
    returns = df["close"].pct_change()
    # Use rolling window in candle counts (6 candles/day for 4h)
    vol_short_candles = VOL_PERIOD_SHORT * 6
    vol_long_candles = VOL_PERIOD_LONG * 6
    df["vol_short"] = returns.rolling(vol_short_candles).std() * np.sqrt(365 * 6)
    df["vol_long"] = returns.rolling(vol_long_candles).std() * np.sqrt(365 * 6)
    df["vol_ratio"] = df["vol_short"] / df["vol_long"].replace(0, np.nan)

    # 7-day range
    df["range_high"] = df["high"].rolling(RANGE_LOOKBACK_CANDLES).max()
    df["range_low"] = df["low"].rolling(RANGE_LOOKBACK_CANDLES).min()

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

    # Previous ADX for crossover detection
    df["adx_prev"] = df["adx"].shift(1)

    # Volume ratio vs 7d average
    df["vol_avg_7d"] = df["volume"].rolling(RANGE_LOOKBACK_CANDLES).mean()
    df["volume_ratio"] = df["volume"] / df["vol_avg_7d"].replace(0, np.nan)

    return df


def _check_breakout_confirmation(df: pd.DataFrame, direction: str, n_candles: int = CONFIRMATION_CANDLES) -> bool:
    """Check that the breakout held for n consecutive candles.

    Args:
        df: DataFrame with computed indicators (last n+1 rows needed)
        direction: "LONG" or "SHORT"
        n_candles: number of candles the breakout must hold
    """
    if len(df) < n_candles + 1:
        return False

    recent = df.tail(n_candles)

    if direction == "LONG":
        for _, row in recent.iterrows():
            threshold = row.get("range_high", np.inf)
            atr = row.get("atr", 0)
            if pd.isna(threshold) or pd.isna(atr):
                return False
            # Allow some tolerance — price must stay above range_high
            if row["close"] < threshold:
                return False
        return True

    elif direction == "SHORT":
        for _, row in recent.iterrows():
            threshold = row.get("range_low", -np.inf)
            atr = row.get("atr", 0)
            if pd.isna(threshold) or pd.isna(atr):
                return False
            if row["close"] > threshold:
                return False
        return True

    return False


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate volatility breakout signal.

    Args:
        candle: latest closed 4h candle
        state: {positions, capital, equity, i, df_full}

    Kwargs:
        df_full: full 4h DataFrame
        borrow_rate: current daily borrow rate (decimal)

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    min_bars = max(VOL_PERIOD_LONG * 6, ATR_PERIOD * 3, RANGE_LOOKBACK_CANDLES) + 20
    if i < min_bars:
        return None

    # Compute indicators
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    vol_ratio = row.get("vol_ratio", 1.0)
    range_high = row.get("range_high", np.nan)
    range_low = row.get("range_low", np.nan)
    atr = row.get("atr", np.nan)
    adx = row.get("adx", np.nan)
    adx_prev = row.get("adx_prev", np.nan)
    volume_ratio = row.get("volume_ratio", 0)

    if any(pd.isna(v) for v in [vol_ratio, range_high, range_low, atr, adx, volume_ratio]):
        return None
    if atr <= 0:
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # ── Exit checks ─────────────────────────────────────────────────────
    if has_position:
        pos = positions[0]

        # Expansion complete exit
        if vol_ratio > VOL_EXPANSION_EXIT:
            return {
                "action": "CLOSE",
                "reason": "vol_expansion_complete",
                "vol_ratio": round(vol_ratio, 3),
                "strategy": "vol_breakout",
            }

        # Max holding
        ts = candle.get("timestamp", None)
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_days = (pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)).days
            except Exception:
                holding_days = 0
            if holding_days >= MAX_HOLDING_DAYS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_14d",
                    "strategy": "vol_breakout",
                }

        return None  # Trailing stop managed externally

    # ── Pre-condition: volatility compression ───────────────────────────
    if vol_ratio >= VOL_COMPRESSION_RATIO:
        return None  # Not compressed enough

    # ── ADX crossover confirmation ──────────────────────────────────────
    adx_crossing_up = (
        not pd.isna(adx_prev)
        and adx_prev < ADX_BREAKOUT_THRESHOLD
        and adx >= ADX_BREAKOUT_THRESHOLD
    )
    adx_above = adx >= ADX_BREAKOUT_THRESHOLD

    # ── LONG breakout ───────────────────────────────────────────────────
    breakout_long_level = range_high + BREAKOUT_ATR_BUFFER * atr
    if (
        price > breakout_long_level
        and volume_ratio > VOLUME_SPIKE_MULT
        and (adx_crossing_up or adx_above)
    ):
        # Check confirmation (breakout held 2 candles)
        if _check_breakout_confirmation(available, "LONG"):
            trailing = TRAILING_ATR_MULT * atr
            return {
                "action": "BUY",
                "pct": STRATEGY_CONFIG["allocation_pct"],
                "trailing_stop_atr": trailing,
                "stop_loss": round(price * (1 - 2 * atr / price), 2),
                "leverage": 1,  # spot long
                "market_type": "spot",
                "strategy": "vol_breakout",
                "indicators": {
                    "vol_ratio": round(vol_ratio, 3),
                    "range_high": round(range_high, 2),
                    "breakout_level": round(breakout_long_level, 2),
                    "adx": round(adx, 1),
                    "volume_ratio": round(volume_ratio, 2),
                    "atr": round(atr, 2),
                },
            }

    # ── SHORT breakout (margin borrow) ──────────────────────────────────
    borrow_rate = kwargs.get("borrow_rate", 0.0)
    breakout_short_level = range_low - BREAKOUT_ATR_BUFFER * atr
    if (
        price < breakout_short_level
        and volume_ratio > VOLUME_SPIKE_MULT
        and (adx_crossing_up or adx_above)
        and borrow_rate < BORROW_RATE_MAX_SHORT
    ):
        if _check_breakout_confirmation(available, "SHORT"):
            trailing = TRAILING_ATR_MULT * atr
            return {
                "action": "SELL",
                "pct": STRATEGY_CONFIG["allocation_pct"],
                "trailing_stop_atr": trailing,
                "stop_loss": round(price * (1 + 2 * atr / price), 2),
                "leverage": STRATEGY_CONFIG["max_leverage"],
                "market_type": "margin",
                "strategy": "vol_breakout",
                "borrow_rate_daily": borrow_rate,
                "indicators": {
                    "vol_ratio": round(vol_ratio, 3),
                    "range_low": round(range_low, 2),
                    "breakout_level": round(breakout_short_level, 2),
                    "adx": round(adx, 1),
                    "volume_ratio": round(volume_ratio, 2),
                    "atr": round(atr, 2),
                },
            }

    return None
