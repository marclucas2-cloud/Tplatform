"""
STRAT-003 — BTC Mean Reversion (Spot Only, Long Only).

Edge: In ranging markets (ADX < 20), BTC reverts to its mean with high
probability. RSI oversold + Bollinger Band lower touch is a classic
mean-reversion setup that works well when trends are absent. This strategy
is COMPLEMENTARY to STRAT-001 (dual momentum) — it only fires when
STRAT-001 is inactive (range regime, ADX < 20).

Pre-condition:
  ADX(14, 4h) < 20 → market is ranging, not trending

Entry (1h candles):
  RSI(14, 1h) < 30
  price < BB_lower(20, 2sigma, 1h)
  volume > 0.8x average
  spread < 5 bps
  price > EMA200(1h) — only buy above long-term support

Exit:
  RSI > 60 OR price > BB_mid OR holding > 48h
  SL: -3%

Frequency: ~3-5 trades/week in range, 0 in trend
Allocation: 12% of crypto capital
Leverage: none (spot only)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "BTC Mean Reversion",
    "id": "STRAT-003",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.12,
    "max_leverage": 1,
    "market_type": "spot",  # long only, no margin
    "timeframe": "1h",
    "frequency": "1h",
    "complementary_to": "STRAT-001",  # only active when ADX < 20
}

# ── Pre-condition (4h regime check) ─────────────────────────────────────
ADX_PERIOD = 14
ADX_MAX_RANGE = 20  # must be < 20 to consider this strategy active

# ── Entry parameters (1h) ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_ENTRY = 35  # RSI < 35 (crypto vol wider than equities)
RSI_EXIT = 60   # RSI > 60

BB_PERIOD = 20
BB_STD = 2.0

EMA_LONG = 200  # long-term support filter

VOLUME_RATIO_MIN = 0.8  # vs 24h average (24 candles at 1h)
VOLUME_AVG_WINDOW = 24
SPREAD_MAX_BPS = 5

# ── Exit / risk ─────────────────────────────────────────────────────────
SL_PCT = -0.03  # -3%
MAX_HOLDING_HOURS = 48


def compute_indicators_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Compute RSI, Bollinger Bands, EMA200, volume ratio on 1h data."""
    df = df.copy()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # Bollinger Bands
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * bb_std
    df["bb_lower"] = df["bb_mid"] - BB_STD * bb_std

    # EMA 200
    df["ema_200"] = df["close"].ewm(span=EMA_LONG, adjust=False).mean()

    # Volume ratio
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(VOLUME_AVG_WINDOW).mean()

    return df


def compute_adx_4h(df_4h: pd.DataFrame) -> float:
    """Compute latest ADX value from 4h candles.

    Args:
        df_4h: DataFrame with 4h OHLCV data

    Returns:
        Latest ADX value, or 50.0 (trending) if insufficient data
    """
    if len(df_4h) < ADX_PERIOD * 3:
        return 50.0  # assume trending if not enough data

    high = df_4h["high"]
    low = df_4h["low"]

    tr = pd.concat([
        high - low,
        (high - df_4h["close"].shift()).abs(),
        (low - df_4h["close"].shift()).abs(),
    ], axis=1).max(axis=1)

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_smooth = tr.rolling(ADX_PERIOD).mean()
    plus_di = 100.0 * plus_dm.rolling(ADX_PERIOD).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.rolling(ADX_PERIOD).mean() / atr_smooth.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    adx = dx.rolling(ADX_PERIOD).mean()

    latest = adx.iloc[-1]
    return float(latest) if not pd.isna(latest) else 50.0


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate mean-reversion signal on BTC (spot, long only).

    Args:
        candle: latest closed 1h candle
        state: {positions, capital, equity, i, df_full}

    Kwargs:
        df_full: full 1h DataFrame
        df_4h: 4h DataFrame for ADX regime check
        spread_bps: current bid-ask spread in basis points

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    min_bars = max(EMA_LONG, BB_PERIOD, RSI_PERIOD) + 20
    if i < min_bars:
        return None

    # ── Pre-condition: ADX(14, 4h) < 20 (range regime) ─────────────────
    df_4h = kwargs.get("df_4h")
    if df_4h is not None:
        adx_4h = compute_adx_4h(df_4h)
        if adx_4h >= ADX_MAX_RANGE:
            # Market is trending → STRAT-001 is active, we stay flat
            return None

    # ── Compute 1h indicators ───────────────────────────────────────────
    available = df_full.iloc[:i].copy()
    available = compute_indicators_1h(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    rsi = row.get("rsi", 50.0)
    bb_lower = row.get("bb_lower", price)
    bb_mid = row.get("bb_mid", price)
    ema_200 = row.get("ema_200", 0.0)
    vol_ratio = row.get("vol_ratio", 1.0)

    if any(pd.isna(v) for v in [rsi, bb_lower, bb_mid, ema_200, vol_ratio]):
        return None

    positions = state.get("positions", [])
    has_position = len(positions) > 0

    # ── Exit checks ─────────────────────────────────────────────────────
    if has_position:
        pos = positions[0]

        # RSI exit
        if rsi > RSI_EXIT:
            return {
                "action": "CLOSE",
                "reason": "rsi_exit_above_60",
                "rsi": round(rsi, 1),
                "strategy": "btc_mean_reversion",
            }

        # BB mid exit (mean reversion target reached)
        if price > bb_mid:
            return {
                "action": "CLOSE",
                "reason": "bb_mid_target_reached",
                "strategy": "btc_mean_reversion",
            }

        # Max holding time exit
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
                    "reason": "max_holding_48h",
                    "strategy": "btc_mean_reversion",
                }

        # SL check
        if hasattr(pos, "entry_price") and pos.entry_price > 0:
            unrealized = (price / pos.entry_price) - 1
            if unrealized < SL_PCT:
                return {
                    "action": "CLOSE",
                    "reason": "stop_loss_3pct",
                    "unrealized_pct": round(unrealized, 4),
                    "strategy": "btc_mean_reversion",
                }

        return None

    # ── Entry conditions ────────────────────────────────────────────────
    spread_bps = kwargs.get("spread_bps", 0)
    if spread_bps > SPREAD_MAX_BPS:
        return None

    if (
        rsi < RSI_ENTRY
        and price < bb_lower
        and vol_ratio > VOLUME_RATIO_MIN
        and price > ema_200  # only buy above long-term support
    ):
        sl = price * (1 + SL_PCT)  # -3%
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"],
            "stop_loss": sl,
            "leverage": 1,
            "market_type": "spot",
            "strategy": "btc_mean_reversion",
            "indicators": {
                "rsi": round(rsi, 1),
                "bb_lower": round(bb_lower, 2),
                "bb_mid": round(bb_mid, 2),
                "ema_200": round(ema_200, 2),
                "vol_ratio": round(vol_ratio, 2),
                "spread_bps": spread_bps,
            },
        }

    return None
