"""
STRAT-010 — Mean Reversion Scalp (high frequency, small size).

Edge: Even in bear markets, BTC/ETH overshoot on short timeframes. RSI
extremes on 15m reliably revert 0.5-1% within hours. Small size + tight
stops keep risk minimal.

Signal (15m timeframe):
  LONG: RSI(14) < 20 AND price > EMA200(15m) * 0.97 (not in free fall)
  SHORT: RSI(14) > 80 AND price < EMA200(15m) * 1.03
  EXIT:
    TP: 0.5-1%
    SL: 0.5%
    Max holding: 4 hours (16 bars)

Params: 3 only (RSI period, RSI thresholds, TP%) — anti-overfitting.

Allocation: 10% of crypto capital
Leverage: 1.5x max (spot preferred, margin for shorts)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


STRATEGY_CONFIG = {
    "name": "MR Scalp BTC",
    "id": "STRAT-010",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1.5,
    "market_type": "spot",  # spot for longs, margin for shorts
    "timeframe": "15m",
    "frequency": "15m",
    "max_positions": 1,
}

RSI_PERIOD = 14
RSI_OVERSOLD = 20
RSI_OVERBOUGHT = 80
EMA_SLOW = 200
TP_PCT = 0.008       # 0.8% take profit
SL_PCT = 0.005       # 0.5% stop loss
MAX_HOLDING_BARS = 16  # 4 hours


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate LONG/SHORT signal on RSI extremes."""
    df_full = kwargs.get("df_full")
    if df_full is None or len(df_full) < EMA_SLOW + 10:
        return None

    df = df_full.copy()
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    i = len(df) - 1
    row = df.iloc[i]

    price = float(row["close"])
    rsi = float(row["rsi"])
    ema = float(row["ema_slow"])

    if np.isnan(rsi) or np.isnan(ema):
        return None

    # Check no existing position
    positions = state.get("positions", [])
    for p in positions:
        if "BTC" in p.get("symbol", ""):
            return None

    # LONG: RSI oversold + not in free fall
    if rsi < RSI_OVERSOLD and price > ema * 0.97:
        sl = price * (1 - SL_PCT)
        tp = price * (1 + TP_PCT)
        return {
            "action": "BUY",
            "direction": "LONG",
            "symbol": "BTCUSDC",
            "price": price,
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "market_type": "spot",
            "strategy": "mr_scalp_v1",
            "confidence": min((RSI_OVERSOLD - rsi) / 10, 1.0),
            "reason": f"RSI={rsi:.1f}<{RSI_OVERSOLD}, price={price:.0f} > 97% EMA200={ema:.0f}",
        }

    # SHORT: RSI overbought + not in breakout
    if rsi > RSI_OVERBOUGHT and price < ema * 1.03:
        sl = price * (1 + SL_PCT)
        tp = price * (1 - TP_PCT)
        return {
            "action": "SELL",
            "direction": "SHORT",
            "symbol": "BTCUSDC",
            "price": price,
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "market_type": "margin",
            "strategy": "mr_scalp_v1",
            "confidence": min((rsi - RSI_OVERBOUGHT) / 10, 1.0),
            "reason": f"RSI={rsi:.1f}>{RSI_OVERBOUGHT}, price={price:.0f} < 103% EMA200={ema:.0f}",
        }

    return None
