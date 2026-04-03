"""
STRAT-006 — Borrow Rate Carry (Binance Earn, NO directional risk).

Edge: Binance Earn lending rates are structurally positive because
leveraged traders need to borrow. By dynamically allocating between
USDT, BTC, and ETH Earn products based on APY, we capture the highest
available yield without taking directional exposure.

This is the safest strategy in the portfolio — pure yield farming via
Flexible Earn (instant withdrawal). The only risk is platform/custodial
risk (mitigated by Binance France regulation under PSAN).

Signal (every 4h):
  If USDT APY > 8%  → 80% USDT Earn + 20% BTC/ETH Earn
  If USDT APY < 5% AND BTC APY > 2% → 40% BTC + 40% ETH + 20% USDT Earn
  If all APY < 3%   → reduce Earn allocation, transfer to active strategies

Uses Flexible Earn ONLY (instant withdrawal) to maintain liquidity for
other strategies that may need capital.

Allocation: 13% of crypto capital
Leverage: none
"""
from __future__ import annotations

import pandas as pd

STRATEGY_CONFIG = {
    "name": "Borrow Rate Carry",
    "id": "STRAT-006",
    "symbols": ["USDT", "BTC", "ETH"],  # Earn products, not trading pairs
    "allocation_pct": 0.13,
    "max_leverage": 1,
    "market_type": "earn",  # Binance Flexible Earn
    "timeframe": "4h",
    "frequency": "4h",
    "earn_type": "flexible",  # instant withdrawal only
}

# ── APY thresholds (annualized, as decimals) ────────────────────────────
USDT_APY_HIGH = 0.08       # 8% → high-yield USDT mode
USDT_APY_LOW = 0.05        # 5% → below this, diversify to BTC/ETH Earn
BTC_APY_MIN_DIVERSIFY = 0.02  # 2% BTC APY to trigger diversification
ALL_APY_LOW = 0.03         # 3% → all rates are low, reduce Earn

# ── Allocation weights per scenario ─────────────────────────────────────
# High USDT yield
HIGH_USDT_WEIGHTS = {"USDT": 0.80, "BTC": 0.10, "ETH": 0.10}

# Diversified (USDT low, BTC/ETH decent)
DIVERSIFIED_WEIGHTS = {"BTC": 0.40, "ETH": 0.40, "USDT": 0.20}

# Low all rates — minimal Earn, rest available for active strategies
LOW_ALL_WEIGHTS = {"USDT": 0.50}  # only 50% stays in Earn
LOW_ALL_RELEASE_PCT = 0.50  # release 50% to active strats

# ── Rate change thresholds (to avoid excessive rebalancing) ─────────────
MIN_APY_CHANGE_TO_REBALANCE = 0.005  # 0.5% APY change to trigger rebalance
MIN_REBALANCE_INTERVAL_HOURS = 8     # don't rebalance more than every 8h


class EarnScenario:
    HIGH_USDT = "HIGH_USDT_YIELD"
    DIVERSIFIED = "DIVERSIFIED_EARN"
    LOW_ALL = "LOW_ALL_RATES"


def detect_scenario(
    usdt_apy: float,
    btc_apy: float,
    eth_apy: float,
) -> str:
    """Determine which Earn allocation scenario applies.

    Args:
        usdt_apy: annualized USDT Flexible Earn APY (decimal)
        btc_apy: annualized BTC Flexible Earn APY
        eth_apy: annualized ETH Flexible Earn APY

    Returns:
        EarnScenario value
    """
    # Check if all rates are low
    if usdt_apy < ALL_APY_LOW and btc_apy < ALL_APY_LOW and eth_apy < ALL_APY_LOW:
        return EarnScenario.LOW_ALL

    # High USDT yield
    if usdt_apy > USDT_APY_HIGH:
        return EarnScenario.HIGH_USDT

    # USDT low but BTC/ETH decent
    if usdt_apy < USDT_APY_LOW and btc_apy > BTC_APY_MIN_DIVERSIFY:
        return EarnScenario.DIVERSIFIED

    # Default: high USDT (safe default)
    return EarnScenario.HIGH_USDT


def get_earn_weights(scenario: str) -> dict[str, float]:
    """Get target Earn allocation weights for the scenario.

    Returns:
        {asset: weight} — weights may sum to < 1.0 if capital is released
    """
    if scenario == EarnScenario.HIGH_USDT:
        return dict(HIGH_USDT_WEIGHTS)
    elif scenario == EarnScenario.DIVERSIFIED:
        return dict(DIVERSIFIED_WEIGHTS)
    elif scenario == EarnScenario.LOW_ALL:
        return dict(LOW_ALL_WEIGHTS)
    else:
        return {"USDT": 1.0}


