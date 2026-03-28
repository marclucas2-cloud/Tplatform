"""
STRAT-005 — Volatility Regime Switch (BTC, Options-Free).

Edge: BTC alternates between high and low volatility regimes.
In low vol → synthetic straddle (breakout) to capture the move.
In high vol → mean reversion on dips/rallies.

Regime Detection:
  - vol_7d < 0.5 * vol_30d → LOW VOL (compression)
  - vol_7d > 1.5 * vol_30d → HIGH VOL (expansion)
  - else → NORMAL (no trade)

Low Vol: breakout entries above/below range
High Vol: buy -5% dips, sell +5% rallies, wide stops

Allocation: 10% of crypto capital
Leverage: 1x (volatility IS the exposure)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Volatility Regime Switch",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "futures",
    "timeframe": "1d",
    "frequency": "daily",
}

# Regime detection
VOL_LOW_RATIO = 0.5     # vol_7d < 0.5 * vol_30d
VOL_HIGH_RATIO = 1.5    # vol_7d > 1.5 * vol_30d
VOL_PERIOD_SHORT = 7
VOL_PERIOD_LONG = 30

# Low vol parameters (breakout)
BREAKOUT_LOOKBACK = 10       # 10-day range
BREAKOUT_ATR_MULT = 1.5      # Entry at range ± 1.5 ATR
BREAKOUT_SL_ATR = 1.0        # Stop loss
BREAKOUT_TP_ATR = 3.0        # Take profit

# High vol parameters (mean reversion)
MR_DIP_PCT = -0.05          # Buy at -5%
MR_RALLY_PCT = 0.05         # Sell at +5%
MR_SL_ATR = 3.0             # Wide stop (high vol)
MR_TP_ATR = 2.0             # Tighter TP

# Common
ATR_PERIOD = 14
MAX_HOLDING_DAYS = 14


class VolRegime:
    LOW = "LOW_VOL"
    HIGH = "HIGH_VOL"
    NORMAL = "NORMAL"


def detect_regime(df: pd.DataFrame) -> str:
    """Detect volatility regime from daily data.

    Args:
        df: DataFrame with 'close' column (daily)

    Returns:
        VolRegime.LOW, VolRegime.HIGH, or VolRegime.NORMAL
    """
    if len(df) < VOL_PERIOD_LONG + 5:
        return VolRegime.NORMAL

    returns = df["close"].pct_change()
    vol_short = returns.tail(VOL_PERIOD_SHORT).std() * np.sqrt(365)
    vol_long = returns.tail(VOL_PERIOD_LONG).std() * np.sqrt(365)

    if vol_long == 0:
        return VolRegime.NORMAL

    ratio = vol_short / vol_long

    if ratio < VOL_LOW_RATIO:
        return VolRegime.LOW
    elif ratio > VOL_HIGH_RATIO:
        return VolRegime.HIGH
    else:
        return VolRegime.NORMAL


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute volatility regime indicators."""
    df = df.copy()

    returns = df["close"].pct_change()
    df["vol_7d"] = returns.rolling(VOL_PERIOD_SHORT).std() * np.sqrt(365)
    df["vol_30d"] = returns.rolling(VOL_PERIOD_LONG).std() * np.sqrt(365)
    df["vol_ratio"] = df["vol_7d"] / df["vol_30d"].replace(0, np.nan)

    # Range
    df["range_high"] = df["high"].rolling(BREAKOUT_LOOKBACK).max()
    df["range_low"] = df["low"].rolling(BREAKOUT_LOOKBACK).min()

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # Returns for mean reversion
    df["ret_1d"] = returns
    df["ret_3d"] = df["close"].pct_change(3)

    # Regime
    df["regime"] = df.apply(
        lambda row: (
            VolRegime.LOW if row.get("vol_ratio", 1) < VOL_LOW_RATIO
            else VolRegime.HIGH if row.get("vol_ratio", 1) > VOL_HIGH_RATIO
            else VolRegime.NORMAL
        ),
        axis=1,
    )

    return df


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal based on volatility regime.

    kwargs:
      - df_full: full DataFrame for indicator computation
      - regime: current regime (optional, computed if not provided)
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0

    df_full = kwargs.get("df_full")
    i = state.get("i", 0)

    if df_full is None or i < VOL_PERIOD_LONG + ATR_PERIOD:
        return None

    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    row = available.iloc[-1]

    regime = row.get("regime", VolRegime.NORMAL)
    price = row["close"]
    atr = row.get("atr", 0)

    if pd.isna(atr) or atr <= 0:
        return None

    # Exit check
    if has_position:
        pos = positions[0]
        if hasattr(pos, "entry_time") and hasattr(candle.get("timestamp", None), "timestamp"):
            holding_days = (candle["timestamp"] - pos.entry_time).days
            if holding_days >= MAX_HOLDING_DAYS:
                return {"action": "CLOSE", "reason": "max_holding", "strategy": "vol_regime"}

        # Exit if regime changed to NORMAL
        if regime == VolRegime.NORMAL:
            return {"action": "CLOSE", "reason": "regime_normal", "strategy": "vol_regime"}
        return None

    # No trade in NORMAL regime
    if regime == VolRegime.NORMAL:
        return None

    # LOW VOL: breakout strategy
    if regime == VolRegime.LOW:
        range_high = row.get("range_high", price)
        range_low = row.get("range_low", price)

        # Breakout above range
        if price > range_high:
            return {
                "action": "BUY",
                "pct": STRATEGY_CONFIG["allocation_pct"],
                "stop_loss": price - BREAKOUT_SL_ATR * atr,
                "take_profit": price + BREAKOUT_TP_ATR * atr,
                "leverage": 1,
                "market_type": "futures",
                "strategy": "vol_regime",
            }

        # Breakout below range
        if price < range_low:
            return {
                "action": "SELL",
                "pct": STRATEGY_CONFIG["allocation_pct"],
                "stop_loss": price + BREAKOUT_SL_ATR * atr,
                "take_profit": price - BREAKOUT_TP_ATR * atr,
                "leverage": 1,
                "market_type": "futures",
                "strategy": "vol_regime",
            }

    # HIGH VOL: mean reversion
    if regime == VolRegime.HIGH:
        ret_3d = row.get("ret_3d", 0)

        # Buy the dip
        if ret_3d is not None and not pd.isna(ret_3d) and ret_3d < MR_DIP_PCT:
            return {
                "action": "BUY",
                "pct": STRATEGY_CONFIG["allocation_pct"] * 0.5,  # Smaller size in high vol
                "stop_loss": price - MR_SL_ATR * atr,
                "take_profit": price + MR_TP_ATR * atr,
                "leverage": 1,
                "market_type": "futures",
                "strategy": "vol_regime",
            }

        # Sell the rally
        if ret_3d is not None and not pd.isna(ret_3d) and ret_3d > MR_RALLY_PCT:
            return {
                "action": "SELL",
                "pct": STRATEGY_CONFIG["allocation_pct"] * 0.5,
                "stop_loss": price + MR_SL_ATR * atr,
                "take_profit": price - MR_TP_ATR * atr,
                "leverage": 1,
                "market_type": "futures",
                "strategy": "vol_regime",
            }

    return None
