"""
STRAT-012 — Crypto Monthly Turn-of-Month (Spot Only, Calendar Anomaly).

Edge: BTC and major crypto assets exhibit a statistically significant
"turn-of-month" (ToM) effect. Returns in the last 3 days of the month and
first 3 days of the next month are significantly higher than mid-month
returns. This is driven by:
  1. Monthly salary/pension fund flows (DCA buyers)
  2. Futures/options expiry (last Friday of month, CME BTC futures)
  3. Institutional rebalancing cadence (monthly)
  4. Retail DCA bots concentrated on the 1st of the month

Academic evidence: documented by Caporale & Plastun (2019), confirmed in
multiple follow-up studies for BTC. Effect size ~3-5x mid-month average daily
return, Sharpe improvement of 0.8-1.2 when filtering for ToM only.

Signal:
  IF current date is in ToM window (day 28-31 or day 1-3):
    AND BTC price > EMA20(1d) (basic trend filter)
    AND volume in last 24h > 0.7x 30d avg (not dead market)
    → BUY spot

  EXIT:
    Day 4 of month (window closed)
    OR SL -3.5%
    OR max 7 days holding

This complements STRAT-008 (weekend gap) by adding a monthly cycle overlay.
The two calendar anomalies are uncorrelated (weekend vs month-end).

Allocation: 5% of crypto capital
Leverage: none (spot only)
Frequency: daily check, trades ~6 days/month
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Monthly Turn-of-Month",
    "id": "STRAT-012",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.05,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1d",
    "frequency": "daily",
    "max_positions": 2,
}

# -- Turn-of-Month window definition --------------------------------------
# Last N days of month + first N days of next month
TOM_END_OF_MONTH_DAYS = 3   # day 28+ (in a 31-day month) or last 3 days
TOM_START_OF_MONTH_DAYS = 3  # days 1, 2, 3
TOM_EXIT_DAY = 4             # exit on day 4

# -- Trend filter ----------------------------------------------------------
EMA_TREND_PERIOD = 20  # 20-day EMA

# -- Volume filter ---------------------------------------------------------
VOLUME_AVG_WINDOW = 30  # 30-day volume average
VOLUME_RATIO_MIN = 0.7  # at least 70% of avg (not dead market)

# -- Risk management -------------------------------------------------------
SL_PCT = -0.035          # -3.5% stop loss
MAX_HOLDING_DAYS = 7

# -- Allocation per symbol -------------------------------------------------
WEIGHTS = {"BTCUSDT": 0.65, "ETHUSDT": 0.35}


def is_turn_of_month(date: pd.Timestamp) -> bool:
    """Check if the given date falls in the turn-of-month window.

    The window is: last 3 days of month OR first 3 days of month.

    Args:
        date: timestamp to check

    Returns:
        True if in ToM window
    """
    day = date.day

    # First N days of month
    if day <= TOM_START_OF_MONTH_DAYS:
        return True

    # Last N days of month: use days_in_month to handle Feb, 30-day months
    try:
        days_in_month = date.days_in_month
    except AttributeError:
        # Fallback for older pandas
        import calendar
        days_in_month = calendar.monthrange(date.year, date.month)[1]

    if day > days_in_month - TOM_END_OF_MONTH_DAYS:
        return True

    return False


def is_tom_exit_day(date: pd.Timestamp) -> bool:
    """Check if today is the exit day (day 4 = window just closed).

    Args:
        date: timestamp to check

    Returns:
        True if should exit ToM position
    """
    return date.day == TOM_EXIT_DAY


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA trend and volume ratio for ToM strategy.

    Args:
        df: daily OHLCV DataFrame

    Returns:
        DataFrame with ema_trend and vol_ratio columns
    """
    df = df.copy()

    # EMA trend
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND_PERIOD, adjust=False).mean()

    # Volume ratio vs 30d average
    vol_avg = df["volume"].rolling(VOLUME_AVG_WINDOW).mean()
    df["vol_ratio"] = df["volume"] / vol_avg.replace(0, np.nan)

    return df


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for monthly turn-of-month calendar strategy.

    Args:
        candle: latest closed daily candle
        state: {positions, capital, equity, i}

    Kwargs:
        df_full: full daily DataFrame for indicator computation
        current_asset: which symbol is being evaluated

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    min_bars = max(EMA_TREND_PERIOD, VOLUME_AVG_WINDOW) + 10
    if i < min_bars:
        return None

    # -- Parse timestamp ----------------------------------------------------
    ts = candle.get("timestamp", None)
    if ts is None:
        return None
    try:
        date = pd.Timestamp(ts)
    except Exception:
        return None

    # -- Compute indicators (anti-lookahead) --------------------------------
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    ema_trend = row.get("ema_trend", np.nan)
    vol_ratio = row.get("vol_ratio", np.nan)

    if any(pd.isna(v) for v in [ema_trend, vol_ratio]):
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # -- Exit checks -------------------------------------------------------
    if has_position:
        pos = positions[0]

        # ToM window exit (day 4)
        if is_tom_exit_day(date):
            return {
                "action": "CLOSE",
                "reason": "tom_window_closed_day4",
                "day": date.day,
                "strategy": "monthly_turn_of_month",
            }

        # Stop loss
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            unrealized = (price / pos.entry_price) - 1
            if unrealized < SL_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "stop_loss_3.5pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "monthly_turn_of_month",
                }

        # Max holding
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_days = (pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)).days
            except Exception:
                holding_days = 0
            if holding_days >= MAX_HOLDING_DAYS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_7d",
                    "strategy": "monthly_turn_of_month",
                }

        return None  # Hold during window

    # -- Entry: check if in ToM window -------------------------------------
    if not is_turn_of_month(date):
        return None

    # -- Trend filter: price > EMA20 --------------------------------------
    if price <= ema_trend:
        return None

    # -- Volume filter: not a dead market ----------------------------------
    if vol_ratio < VOLUME_RATIO_MIN:
        return None

    # -- Entry signal (spot long) ------------------------------------------
    current_asset = kwargs.get("current_asset", "BTCUSDT")
    weight = WEIGHTS.get(current_asset, 0.0)
    if weight <= 0:
        return None

    sl = price * (1 + SL_PCT)

    return {
        "action": "BUY",
        "pct": STRATEGY_CONFIG["allocation_pct"] * weight,
        "stop_loss": sl,
        "leverage": 1,
        "market_type": "spot",
        "strategy": "monthly_turn_of_month",
        "calendar_data": {
            "day_of_month": date.day,
            "is_end_of_month": date.day > 25,
            "is_start_of_month": date.day <= TOM_START_OF_MONTH_DAYS,
            "ema_trend": round(ema_trend, 2),
            "vol_ratio": round(vol_ratio, 2),
            "asset_weight": weight,
        },
    }
