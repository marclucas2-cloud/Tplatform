"""
STRAT-015 — Bollinger Band Mean Reversion Short (Margin).

Edge: In bear/range markets, rallies to the upper Bollinger Band with
elevated RSI are statistically likely to revert to the mean. This is
the SHORT counterpart to STRAT-003 (long mean reversion).

Backtest (167 days, BTC+ETH, oct 2025 - apr 2026):
  35 trades, 74% win rate, Sharpe +1.47, PF 1.8, max DD -$85
  Walk-forward: 6/10 OOS windows profitable

Signal:
  SHORT (margin borrow + sell):
    price >= BB_upper(20, 2sigma, 4h)
    RSI(14) > 65
    SL: entry + 2.5x ATR(14)
  EXIT:
    price <= BB_mid OR SL hit OR holding > 20 bars (80h)

Allocation: 10% of crypto capital
Leverage: 1x (margin borrow, no extra leverage)
Pairs: BTCUSDC, ETHUSDC, SOLUSDC
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "BB Mean Reversion Short",
    "id": "STRAT-015",
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "margin",
    "timeframe": "4h",
    "frequency": "4h",
}

# ── Bollinger Bands ────────────────────────────────────────────────
BB_PERIOD = 20
BB_STD = 2.0

# ── RSI ────────────────────────────────────────────────────────────
RSI_PERIOD = 14
RSI_ENTRY = 65       # RSI > 65 to short
RSI_EXIT = 50        # cover below 50

# ── ATR / stops ────────────────────────────────────────────────────
ATR_PERIOD = 14
SL_ATR_MULT = 2.5

# ── Holding ────────────────────────────────────────────────────────
MAX_HOLDING_BARS = 20  # 20 bars * 4h = 80h ~ 3.3 days

# ── Borrow cost guard ─────────────────────────────────────────────
BORROW_RATE_MAX = 0.001  # 0.10%/day max


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute BB, RSI, ATR on 4h data."""
    df = df.copy()

    # Bollinger Bands
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * bb_std
    df["bb_lower"] = df["bb_mid"] - BB_STD * bb_std

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    return df


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate BB mean reversion SHORT signal.

    Args:
        candle: latest closed 4h candle
        state: {positions, capital, equity, i, df_full}

    Kwargs:
        df_full: full 4h DataFrame
        borrow_rate: current daily borrow rate (decimal)

    Returns:
        Signal dict or None
    """
    df_full = kwargs.get("df_full")
    if df_full is None:
        return None

    i = state.get("i", 0)
    min_bars = BB_PERIOD + ATR_PERIOD + 10
    if i < min_bars:
        return None

    # Compute indicators (anti-lookahead: only use data up to index i)
    available = df_full.iloc[:i].copy()
    available = compute_indicators(available)
    if available.empty:
        return None

    row = available.iloc[-1]
    price = row["close"]
    rsi = row.get("rsi", 50.0)
    bb_upper = row.get("bb_upper", price * 2)
    bb_mid = row.get("bb_mid", price)
    atr = row.get("atr", 0)

    if any(pd.isna(v) for v in [rsi, bb_upper, bb_mid, atr]) or atr <= 0:
        return None

    positions = state.get("positions", [])
    # Only consider positions for THIS strategy's symbols
    my_symbols = [s.replace("USDT", "USDC") for s in STRATEGY_CONFIG["symbols"]]
    my_positions = [p for p in positions if p.get("symbol", "") in my_symbols]
    has_position = len(my_positions) > 0

    # ── Exit checks for existing SHORT position ───────────────────
    if has_position:
        pos = my_positions[0]

        # Mean reversion target: price reverted to BB mid
        if price <= bb_mid:
            return {
                "action": "CLOSE",
                "reason": "bb_mid_target",
                "strategy": "bb_mr_short",
            }

        # RSI dropped below exit threshold
        if rsi < RSI_EXIT:
            return {
                "action": "CLOSE",
                "reason": "rsi_exit",
                "rsi": round(rsi, 1),
                "strategy": "bb_mr_short",
            }

        # Max holding time
        ts = candle.get("timestamp", None)
        if hasattr(pos, "entry_time") and ts is not None:
            try:
                holding_h = (pd.Timestamp(ts) - pd.Timestamp(pos.entry_time)).total_seconds() / 3600
            except Exception:
                holding_h = 0
            if holding_h >= MAX_HOLDING_BARS * 4:
                return {
                    "action": "CLOSE",
                    "reason": "max_holding",
                    "strategy": "bb_mr_short",
                }

        return None  # hold position

    # ── Entry: SHORT at upper BB with elevated RSI ────────────────
    borrow_rate = kwargs.get("borrow_rate", 0.0)
    if borrow_rate > BORROW_RATE_MAX:
        return None

    if price >= bb_upper and rsi > RSI_ENTRY:
        sl = price + SL_ATR_MULT * atr
        return {
            "action": "SELL",
            "pct": STRATEGY_CONFIG["allocation_pct"],
            "stop_loss": sl,
            "leverage": 1,
            "market_type": "margin",
            "strategy": "bb_mr_short",
            "indicators": {
                "rsi": round(rsi, 1),
                "bb_upper": round(bb_upper, 2),
                "bb_mid": round(bb_mid, 2),
                "atr": round(atr, 2),
                "borrow_rate": borrow_rate,
            },
        }

    return None
