"""
STRAT-003 — Altcoin Momentum Cross-Sectionnel (Perp).

Edge: Momentum cross-sectionnel documented academically in crypto.
Weekly ranking of top 20 altcoins by BTC-adjusted momentum.
Long top 3, short bottom 3.

Signal (Sunday 00:00 UTC):
  1. Compute 7d return for each altcoin
  2. Subtract BTC 7d return (beta-adjusted)
  3. Rank by residual momentum
  4. LONG top 3, SHORT bottom 3
  5. Rebalance weekly

Filters:
  - Volume 24h > $50M
  - OI > $20M
  - Market cap > $1B
  - No massive unlock events (> 5% supply in 7d)

Allocation: 15% of crypto capital
Leverage: 2x max
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_CONFIG = {
    "name": "Altcoin Momentum Cross-Sectionnel",
    "allocation_pct": 0.15,
    "max_leverage": 2,
    "market_type": "futures",
    "timeframe": "1d",
    "frequency": "weekly",
}

# Universe
UNIVERSE = [
    "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "DOTUSDT",
    "LINKUSDT", "ADAUSDT", "NEARUSDT", "ATOMUSDT",
    "ARBUSDT", "OPUSDT", "DOGEUSDT", "SUIUSDT",
    "INJUSDT", "SEIUSDT", "APTUSDT", "TIAUSDT",
]

# Parameters
LOOKBACK_DAYS = 7
TOP_N = 3
BOTTOM_N = 3
POSITION_PCT = 0.05     # 5% per position
MAX_LEVERAGE = 2
SL_PCT = 0.10           # 10% stop loss per position
MIN_VOLUME_24H = 50_000_000
MIN_OI = 20_000_000

# Stablecoins and wrapped tokens to exclude
EXCLUDED = {"USDTUSDT", "BUSDUSDT", "USDCUSDT", "WBTCUSDT"}


def compute_momentum_ranking(
    returns_7d: dict[str, float],
    btc_return_7d: float,
    volumes: dict[str, float] | None = None,
    open_interests: dict[str, float] | None = None,
) -> list[dict]:
    """Rank altcoins by BTC-adjusted momentum.

    Args:
        returns_7d: {symbol: 7d return} for each altcoin
        btc_return_7d: BTC 7d return (for beta adjustment)
        volumes: optional {symbol: 24h volume} for filtering
        open_interests: optional {symbol: OI} for filtering

    Returns:
        Sorted list of {symbol, raw_return, residual, rank}
    """
    rankings = []
    for symbol, ret in returns_7d.items():
        if symbol in EXCLUDED or symbol == "BTCUSDT":
            continue

        # Volume filter
        if volumes and volumes.get(symbol, 0) < MIN_VOLUME_24H:
            continue

        # OI filter
        if open_interests and open_interests.get(symbol, 0) < MIN_OI:
            continue

        residual = ret - btc_return_7d  # Beta-adjusted
        rankings.append({
            "symbol": symbol,
            "raw_return": round(ret * 100, 2),
            "btc_return": round(btc_return_7d * 100, 2),
            "residual": round(residual * 100, 2),
        })

    rankings.sort(key=lambda x: x["residual"], reverse=True)
    for i, r in enumerate(rankings):
        r["rank"] = i + 1

    return rankings


def generate_rebalance_signals(
    rankings: list[dict],
    current_positions: dict[str, str],  # {symbol: "LONG"/"SHORT"}
    capital: float,
) -> list[dict]:
    """Generate rebalance signals from momentum ranking.

    Args:
        rankings: sorted list from compute_momentum_ranking
        current_positions: current positions {symbol: direction}
        capital: available capital for this strategy

    Returns:
        List of signal dicts
    """
    signals = []

    if len(rankings) < TOP_N + BOTTOM_N:
        return signals

    # Target positions
    longs = {r["symbol"] for r in rankings[:TOP_N]}
    shorts = {r["symbol"] for r in rankings[-BOTTOM_N:]}

    # Close positions not in target
    for symbol, direction in current_positions.items():
        if direction == "LONG" and symbol not in longs:
            signals.append({
                "action": "CLOSE",
                "symbol": symbol,
                "reason": "rebalance_out",
                "strategy": "altcoin_momentum",
            })
        elif direction == "SHORT" and symbol not in shorts:
            signals.append({
                "action": "CLOSE",
                "symbol": symbol,
                "reason": "rebalance_out",
                "strategy": "altcoin_momentum",
            })

    # Open new longs
    position_size = capital * POSITION_PCT
    for symbol in longs:
        if symbol not in current_positions:
            signals.append({
                "action": "BUY",
                "symbol": symbol,
                "pct": POSITION_PCT,
                "leverage": MAX_LEVERAGE,
                "market_type": "futures",
                "strategy": "altcoin_momentum",
            })

    # Open new shorts
    for symbol in shorts:
        if symbol not in current_positions:
            signals.append({
                "action": "SELL",
                "symbol": symbol,
                "pct": POSITION_PCT,
                "leverage": MAX_LEVERAGE,
                "market_type": "futures",
                "strategy": "altcoin_momentum",
            })

    return signals


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal (simplified for single-symbol backtesting).

    For portfolio backtesting, use generate_rebalance_signals instead.

    kwargs:
      - btc_return_7d: BTC 7d return
      - symbol_return_7d: this symbol's 7d return
      - rank: momentum rank (1 = strongest)
      - total_ranked: total symbols ranked
      - is_rebalance_day: True on Sundays
    """
    if not kwargs.get("is_rebalance_day", False):
        return None

    rank = kwargs.get("rank", 0)
    total = kwargs.get("total_ranked", 20)
    positions = state.get("positions", [])

    if rank == 0 or total == 0:
        return None

    has_position = len(positions) > 0

    # Close if not in top/bottom anymore
    if has_position:
        pos = positions[0]
        if pos.is_long and rank > TOP_N:
            return {"action": "CLOSE", "reason": "rank_dropped", "strategy": "altcoin_momentum"}
        if not pos.is_long and rank <= total - BOTTOM_N:
            return {"action": "CLOSE", "reason": "rank_improved", "strategy": "altcoin_momentum"}
        return None

    # New position
    if rank <= TOP_N:
        return {
            "action": "BUY",
            "pct": POSITION_PCT,
            "leverage": MAX_LEVERAGE,
            "market_type": "futures",
            "strategy": "altcoin_momentum",
        }
    elif rank > total - BOTTOM_N:
        return {
            "action": "SELL",
            "pct": POSITION_PCT,
            "leverage": MAX_LEVERAGE,
            "market_type": "futures",
            "strategy": "altcoin_momentum",
        }

    return None
