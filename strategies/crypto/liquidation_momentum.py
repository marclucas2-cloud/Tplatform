"""
STRAT-007 — Liquidation Momentum (Margin, Event-Driven).

Edge: Cascade liquidations on futures markets create predictable momentum
on spot/margin. We use READ-ONLY futures data (OI, funding rate) as signals
but execute exclusively on spot/margin — compliant with Binance France
(no perp trading).

When OI drops sharply (>8% in 4h) with extreme volume and a large price
move, forced liquidations are creating a cascade. We wait 30-60 minutes
after peak liquidations for the dust to settle, then enter in the cascade
direction to ride the follow-through.

Signal:
  1. OI drop > 8% in 4h (READ-ONLY futures data)
  2. Volume > 3x avg_7d
  3. Price move > 4% in 4h
  4. Wait 30-60 min after peak liquidations
  5. Enter in cascade direction (margin for shorts)

Exit:
  SL 1.5%, TP 3% (2:1 ratio)
  Max 24h holding
  Max 3 trades/week

Allocation: 10% of crypto capital
Leverage: 3x max (margin) — tight stops justify higher lever
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "Liquidation Momentum",
    "id": "STRAT-007",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 3,
    "market_type": "margin",  # execute on margin, signals from futures data
    "timeframe": "5m",
    "frequency": "5m",
    "max_trades_per_week": 3,
}

# ── Cascade detection thresholds ────────────────────────────────────────
OI_DROP_MIN_PCT = 0.08        # 8% OI drop in 4h
VOLUME_SPIKE_MULT = 3.0       # volume > 3x avg_7d
PRICE_MOVE_MIN_PCT = 0.04     # 4% price move in 4h

# ── Timing: wait after peak liquidations ────────────────────────────────
WAIT_AFTER_PEAK_MIN = 6       # 30 min at 5m candles = 6 candles
WAIT_AFTER_PEAK_MAX = 12      # 60 min = 12 candles

# ── Risk management ─────────────────────────────────────────────────────
SL_PCT = 0.015                # 1.5% stop loss
TP_PCT = 0.03                 # 3% take profit (2:1 ratio)
MAX_HOLDING_HOURS = 24
MAX_TRADES_PER_WEEK = 3

# ── Volume averaging ───────────────────────────────────────────────────
VOLUME_AVG_WINDOW_7D = 2016   # 7 days * 24h * 60/5 = 2016 candles at 5m


def detect_liquidation_cascade(
    oi_change_4h: float,
    price_change_4h: float,
    volume_ratio: float,
) -> tuple[bool, str]:
    """Detect if a liquidation cascade is occurring.

    Uses READ-ONLY futures data for detection but the actual trade
    happens on spot/margin.

    Args:
        oi_change_4h: OI change over 4h as decimal (e.g., -0.10 = -10%)
        price_change_4h: price change over 4h as decimal
        volume_ratio: current volume / 7d avg volume

    Returns:
        (is_cascade, direction) — direction is "LONG" or "SHORT"
    """
    # OI must drop significantly (negative)
    if oi_change_4h > -OI_DROP_MIN_PCT:
        return False, ""

    # Price must have moved significantly
    if abs(price_change_4h) < PRICE_MOVE_MIN_PCT:
        return False, ""

    # Volume must spike
    if volume_ratio < VOLUME_SPIKE_MULT:
        return False, ""

    # Direction: follow the cascade
    # Price dropped + longs liquidated → continue SHORT
    # Price rallied + shorts liquidated → continue LONG
    if price_change_4h < 0:
        return True, "SHORT"
    else:
        return True, "LONG"


def check_cooldown_timing(
    bars_since_peak: int,
) -> bool:
    """Check if enough time has passed since peak liquidations.

    We want to enter 30-60 minutes after the peak, not during it.

    Args:
        bars_since_peak: number of 5m candles since peak liquidation volume

    Returns:
        True if timing is right to enter
    """
    return WAIT_AFTER_PEAK_MIN <= bars_since_peak <= WAIT_AFTER_PEAK_MAX


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for liquidation momentum trades.

    Args:
        candle: latest closed 5m candle
        state: {positions, capital, equity, i}

    Kwargs:
        oi_change_4h: OI change over last 4h (decimal, from futures data)
        price_change_4h: price change over last 4h (decimal)
        volume_ratio: current volume / 7d average
        bars_since_peak: candles since peak liquidation volume
        trades_this_week: number of trades executed this week
        funding_rate: current funding rate (READ-ONLY, informational)

    Returns:
        Signal dict or None
    """
    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # ── Exit checks ─────────────────────────────────────────────────────
    if has_position:
        pos = positions[0]
        price = candle.get("close", 0)

        # Stop loss / Take profit
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            direction = getattr(pos, "direction", 1)
            unrealized = (price / pos.entry_price - 1) * direction

            if unrealized < -SL_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "stop_loss_1.5pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "liquidation_momentum",
                }

            if unrealized > TP_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "take_profit_3pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "liquidation_momentum",
                }

        # Max holding time
        ts = candle.get("timestamp", None)
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_hours = (
                    pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)
                ).total_seconds() / 3600
            except Exception:
                holding_hours = 0
            if holding_hours >= MAX_HOLDING_HOURS:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding_24h",
                    "strategy": "liquidation_momentum",
                }

        return None

    # ── Weekly trade limit ──────────────────────────────────────────────
    trades_this_week = kwargs.get("trades_this_week", 0)
    if trades_this_week >= MAX_TRADES_PER_WEEK:
        return None

    # ── Cascade detection (using READ-ONLY futures data) ────────────────
    oi_change_4h = kwargs.get("oi_change_4h", 0.0)
    price_change_4h = kwargs.get("price_change_4h", 0.0)
    volume_ratio = kwargs.get("volume_ratio", 1.0)

    is_cascade, direction = detect_liquidation_cascade(
        oi_change_4h, price_change_4h, volume_ratio
    )

    if not is_cascade:
        return None

    # ── Timing check: wait 30-60 min after peak ────────────────────────
    bars_since_peak = kwargs.get("bars_since_peak", 0)
    if not check_cooldown_timing(bars_since_peak):
        return None

    # ── Generate entry signal ───────────────────────────────────────────
    price = candle.get("close", 0)
    if price <= 0:
        return None

    funding_rate = kwargs.get("funding_rate", 0.0)

    if direction == "LONG":
        sl = price * (1 - SL_PCT)
        tp = price * (1 + TP_PCT)
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "margin",
            "strategy": "liquidation_momentum",
            "cascade_data": {
                "oi_change_4h": round(oi_change_4h, 4),
                "price_change_4h": round(price_change_4h, 4),
                "volume_ratio": round(volume_ratio, 2),
                "bars_since_peak": bars_since_peak,
                "funding_rate": round(funding_rate, 6),
            },
        }

    else:  # SHORT via margin borrow
        sl = price * (1 + SL_PCT)
        tp = price * (1 - TP_PCT)
        return {
            "action": "SELL",
            "pct": STRATEGY_CONFIG["allocation_pct"] / len(STRATEGY_CONFIG["symbols"]),
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": STRATEGY_CONFIG["max_leverage"],
            "market_type": "margin",
            "strategy": "liquidation_momentum",
            "cascade_data": {
                "oi_change_4h": round(oi_change_4h, 4),
                "price_change_4h": round(price_change_4h, 4),
                "volume_ratio": round(volume_ratio, 2),
                "bars_since_peak": bars_since_peak,
                "funding_rate": round(funding_rate, 6),
            },
        }
