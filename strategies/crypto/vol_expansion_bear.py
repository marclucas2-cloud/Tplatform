"""
STRAT-012 — Volatility Expansion Crash Capture (4H).

Edge: Before major crypto moves, volatility compresses (BBands squeeze).
When it expands DOWN with volume, it signals liquidation cascades.
We follow the move (not counter-trade). TF 4H = ~2 trades/month.

Signal:
  SHORT entry:
    BBands width < 20th percentile of last 50 bars (compression)
    Close breaks below lower BB
    Volume > 1.5x 7d avg
  EXIT:
    Trailing stop: 1.5x ATR
    TP: 3x ATR (ride the expansion)
    Max holding: 7 days (42 bars)

3 params: BB period, compression percentile, volume multiplier.
Allocation: 15%
"""
from __future__ import annotations
import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Vol Expansion Bear",
    "id": "STRAT-012",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.15,
    "max_leverage": 2,
    "market_type": "margin",
    "timeframe": "4h",
    "frequency": "4h",
    "max_positions": 1,
}

BB_PERIOD = 20
BB_STD = 2.0
COMPRESSION_PCTL = 20       # BBwidth must be < 20th percentile
VOLUME_MULT = 1.5
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    df = kwargs.get("df_full")
    if df is None or len(df) < BB_PERIOD + 60:
        return None

    df = df.copy()
    df["sma"] = df["close"].rolling(BB_PERIOD).mean()
    df["std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["sma"] + BB_STD * df["std"]
    df["bb_lower"] = df["sma"] - BB_STD * df["std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["sma"]
    df["vol_avg"] = df["volume"].rolling(42).mean()  # 7d at 4H

    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": (df["high"] - df["close"].shift(1)).abs(),
        "lc": (df["low"] - df["close"].shift(1)).abs(),
    })
    df["atr"] = tr.max(axis=1).rolling(ATR_PERIOD).mean()

    i = len(df) - 1
    row = df.iloc[i]
    price = float(row["close"])
    bb_lower = float(row["bb_lower"])
    bb_width = float(row["bb_width"])
    vol = float(row["volume"])
    vol_avg = float(row["vol_avg"])
    atr = float(row["atr"])

    if any(np.isnan(x) for x in [bb_lower, bb_width, vol_avg, atr]) or atr <= 0:
        return None

    # Compression check: current BBwidth in bottom percentile
    recent_widths = df["bb_width"].iloc[i-50:i].dropna()
    if len(recent_widths) < 30:
        return None
    threshold = np.percentile(recent_widths, COMPRESSION_PCTL)
    prev_width = float(df["bb_width"].iloc[i-1]) if i > 0 else bb_width

    # Signal: was compressed, now expanding DOWN
    was_compressed = prev_width <= threshold
    breaks_lower = price < bb_lower
    volume_spike = vol > vol_avg * VOLUME_MULT

    if not (was_compressed and breaks_lower and volume_spike):
        return None

    # Check no existing position
    for p in state.get("positions", []):
        if "BTC" in p.get("symbol", ""):
            return None

    sl = price + atr * SL_ATR_MULT
    tp = price - atr * TP_ATR_MULT

    return {
        "action": "SELL", "direction": "SHORT", "symbol": "BTCUSDC",
        "price": price, "stop_loss": round(sl, 2), "take_profit": round(tp, 2),
        "market_type": "margin", "strategy": "vol_expansion_bear",
        "confidence": 0.8,
        "reason": f"BB squeeze->expansion, width_prev={prev_width:.4f}<={threshold:.4f}, vol={vol/vol_avg:.1f}x",
    }
