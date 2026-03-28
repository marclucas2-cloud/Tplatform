"""
STRAT-010 — Stablecoin Supply Flow (Spot Only, Long/Cash).

Edge: Stablecoin market cap changes are a leading indicator of crypto demand.
When USDT+USDC combined market cap increases significantly (>0.5% in 7d), it
signals fresh fiat inflows waiting to be deployed into crypto. Historically
this precedes BTC rallies by 3-7 days. Conversely, stablecoin supply
contraction signals capital exit and precedes drawdowns.

This is a macro-level indicator that captures institutional flow dynamics.
Unlike on-chain whale watching, stablecoin supply is publicly verifiable
and not gameable (minting/burning requires actual USD deposits/withdrawals
from regulated entities like Tether/Circle).

Data source: CoinGecko API (free, no API key needed for basic endpoints)
  GET /api/v3/coins/{id}/market_chart?vs_currency=usd&days=30
  IDs: tether, usd-coin

Signal:
  stablecoin_supply_change_7d > +0.5% → LONG BTC + ETH (spot)
  stablecoin_supply_change_7d < -0.3% → EXIT to USDT (risk-off)
  Dead zone (-0.3% to +0.5%) → hold existing position

  Confirmation filter:
  - BTC price must be above EMA50(1d) for LONG entry
  - Weekly rebalance (Sunday 00:00 UTC)

Exit:
  - Stablecoin supply turns negative (< -0.3%)
  - Price drops below EMA50(1d)
  - SL -4% per position
  - Max 21 days holding

Allocation: 7% of crypto capital
Leverage: none (spot only)
Frequency: weekly (Sunday)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Stablecoin Supply Flow",
    "id": "STRAT-010",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.07,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1d",
    "frequency": "weekly",
    "data_source": "coingecko_free",
}

# -- Supply change thresholds (7-day % change, as decimal) -----------------
SUPPLY_INFLOW_THRESHOLD = 0.005     # +0.5% = fresh inflows
SUPPLY_OUTFLOW_THRESHOLD = -0.003   # -0.3% = capital exit
SUPPLY_LOOKBACK_DAYS = 7

# -- Trend filter ----------------------------------------------------------
EMA_TREND_PERIOD = 50  # 50-day EMA on daily candles

# -- Allocation weights (within strategy's 7%) -----------------------------
WEIGHTS_RISK_ON = {"BTCUSDT": 0.60, "ETHUSDT": 0.40}

# -- Risk management -------------------------------------------------------
SL_PCT = -0.04          # -4% stop loss per position
MAX_HOLDING_DAYS = 21

# -- Supply smoothing (avoid noise from single-day spikes) -----------------
SUPPLY_SMA_WINDOW = 3   # 3-day SMA on supply before computing change


def compute_supply_change(
    supply_series: pd.Series,
    lookback_days: int = SUPPLY_LOOKBACK_DAYS,
    smooth_window: int = SUPPLY_SMA_WINDOW,
) -> float | None:
    """Compute smoothed stablecoin supply change over lookback period.

    Args:
        supply_series: daily total stablecoin market cap (USDT + USDC)
        lookback_days: period to compute change (7d)
        smooth_window: SMA window for noise reduction

    Returns:
        Percentage change as decimal (e.g., 0.005 = +0.5%), or None
    """
    if len(supply_series) < lookback_days + smooth_window:
        return None

    # Smooth to reduce daily noise
    smoothed = supply_series.rolling(smooth_window).mean()
    smoothed = smoothed.dropna()

    if len(smoothed) < lookback_days:
        return None

    current = smoothed.iloc[-1]
    previous = smoothed.iloc[-lookback_days]

    if previous <= 0:
        return None

    return (current / previous) - 1


class SupplyRegime:
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"
    NEUTRAL = "NEUTRAL"


def detect_supply_regime(supply_change: float) -> str:
    """Classify current supply regime.

    Args:
        supply_change: 7d supply change as decimal

    Returns:
        SupplyRegime value
    """
    if supply_change > SUPPLY_INFLOW_THRESHOLD:
        return SupplyRegime.INFLOW
    elif supply_change < SUPPLY_OUTFLOW_THRESHOLD:
        return SupplyRegime.OUTFLOW
    else:
        return SupplyRegime.NEUTRAL


def compute_ema_trend(prices: pd.Series, period: int = EMA_TREND_PERIOD) -> float | None:
    """Compute latest EMA value for trend filter.

    Args:
        prices: daily close prices
        period: EMA period

    Returns:
        Latest EMA value, or None if insufficient data
    """
    if len(prices) < period + 5:
        return None

    ema = prices.ewm(span=period, adjust=False).mean()
    val = ema.iloc[-1]
    return float(val) if not pd.isna(val) else None


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal based on stablecoin supply dynamics.

    Args:
        candle: latest closed daily candle
        state: {positions, capital, equity, i}

    Kwargs:
        is_rebalance_day: True on Sundays at 00:00 UTC
        stablecoin_supply_series: pd.Series of daily total stablecoin mcap
        daily_prices: pd.Series of daily close prices for the current asset
        current_asset: which symbol is being evaluated

    Returns:
        Signal dict or None
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0
    price = candle.get("close", 0)

    if price <= 0:
        return None

    # -- Exit checks for existing position ---------------------------------
    if has_position:
        pos = positions[0]

        # Stop loss check
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            unrealized = (price / pos.entry_price) - 1
            if unrealized < SL_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "stop_loss_4pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "stablecoin_supply_flow",
                }

        # Max holding time
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
                    "strategy": "stablecoin_supply_flow",
                }

        # Supply outflow exit (risk-off)
        supply_series = kwargs.get("stablecoin_supply_series")
        if supply_series is not None:
            supply_change = compute_supply_change(supply_series)
            if supply_change is not None:
                regime = detect_supply_regime(supply_change)
                if regime == SupplyRegime.OUTFLOW:
                    return {
                        "action": "CLOSE",
                        "reason": "stablecoin_outflow_risk_off",
                        "supply_change_7d": round(supply_change, 5),
                        "strategy": "stablecoin_supply_flow",
                    }

        # EMA trend break exit
        daily_prices = kwargs.get("daily_prices")
        if daily_prices is not None:
            ema = compute_ema_trend(daily_prices)
            if ema is not None and price < ema:
                return {
                    "action": "CLOSE",
                    "reason": "below_ema50_trend_break",
                    "ema50": round(ema, 2),
                    "strategy": "stablecoin_supply_flow",
                }

        return None  # Hold

    # -- Only trade on rebalance day ---------------------------------------
    if not kwargs.get("is_rebalance_day", False):
        return None

    # -- Compute supply regime ---------------------------------------------
    supply_series = kwargs.get("stablecoin_supply_series")
    if supply_series is None:
        return None

    supply_change = compute_supply_change(supply_series)
    if supply_change is None:
        return None

    regime = detect_supply_regime(supply_change)

    # Only enter on inflow regime
    if regime != SupplyRegime.INFLOW:
        return None

    # -- Trend filter: price must be above EMA50(1d) -----------------------
    daily_prices = kwargs.get("daily_prices")
    if daily_prices is not None:
        ema = compute_ema_trend(daily_prices)
        if ema is not None and price < ema:
            return None  # Below trend, don't buy

    # -- Entry signal (spot long) ------------------------------------------
    current_asset = kwargs.get("current_asset", "BTCUSDT")
    weight = WEIGHTS_RISK_ON.get(current_asset, 0.0)

    if weight <= 0:
        return None

    sl = price * (1 + SL_PCT)  # -4% from entry

    return {
        "action": "BUY",
        "pct": STRATEGY_CONFIG["allocation_pct"] * weight,
        "stop_loss": sl,
        "leverage": 1,
        "market_type": "spot",
        "strategy": "stablecoin_supply_flow",
        "supply_data": {
            "supply_change_7d_pct": round(supply_change * 100, 3),
            "regime": regime,
            "asset_weight": weight,
        },
    }