def compute_expected_daily_yield(
    weights: dict[str, float],
    usdt_apy: float,
    btc_apy: float,
    eth_apy: float,
    capital: float,
) -> float:
    """Estimate daily yield in USD from Earn allocation.

    Args:
        weights: {asset: weight}
        usdt_apy/btc_apy/eth_apy: annualized APYs
        capital: total capital allocated to Earn

    Returns:
        Expected daily yield in USD
    """
    apy_map = {"USDT": usdt_apy, "BTC": btc_apy, "ETH": eth_apy}
    daily_yield = 0.0
    for asset, weight in weights.items():
        apy = apy_map.get(asset, 0.0)
        daily_yield += capital * weight * apy / 365.0
    return daily_yield


def generate_earn_signals(
    target_weights: dict[str, float],
    current_allocations: dict[str, float],  # {asset: current_weight}
    capital: float,
    scenario: str,
) -> list[dict]:
    """Generate Earn subscription/redemption signals.

    Args:
        target_weights: {asset: target_weight}
        current_allocations: {asset: current_weight}
        capital: total capital
        scenario: current EarnScenario

    Returns:
        List of signal dicts
    """
    signals = []
    all_assets = set(list(target_weights.keys()) + list(current_allocations.keys()))

    for asset in all_assets:
        target = target_weights.get(asset, 0.0)
        current = current_allocations.get(asset, 0.0)
        diff = target - current

        if abs(diff) < 0.02:  # ignore < 2% differences
            continue

        if diff > 0:
            # Subscribe to Earn
            signals.append({
                "action": "EARN_SUBSCRIBE",
                "asset": asset,
                "amount_usd": capital * diff,
                "earn_type": "flexible",
                "strategy": "borrow_rate_carry",
                "scenario": scenario,
                "target_weight": round(target, 3),
            })
        else:
            # Redeem from Earn
            signals.append({
                "action": "EARN_REDEEM",
                "asset": asset,
                "amount_usd": capital * abs(diff),
                "earn_type": "flexible",
                "strategy": "borrow_rate_carry",
                "scenario": scenario,
                "target_weight": round(target, 3),
            })

    # If LOW_ALL scenario, signal capital release
    if scenario == EarnScenario.LOW_ALL:
        released = capital * LOW_ALL_RELEASE_PCT
        signals.append({
            "action": "CAPITAL_RELEASE",
            "amount_usd": released,
            "reason": "low_earn_rates_all_below_3pct",
            "strategy": "borrow_rate_carry",
        })

    return signals


def signal_fn(candle: pd.Series, state: dict, **kwargs) -> dict | None:
    """Generate signal for Earn allocation management.

    This runs every 4 hours. It checks APY rates and rebalances Earn
    subscriptions accordingly.

    Kwargs:
        usdt_apy: current USDT Flexible Earn APY (annualized decimal)
        btc_apy: current BTC Flexible Earn APY
        eth_apy: current ETH Flexible Earn APY
        current_earn_allocations: {asset: weight} of current Earn positions
        last_rebalance_ts: timestamp of last rebalance
        previous_scenario: last scenario string
    """
    usdt_apy = kwargs.get("usdt_apy", 0.05)
    btc_apy = kwargs.get("btc_apy", 0.01)
    eth_apy = kwargs.get("eth_apy", 0.01)

    current_allocations = kwargs.get("current_earn_allocations", {})
    previous_scenario = kwargs.get("previous_scenario")
    last_rebalance_ts = kwargs.get("last_rebalance_ts")
    capital = state.get("capital", 10_000) * STRATEGY_CONFIG["allocation_pct"]

    # ── Detect scenario ─────────────────────────────────────────────────
    scenario = detect_scenario(usdt_apy, btc_apy, eth_apy)
    target_weights = get_earn_weights(scenario)

    # ── Check if rebalance is needed ────────────────────────────────────
    # Skip if scenario hasn't changed and rates haven't moved significantly
    if scenario == previous_scenario and current_allocations:
        # Check if any APY changed enough to warrant rebalance
        significant_change = False
        for asset, weight in target_weights.items():
            current_w = current_allocations.get(asset, 0.0)
            if abs(weight - current_w) > 0.05:  # > 5% weight difference
                significant_change = True
                break
        if not significant_change:
            return None

    # ── Check minimum rebalance interval ────────────────────────────────
    ts = candle.get("timestamp", None)
    if last_rebalance_ts is not None and ts is not None:
        try:
            hours_since = (
                pd.Timestamp(ts) - pd.Timestamp(last_rebalance_ts)
            ).total_seconds() / 3600
            if hours_since < MIN_REBALANCE_INTERVAL_HOURS:
                return None
        except Exception:
            pass

    # ── Generate rebalance signal ───────────────────────────────────────
    expected_daily = compute_expected_daily_yield(
        target_weights, usdt_apy, btc_apy, eth_apy, capital
    )

    return {
        "action": "EARN_REBALANCE",
        "scenario": scenario,
        "target_weights": target_weights,
        "market_type": "earn",
        "strategy": "borrow_rate_carry",
        "rates": {
            "usdt_apy": round(usdt_apy, 4),
            "btc_apy": round(btc_apy, 4),
            "eth_apy": round(eth_apy, 4),
        },
        "expected_daily_yield_usd": round(expected_daily, 2),
        "capital_allocated": round(capital, 2),
    }
