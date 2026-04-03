"""
STRAT-005 — BTC Dominance Rotation V2 (Spot Only).

Edge: BTC dominance is cyclical and trends. Capital flows between BTC and
altcoins in predictable waves. V2 improves on the original STRAT-006 by:
  1. Using EMA difference thresholds (0.5%) to avoid whipsaw in dead zones
  2. Including a dynamic "top performer" slot in alt-season allocation
  3. Always holding a USDT cash buffer for rebalance slippage

Signal:
  BTC dom EMA7 > EMA21 + 0.5% → 80% BTC + 20% USDT (BTC season)
  BTC dom EMA7 < EMA21 - 0.5% → 30% ETH + 30% SOL + 20% top_perf + 20% USDT (alt season)
  Dead zone |diff| < 0.5%     → 50% BTC + 30% ETH + 20% USDT (neutral)

Rebalance: weekly (Sunday 00:00 UTC)
Allocation: 10% of crypto capital
Leverage: none (spot only)
"""
from __future__ import annotations

import pandas as pd

STRATEGY_CONFIG = {
    "name": "BTC Dominance Rotation V2",
    "id": "STRAT-005",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "allocation_pct": 0.10,
    "max_leverage": 1,
    "market_type": "spot",
    "timeframe": "1d",
    "frequency": "weekly",
}

# ── Dominance EMA parameters ───────────────────────────────────────────
EMA_FAST = 7
EMA_SLOW = 21
DEAD_ZONE_THRESHOLD = 0.5  # percentage points

# ── Allocation weights per regime ───────────────────────────────────────
# BTC season: 80% BTC, 20% USDT
BTC_SEASON_WEIGHTS = {"BTCUSDT": 0.80, "USDT_CASH": 0.20}

# Alt season: 30% ETH, 30% SOL, 20% top performer, 20% USDT
ALT_SEASON_BASE_WEIGHTS = {"ETHUSDT": 0.30, "SOLUSDT": 0.30, "USDT_CASH": 0.20}
ALT_TOP_PERFORMER_WEIGHT = 0.20

# Neutral (dead zone): 50% BTC, 30% ETH, 20% USDT
NEUTRAL_WEIGHTS = {"BTCUSDT": 0.50, "ETHUSDT": 0.30, "USDT_CASH": 0.20}

# ── Top performer candidates ───────────────────────────────────────────
TOP_PERFORMER_CANDIDATES = [
    "SOLUSDT", "BNBUSDT", "AVAXUSDT", "ADAUSDT",
    "DOTUSDT", "LINKUSDT", "MATICUSDT", "NEARUSDT",
]
TOP_PERFORMER_LOOKBACK_DAYS = 14


class DominanceRegime:
    BTC = "BTC_SEASON"
    ALT = "ALT_SEASON"
    NEUTRAL = "NEUTRAL"


def detect_dominance_regime(dominance_series: pd.Series) -> tuple[str, float]:
    """Detect dominance regime with dead-zone thresholds.

    Args:
        dominance_series: BTC dominance % (e.g., 55.0, 48.3)

    Returns:
        (regime, ema_diff) where ema_diff = ema_fast - ema_slow
    """
    if len(dominance_series) < EMA_SLOW + 5:
        return DominanceRegime.NEUTRAL, 0.0

    ema_fast = dominance_series.ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = dominance_series.ewm(span=EMA_SLOW, adjust=False).mean()

    fast_val = ema_fast.iloc[-1]
    slow_val = ema_slow.iloc[-1]
    diff = fast_val - slow_val

    if diff > DEAD_ZONE_THRESHOLD:
        return DominanceRegime.BTC, float(diff)
    elif diff < -DEAD_ZONE_THRESHOLD:
        return DominanceRegime.ALT, float(diff)
    else:
        return DominanceRegime.NEUTRAL, float(diff)


def find_top_performer(
    returns_data: dict[str, pd.Series],
    lookback_days: int = TOP_PERFORMER_LOOKBACK_DAYS,
) -> str | None:
    """Find the best-performing altcoin over the lookback period.

    Args:
        returns_data: {symbol: Series of daily returns}
        lookback_days: period to evaluate performance

    Returns:
        Symbol of top performer, or None
    """
    performances = {}
    for sym in TOP_PERFORMER_CANDIDATES:
        if sym not in returns_data:
            continue
        ret_series = returns_data[sym]
        if len(ret_series) < lookback_days:
            continue
        cumret = (1 + ret_series.tail(lookback_days)).prod() - 1
        if not pd.isna(cumret):
            performances[sym] = cumret

    if not performances:
        return None

    return max(performances, key=performances.get)


