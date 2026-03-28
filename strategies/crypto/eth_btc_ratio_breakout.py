"""
STRAT-011 — ETH/BTC Ratio Breakout (Spot + Margin, Pairs Trade).

Edge: The ETH/BTC ratio is the most important relative value metric in crypto.
It trends for months at a time and mean-reverts around macro regime changes.
When ETH/BTC breaks out of a 30-day range with volume confirmation, the trend
tends to persist for 2-4 weeks. This is a PAIRS trade: long ETH + short BTC
(or vice versa) — minimal net crypto exposure, pure alpha from the ratio.

The ratio is a cleaner signal than trading either asset individually because
it strips out the correlated "crypto beta" (both move with macro risk) and
isolates the relative performance driven by fundamental factors (ETH burn
rate, L2 activity, institutional rotation, narrative shifts).

Signal:
  ETH/BTC UPSIDE breakout (long ETH, short BTC):
    ratio > upper_30d_range + 0.5 * ratio_ATR(14)
    ratio EMA7 > EMA21
    volume ETH > 1.3x avg_7d
  ETH/BTC DOWNSIDE breakout (long BTC, short ETH):
    ratio < lower_30d_range - 0.5 * ratio_ATR(14)
    ratio EMA7 < EMA21
    volume BTC > 1.3x avg_7d

Exit:
  - Ratio EMA7 crosses back through EMA21 (trend reversal)
  - Trailing stop 2x ratio_ATR
  - Max 21 days holding
  - If either leg's borrow_rate > 0.1%/day, close short leg

Data: Binance ETHBTC pair or computed from ETHUSDT/BTCUSDT
Allocation: 6% of crypto capital (3% each leg)
Leverage: 1.5x max (margin for short leg)
Frequency: 4h
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "ETH/BTC Ratio Breakout",
    "id": "STRAT-011",
    "symbols": ["ETHUSDT", "BTCUSDT"],
    "allocation_pct": 0.06,
    "max_leverage": 1.5,
    "market_type": "margin",  # margin for short leg, spot for long leg
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 2,  # always paired: long one + short other
    "pair_trade": True,
}

# -- Ratio computation ----------------------------------------------------
# Using ETHBTC direct pair or ETHUSDT/BTCUSDT

# -- Range breakout parameters --------------------------------------------
RANGE_LOOKBACK_CANDLES = 180   # 30 days * 6 candles/day (4h)
BREAKOUT_ATR_BUFFER = 0.5     # breakout = range + 0.5 * ratio_ATR
ATR_PERIOD = 14

# -- Trend EMAs on ratio ---------------------------------------------------
EMA_FAST_RATIO = 7
EMA_SLOW_RATIO = 21

# -- Volume confirmation ---------------------------------------------------
VOLUME_AVG_WINDOW = 42  # 7 days of 4h candles
VOLUME_RATIO_MIN = 1.3

# -- Stops -----------------------------------------------------------------
TRAILING_ATR_MULT = 2.0
MAX_HOLDING_DAYS = 21

# -- Borrow rate -----------------------------------------------------------
BORROW_RATE_EMERGENCY = 0.001  # 0.1%/day = close short leg


def compute_ratio_indicators(df_ratio: pd.DataFrame) -> pd.DataFrame:
    """Compute indicators on the ETH/BTC ratio series.

    Args:
        df_ratio: DataFrame with columns [ratio, volume_eth, volume_btc]
                  Index should be datetime, 4h candles

    Returns:
        DataFrame with added indicator columns
    """
    df = df_ratio.copy()

    # Range (30-day high/low of ratio)
    df["range_high"] = df["ratio"].rolling(RANGE_LOOKBACK_CANDLES).max()
    df["range_low"] = df["ratio"].rolling(RANGE_LOOKBACK_CANDLES).min()

    # Ratio ATR (using ratio itself as "price")
    ratio_diff = df["ratio"].diff().abs()
    df["ratio_atr"] = ratio_diff.rolling(ATR_PERIOD).mean()

    # Ratio EMAs
    df["ratio_ema_fast"] = df["ratio"].ewm(span=EMA_FAST_RATIO, adjust=False).mean()
    df["ratio_ema_slow"] = df["ratio"].ewm(span=EMA_SLOW_RATIO, adjust=False).mean()

    # Volume ratios
    if "volume_eth" in df.columns:
        df["eth_vol_ratio"] = (
            df["volume_eth"] / df["volume_eth"].rolling(VOLUME_AVG_WINDOW).mean()
        )
    if "volume_btc" in df.columns:
        df["btc_vol_ratio"] = (
            df["volume_btc"] / df["volume_btc"].rolling(VOLUME_AVG_WINDOW).mean()
        )

    return df


def compute_ratio_from_prices(
    eth_close: pd.Series,
    btc_close: pd.Series,
) -> pd.Series:
    """Compute ETH/BTC ratio from individual USDT prices.

    Args:
        eth_close: ETHUSDT close prices
        btc_close: BTCUSDT close prices

    Returns:
        ETH/BTC ratio series
    """
    return eth_close / btc_close.replace(0, np.nan)


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for ETH/BTC ratio breakout pairs trade.

    Args:
        candle: latest closed 4h candle
        state: {positions, capital, equity, i}

    Kwargs:
        df_ratio: DataFrame with ratio + volume columns
        borrow_rate_eth: current daily ETH borrow rate
        borrow_rate_btc: current daily BTC borrow rate
        trade_direction: existing trade direction ("LONG_ETH" or "LONG_BTC")

    Returns:
        Signal dict or None
    """
    df_ratio = kwargs.get("df_ratio")
    if df_ratio is None:
        return None

    i = state.get("i", 0)
    min_bars = RANGE_LOOKBACK_CANDLES + ATR_PERIOD + 10
    if i < min_bars:
        return None

    # -- Compute indicators (anti-lookahead) --------------------------------
    available = df_ratio.iloc[:i].copy()
    available = compute_ratio_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    ratio = row.get("ratio", np.nan)
    range_high = row.get("range_high", np.nan)
    range_low = row.get("range_low", np.nan)
    ratio_atr = row.get("ratio_atr", np.nan)
    ema_fast = row.get("ratio_ema_fast", np.nan)
    ema_slow = row.get("ratio_ema_slow", np.nan)
    eth_vol_ratio = row.get("eth_vol_ratio", 1.0)
    btc_vol_ratio = row.get("btc_vol_ratio", 1.0)

    key_values = [ratio, range_high, range_low, ratio_atr, ema_fast, ema_slow]
    if any(pd.isna(v) for v in key_values):
        return None
    if ratio_atr <= 0:
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    borrow_rate_eth = kwargs.get("borrow_rate_eth", 0.0)
    borrow_rate_btc = kwargs.get("borrow_rate_btc", 0.0)

    # -- Exit checks for existing position ---------------------------------
    if has_position:
        trade_direction = kwargs.get("trade_direction", "")

        # EMA crossover reversal exit
        if trade_direction == "LONG_ETH" and ema_fast < ema_slow:
            return {
                "action": "CLOSE",
                "reason": "ratio_ema_crossover_reversal",
                "ratio": round(ratio, 6),
                "strategy": "eth_btc_ratio_breakout",
            }
        elif trade_direction == "LONG_BTC" and ema_fast > ema_slow:
            return {
                "action": "CLOSE",
                "reason": "ratio_ema_crossover_reversal",
                "ratio": round(ratio, 6),
                "strategy": "eth_btc_ratio_breakout",
            }

        # Borrow rate emergency (close short leg)
        if trade_direction == "LONG_ETH" and borrow_rate_btc > BORROW_RATE_EMERGENCY:
            return {
                "action": "CLOSE",
                "reason": "borrow_rate_emergency_btc",
                "borrow_rate_btc": borrow_rate_btc,
                "strategy": "eth_btc_ratio_breakout",
            }
        elif trade_direction == "LONG_BTC" and borrow_rate_eth > BORROW_RATE_EMERGENCY:
            return {
                "action": "CLOSE",
                "reason": "borrow_rate_emergency_eth",
                "borrow_rate_eth": borrow_rate_eth,
                "strategy": "eth_btc_ratio_breakout",
            }

        # Max holding
        ts = candle.get("timestamp", None)
        if hasattr(positions[0], "entry_time") and ts is not None:
            try:
                holding_days = (
                    pd.Timestamp(ts) - pd.Timestamp(positions[0].entry_time)
                ).days
            except Exception:
                holding_days = 0
            if holding_days >= MAX_HOLDING_DAYS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_21d",
                    "strategy": "eth_btc_ratio_breakout",
                }

        return None  # Trailing stop managed externally

    # -- No existing position: evaluate breakout ---------------------------

    per_leg_pct = STRATEGY_CONFIG["allocation_pct"] / 2  # 3% each leg

    # UPSIDE breakout: long ETH, short BTC
    breakout_up = range_high + BREAKOUT_ATR_BUFFER * ratio_atr
    if (
        ratio > breakout_up
        and ema_fast > ema_slow
        and not pd.isna(eth_vol_ratio) and eth_vol_ratio > VOLUME_RATIO_MIN
        and borrow_rate_btc < BORROW_RATE_EMERGENCY
    ):
        trailing = TRAILING_ATR_MULT * ratio_atr
        return {
            "action": "PAIR_TRADE",
            "long_symbol": "ETHUSDT",
            "short_symbol": "BTCUSDT",
            "pct_per_leg": per_leg_pct,
            "stop_loss_ratio": ratio - TRAILING_ATR_MULT * ratio_atr,
            "trailing_stop_ratio_atr": trailing,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "margin",
            "strategy": "eth_btc_ratio_breakout",
            "trade_direction": "LONG_ETH",
            "indicators": {
                "ratio": round(ratio, 6),
                "range_high": round(range_high, 6),
                "breakout_level": round(breakout_up, 6),
                "ratio_atr": round(ratio_atr, 6),
                "ema_fast": round(ema_fast, 6),
                "ema_slow": round(ema_slow, 6),
                "eth_vol_ratio": round(eth_vol_ratio, 2),
            },
        }

    # DOWNSIDE breakout: long BTC, short ETH
    breakout_down = range_low - BREAKOUT_ATR_BUFFER * ratio_atr
    if (
        ratio < breakout_down
        and ema_fast < ema_slow
        and not pd.isna(btc_vol_ratio) and btc_vol_ratio > VOLUME_RATIO_MIN
        and borrow_rate_eth < BORROW_RATE_EMERGENCY
    ):
        trailing = TRAILING_ATR_MULT * ratio_atr
        return {
            "action": "PAIR_TRADE",
            "long_symbol": "BTCUSDT",
            "short_symbol": "ETHUSDT",
            "pct_per_leg": per_leg_pct,
            "stop_loss_ratio": ratio + TRAILING_ATR_MULT * ratio_atr,
            "trailing_stop_ratio_atr": trailing,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "margin",
            "strategy": "eth_btc_ratio_breakout",
            "trade_direction": "LONG_BTC",
            "indicators": {
                "ratio": round(ratio, 6),
                "range_low": round(range_low, 6),
                "breakout_level": round(breakout_down, 6),
                "ratio_atr": round(ratio_atr, 6),
                "ema_fast": round(ema_fast, 6),
                "ema_slow": round(ema_slow, 6),
                "btc_vol_ratio": round(btc_vol_ratio, 2),
            },
        }

    return None
