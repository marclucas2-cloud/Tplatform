"""
STRAT-009 — Funding Rate Divergence (Margin + Spot, NO perp).

Edge: Perpetual futures funding rates are observable signals of leveraged
positioning. Extreme positive funding (> +0.05% per 8h) means longs are
overleveraged and paying shorts — historically this precedes mean reversion
(price drops). Extreme negative funding (< -0.05%) means shorts are
overleveraged — precedes price rallies. We READ funding data from Binance
futures API but EXECUTE exclusively on spot/margin (Binance France compliant).

The key insight: funding rate extremes are a contrarian signal because they
reflect crowded positioning. When funding stays extreme for 3+ periods (24h),
the crowded side is likely to get flushed.

Signal:
  LONG (spot buy):
    funding_8h < -0.05% for 3 consecutive periods (24h)
    AND price above EMA100(4h) — not catching a falling knife
    AND volume > 1.5x avg_7d — confirmation of reversal interest
  SHORT (margin borrow):
    funding_8h > +0.05% for 3 consecutive periods (24h)
    AND price below EMA100(4h) — not shorting into strength
    AND borrow_rate < 0.08%/day
  EXIT:
    funding normalizes (crosses zero from extreme side)
    OR trailing stop 2x ATR
    OR max 7 days holding

Data source: Binance Futures API (READ-ONLY) — GET /fapi/v1/fundingRate
Execution: spot/margin only (Binance France legal)

Allocation: 8% of crypto capital (new, added on top of existing 100%)
Leverage: 2x max (margin for shorts)
Frequency: every 8h (aligned with funding settlement)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Funding Rate Divergence",
    "id": "STRAT-009",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.08,
    "max_leverage": 2,
    "market_type": "margin",  # margin for shorts, spot for longs
    "timeframe": "4h",
    "frequency": "8h",  # aligned with funding settlement
    "max_positions": 2,
    "data_source": "binance_futures_readonly",
}

# -- Funding rate thresholds (per 8h period, as decimal) -------------------
FUNDING_EXTREME_POSITIVE = 0.0005   # +0.05% per 8h = longs overleveraged
FUNDING_EXTREME_NEGATIVE = -0.0005  # -0.05% per 8h = shorts overleveraged
CONSECUTIVE_EXTREME_PERIODS = 3     # 3 periods = 24h of extreme funding

# -- Trend filter (anti-knife-catching) ------------------------------------
EMA_TREND = 100  # 100-period EMA on 4h = ~16 days

# -- Volume confirmation ---------------------------------------------------
VOLUME_AVG_WINDOW = 42  # 7 days of 4h candles
VOLUME_RATIO_MIN = 1.5

# -- ATR / stops -----------------------------------------------------------
ATR_PERIOD = 14
TRAILING_ATR_MULT = 2.0
SL_ATR_MULT = 2.5
MAX_HOLDING_DAYS = 7

# -- Borrow rate limit for shorts -----------------------------------------
BORROW_RATE_MAX_SHORT = 0.0008  # 0.08%/day

# -- Funding normalization exit threshold ----------------------------------
FUNDING_NORMALIZED_THRESHOLD = 0.0001  # close to zero = normalized


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA trend, ATR, and volume ratio on 4h OHLCV data."""
    df = df.copy()

    # EMA trend filter
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # Volume ratio vs 7d average
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(VOLUME_AVG_WINDOW).mean()

    return df


def check_funding_extreme(
    funding_history: list[float],
    direction: str,
    n_periods: int = CONSECUTIVE_EXTREME_PERIODS,
) -> bool:
    """Check if funding has been extreme for n consecutive periods.

    Args:
        funding_history: list of recent 8h funding rates (most recent last)
        direction: "LONG" (checking for negative extreme) or "SHORT" (positive)
        n_periods: number of consecutive extreme periods required

    Returns:
        True if conditions met for contrarian entry
    """
    if len(funding_history) < n_periods:
        return False

    recent = funding_history[-n_periods:]

    if direction == "LONG":
        # All recent funding must be extremely negative (shorts overleveraged)
        return all(f < FUNDING_EXTREME_NEGATIVE for f in recent)
    elif direction == "SHORT":
        # All recent funding must be extremely positive (longs overleveraged)
        return all(f > FUNDING_EXTREME_POSITIVE for f in recent)

    return False