def get_target_weights(
    regime: str,
    top_performer: str | None = None,
) -> dict[str, float]:
    """Get target portfolio weights for the given regime.

    Args:
        regime: DominanceRegime value
        top_performer: symbol of top-performing altcoin (for ALT regime)

    Returns:
        {symbol: weight} dict, always sums to 1.0
    """
    if regime == DominanceRegime.BTC:
        return dict(BTC_SEASON_WEIGHTS)

    elif regime == DominanceRegime.ALT:
        weights = dict(ALT_SEASON_BASE_WEIGHTS)
        if top_performer and top_performer not in weights:
            weights[top_performer] = ALT_TOP_PERFORMER_WEIGHT
        elif top_performer and top_performer in weights:
            # If top performer is already in base (e.g. SOL), increase its weight
            weights[top_performer] += ALT_TOP_PERFORMER_WEIGHT
        else:
            # No top performer found → give extra to ETH
            weights["ETHUSDT"] = weights.get("ETHUSDT", 0) + ALT_TOP_PERFORMER_WEIGHT
        return weights

    else:  # NEUTRAL
        return dict(NEUTRAL_WEIGHTS)


def generate_rebalance_signals(
    target_weights: dict[str, float],
    current_positions: dict[str, float],  # {symbol: current_weight}
    capital: float,
    min_rebalance_pct: float = 0.03,
) -> list[dict]:
    """Generate rebalance orders to match target weights.

    Args:
        target_weights: {symbol: target_weight}
        current_positions: {symbol: current_weight}
        capital: total capital for this strategy
        min_rebalance_pct: minimum weight change to trigger a trade (3%)

    Returns:
        List of signal dicts
    """
    signals = []

    # All symbols involved
    all_symbols = set(list(target_weights.keys()) + list(current_positions.keys()))
    all_symbols.discard("USDT_CASH")  # Not a tradeable position

    for sym in all_symbols:
        target = target_weights.get(sym, 0.0)
        current = current_positions.get(sym, 0.0)
        diff = target - current

        if abs(diff) < min_rebalance_pct:
            continue

        if diff > 0:
            # Need to buy more
            signals.append({
                "action": "BUY",
                "symbol": sym,
                "notional": capital * diff,
                "market_type": "spot",
                "strategy": "btc_dominance_v2",
                "target_weight": round(target, 3),
            })
        elif diff < 0:
            # Need to sell
            signals.append({
                "action": "SELL",
                "symbol": sym,
                "notional": capital * abs(diff),
                "market_type": "spot",
                "strategy": "btc_dominance_v2",
                "target_weight": round(target, 3),
            })

    return signals


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for BTC dominance rotation V2.

    Kwargs:
        is_rebalance_day: True on Sundays at 00:00 UTC
        dominance_series: BTC dominance time series (%)
        returns_data: {symbol: Series of daily returns} for top performer
        current_asset: which asset is being evaluated
    """
    if not kwargs.get("is_rebalance_day", False):
        return None

    dominance_series = kwargs.get("dominance_series")
    if dominance_series is None:
        return None

    regime, ema_diff = detect_dominance_regime(dominance_series)
    positions = state.get("positions", [])
    has_position = len(positions) > 0
    current_asset = kwargs.get("current_asset", "BTCUSDT")

    # Find top performer for alt season
    returns_data = kwargs.get("returns_data", {})
    top_performer = find_top_performer(returns_data) if regime == DominanceRegime.ALT else None

    # Get target weights
    targets = get_target_weights(regime, top_performer)

    # Check if current asset should be held
    target_weight = targets.get(current_asset, 0.0)

    if target_weight > 0 and not has_position:
        return {
            "action": "BUY",
            "pct": STRATEGY_CONFIG["allocation_pct"] * target_weight,
            "market_type": "spot",
            "strategy": "btc_dominance_v2",
            "regime": regime,
            "ema_diff": round(ema_diff, 2),
            "target_weight": round(target_weight, 3),
            "top_performer": top_performer,
        }

    elif target_weight == 0 and has_position:
        return {
            "action": "CLOSE",
            "reason": f"regime_{regime.lower()}_no_allocation",
            "strategy": "btc_dominance_v2",
            "regime": regime,
        }

    return None
