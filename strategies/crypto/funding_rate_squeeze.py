"""
STRAT-015 — Funding Rate Squeeze (4H, read-only perp data).

Edge: When funding rate is extremely negative, shorts are overcrowded.
A short squeeze is imminent. We can READ funding data from Binance
(legal in France) and trade SPOT/MARGIN (not perp).

Signal:
  LONG entry:
    Funding rate < -0.05% (8h) = shorts overcrowded
    No breakout below support (price > recent low)
    Volume normal (not capitulation)
  EXIT:
    TP: 2% (squeeze target)
    SL: 1% (tight — if squeeze doesn't happen, exit fast)
    Max holding: 2 days (12 bars at 4H)

3 params: funding threshold, TP%, SL%.
Allocation: 10%

Note: Binance France blocks futures trading but allows reading
futures/funding data via API — this is our informational edge.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Funding Rate Squeeze",
    "id": "STRAT-015",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 1,
}

FUNDING_THRESHOLD = -0.0005  # -0.05% per 8h (very negative)
TP_PCT = 0.02                # 2% take profit
SL_PCT = 0.01                # 1% stop loss
LOOKBACK_LOW = 12            # 2 days of 4H bars for recent low


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    df = kwargs.get("df_full")
    if df is None or len(df) < LOOKBACK_LOW + 5:
        return None

    # Try to get funding rate from kwargs (enriched by worker)
    funding_rate = kwargs.get("funding_rate")
    if funding_rate is None:
        # Try reading from Binance API directly
        try:
            broker = kwargs.get("broker")
            if broker:
                resp = broker._get("/fapi/v1/fundingRate",
                                   {"symbol": "BTCUSDT", "limit": 1})
                if resp and isinstance(resp, list) and len(resp) > 0:
                    funding_rate = float(resp[0].get("fundingRate", 0))
        except Exception:
            pass

    if funding_rate is None:
        return None

    # Funding must be extremely negative
    if funding_rate >= FUNDING_THRESHOLD:
        return None

    i = len(df) - 1
    price = float(df.iloc[i]["close"])

    # Price must be above recent low (not capitulating)
    recent_low = float(df["low"].iloc[max(0, i-LOOKBACK_LOW):i].min())
    if price < recent_low * 1.005:  # Within 0.5% of recent low = still falling
        return None

    # Check no existing position
    for p in state.get("positions", []):
        if "BTC" in p.get("symbol", ""):
            return None

    sl = price * (1 - SL_PCT)
    tp = price * (1 + TP_PCT)

    return {
        "action": "BUY", "direction": "LONG", "symbol": "BTCUSDC",
        "price": price, "stop_loss": round(sl, 2), "take_profit": round(tp, 2),
        "market_type": "spot", "strategy": "funding_rate_squeeze",
        "confidence": min(abs(funding_rate) / 0.001, 1.0),
        "reason": f"Funding={funding_rate:.4%} < {FUNDING_THRESHOLD:.4%}, squeeze setup, price={price:.0f} > low={recent_low:.0f}",
    }
