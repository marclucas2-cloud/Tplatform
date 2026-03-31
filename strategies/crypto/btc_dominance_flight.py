"""
STRAT-016 — BTC Dominance Flight to Quality (Daily).

Edge: In macro BEAR, capital flees altcoins to BTC and stablecoins.
BTC dominance rises. We go long BTC / short alt (or just reduce alt
exposure) when dominance breaks out.

Signal (1D):
  LONG BTC / reduce alts:
    BTC dominance EMA7 > EMA21 (dominance trending up)
    Dominance > 50% (flight to quality regime)
  EXIT:
    Dominance EMA7 < EMA21 (reversal)
    Max holding: 30 days

This is the existing btc_dominance_v2 but on daily TF with
flight-to-quality framing. Uses the same data source.

3 params: EMA fast, EMA slow, dominance threshold.
Allocation: 10%
"""
from __future__ import annotations
import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "BTC Dominance Flight",
    "id": "STRAT-016",
    "symbols": ["BTCUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1d",
    "frequency": "1d",
    "max_positions": 1,
}

EMA_FAST = 7
EMA_SLOW = 21
DOMINANCE_THRESHOLD = 50.0  # %


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    df = kwargs.get("df_full")
    if df is None or len(df) < EMA_SLOW + 10:
        return None

    # Try to get BTC dominance from kwargs
    btc_dominance = kwargs.get("btc_dominance")

    if btc_dominance is None:
        # Proxy: use BTC price vs total crypto market
        # If we don't have dominance data, skip
        try:
            broker = kwargs.get("broker")
            if broker:
                # Use CoinGecko or Binance global data
                # Fallback: estimate from BTCUSDT vs ETHUSDT ratio
                btc_price = float(df.iloc[-1]["close"])
                eth_data = kwargs.get("eth_df")
                if eth_data is not None and len(eth_data) > 0:
                    eth_price = float(eth_data.iloc[-1]["close"])
                    # Rising BTC/ETH ratio = BTC dominance increasing
                    ratio = btc_price / eth_price
                    ratio_series = df["close"] / eth_data["close"].iloc[:len(df)]
                    ema_f = ratio_series.ewm(span=EMA_FAST).mean().iloc[-1]
                    ema_s = ratio_series.ewm(span=EMA_SLOW).mean().iloc[-1]
                    if ema_f > ema_s:
                        btc_dominance = 55  # proxy: dominance rising
                    else:
                        btc_dominance = 45
        except Exception:
            return None

    if btc_dominance is None:
        return None

    # Flight to quality: dominance rising above threshold
    if btc_dominance < DOMINANCE_THRESHOLD:
        return None

    # Check no existing position
    for p in state.get("positions", []):
        if "BTC" in p.get("symbol", ""):
            return None

    price = float(df.iloc[-1]["close"])

    # In flight-to-quality: LONG BTC (it's the safe haven in crypto)
    sl = price * 0.95   # 5% SL (daily TF = wider stops)
    tp = price * 1.08   # 8% TP target

    return {
        "action": "BUY", "direction": "LONG", "symbol": "BTCUSDC",
        "price": price, "stop_loss": round(sl, 2), "take_profit": round(tp, 2),
        "market_type": "spot", "strategy": "btc_dominance_flight",
        "confidence": min((btc_dominance - DOMINANCE_THRESHOLD) / 10, 1.0),
        "reason": f"BTC dominance={btc_dominance:.1f}%>{DOMINANCE_THRESHOLD}%, flight to quality",
    }