def check_funding_normalized(
    current_funding: float,
    entry_direction: str,
) -> bool:
    """Check if funding has normalized (exit signal).

    Args:
        current_funding: latest 8h funding rate
        entry_direction: "LONG" or "SHORT" — the direction we entered

    Returns:
        True if funding has normalized from the extreme that triggered entry
    """
    if entry_direction == "LONG":
        # We went long because funding was extremely negative (shorts crowded)
        # Exit when funding normalizes back above zero
        return current_funding > FUNDING_NORMALIZED_THRESHOLD
    elif entry_direction == "SHORT":
        # We went short because funding was extremely positive (longs crowded)
        # Exit when funding normalizes back below zero
        return current_funding < -FUNDING_NORMALIZED_THRESHOLD

    return False


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal from funding rate divergence.

    Args:
        candle: latest closed 4h candle with OHLCV + timestamp
        state: {positions, capital, equity, i, df_full}

    Kwargs:
        df_full: full 4h DataFrame for indicator computation
        funding_history: list of recent 8h funding rates (decimal, most recent last)
        current_funding: latest 8h funding rate
        borrow_rate: current daily borrow rate for the symbol (decimal)
        entry_direction: direction of existing position ("LONG" or "SHORT")

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    if i < EMA_TREND + ATR_PERIOD + 10:
        return None

    funding_history = kwargs.get("funding_history", [])
    current_funding = kwargs.get("current_funding", 0.0)

    # -- Compute indicators (anti-lookahead: only use data up to index i) --
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    ema_trend = row.get("ema_trend", np.nan)
    atr = row.get("atr", np.nan)
    vol_ratio = row.get("vol_ratio", np.nan)

    if any(pd.isna(v) for v in [ema_trend, atr, vol_ratio]):
        return None
    if atr <= 0:
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # -- Exit checks for existing position ---------------------------------
    if has_position:
        pos = positions[0]
        entry_direction = kwargs.get("entry_direction", "LONG")

        # Funding normalization exit
        if check_funding_normalized(current_funding, entry_direction):
            return {
                "action": "CLOSE",
                "reason": "funding_normalized",
                "current_funding": round(current_funding, 6),
                "strategy": "funding_rate_divergence",
            }

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
                    "reason": "max_holding_7d",
                    "strategy": "funding_rate_divergence",
                }

        return None  # Trailing stop managed externally

    # -- No existing position — evaluate entry -----------------------------

    # LONG signal: funding extremely negative (shorts crowded) + price above trend
    if (
        check_funding_extreme(funding_history, "LONG")
        and price > ema_trend
        and vol_ratio > VOLUME_RATIO_MIN
    ):
        sl = price - SL_ATR_MULT * atr
        trailing = TRAILING_ATR_MULT * atr
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "trailing_stop_atr": trailing,
            "leverage": 1,  # spot long
            "market_type": "spot",
            "strategy": "funding_rate_divergence",
            "indicators": {
                "current_funding": round(current_funding, 6),
                "funding_streak": len(funding_history),
                "ema_trend": round(ema_trend, 2),
                "atr": round(atr, 2),
                "vol_ratio": round(vol_ratio, 2),
            },
        }

    # SHORT signal: funding extremely positive (longs crowded) + price below trend
    borrow_rate = kwargs.get("borrow_rate", 0.0)
    if (
        check_funding_extreme(funding_history, "SHORT")
        and price < ema_trend
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
            "market_type": "margin",
            "strategy": "funding_rate_divergence",
            "borrow_rate_daily": borrow_rate,
            "indicators": {
                "current_funding": round(current_funding, 6),
                "funding_streak": len(funding_history),
                "ema_trend": round(ema_trend, 2),
                "atr": round(atr, 2),
            },
        }

    return None
