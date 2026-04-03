"""Execution simulator — realistic fills with latency, spread, slippage, impact."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict

from core.backtester_v2.cost_models.base import CostModel
from core.backtester_v2.types import Fill, MarketState, Order


@dataclass(frozen=True)
class LatencyConfig:
    """Broker latency parameters (milliseconds)."""
    mean: float
    std: float
    min_ms: float = 10.0
    max_ms: float = 500.0


LATENCY: Dict[str, LatencyConfig] = {
    "IBKR": LatencyConfig(mean=80.0, std=30.0, min_ms=20.0, max_ms=300.0),
    "BINANCE": LatencyConfig(mean=40.0, std=15.0, min_ms=5.0, max_ms=200.0),
}

BASE_SPREAD_BPS: Dict[str, float] = {
    "FX_MAJOR": 1.0, "FX_CROSS": 2.0,
    "EQUITY_LARGE": 1.5, "EQUITY_US": 1.5, "EQUITY_EU": 2.0,
    "FUTURES_MICRO": 2.0, "FUTURES": 2.0,
    "CRYPTO_BTC": 2.0, "CRYPTO_ETH": 3.0,
    "CRYPTO_ALT_T2": 5.0, "CRYPTO_ALT_T3": 8.0,
}

_DEFAULT_SPREAD_BPS: float = 3.0
_IMPACT_COEFF: float = 0.1  # Almgren-Chriss coefficient


class ExecutionSimulator:
    """Simulates realistic order execution with spread, impact, and latency.

    Models: latency (normal dist per broker), spread (base * vol * liq * time),
    market impact (Almgren-Chriss simplified), limit order logic, margin check.
    """

    def __init__(self, seed: int | None = 42) -> None:
        """Args: seed — random seed for reproducibility."""
        self._rng = random.Random(seed)

    def simulate_fill(self, order: Order, market_state: MarketState,
                      cost_model: CostModel) -> Fill:
        """Simulate execution of an order. Returns Fill (filled or rejected)."""
        # 1. Latency
        broker = getattr(order, "broker", "IBKR").upper()
        latency_ms = self._simulate_latency(broker)

        # 2. Market open check
        if not getattr(market_state, "is_open", True):
            return Fill.rejected(order, reason="market_closed", latency_ms=latency_ms)

        # 3. Margin check (reject if notional > 2x cash)
        mid_price = getattr(market_state, "mid_price", 0.0)
        qty = abs(getattr(order, "quantity", 0))
        notional = qty * mid_price
        cash = getattr(market_state, "available_cash", float("inf"))
        if notional > 2.0 * cash:
            return Fill.rejected(order, reason="insufficient_margin", latency_ms=latency_ms)

        # 4. Spread and impact
        spread_bps = self._calculate_spread(order, market_state)
        impact = self._calculate_impact(order, market_state)
        half_spread = mid_price * (spread_bps / 10_000.0) / 2.0

        # 5. Fill price
        side = getattr(order, "side", "BUY").upper()
        order_type = getattr(order, "order_type", "MARKET").upper()

        if side == "BUY":
            market_fill_price = mid_price + half_spread + impact
        else:
            market_fill_price = mid_price - half_spread - impact

        if order_type == "LIMIT":
            limit_price = getattr(order, "limit_price", None)
            if limit_price is None:
                return Fill.rejected(order, reason="missing_limit_price", latency_ms=latency_ms)
            if side == "BUY" and market_fill_price > limit_price:
                return Fill.rejected(order, reason="limit_not_reached", latency_ms=latency_ms)
            if side == "SELL" and market_fill_price < limit_price:
                return Fill.rejected(order, reason="limit_not_reached", latency_ms=latency_ms)
            fill_price = limit_price
        else:
            fill_price = market_fill_price

        # 6. Commission
        commission = cost_model.calculate_commission(order, fill_price)

        # 7. Build fill
        return Fill(
            order=order, fill_price=round(fill_price, 8), quantity=qty,
            commission=commission, latency_ms=latency_ms,
            spread_bps=spread_bps, impact=impact, side=side,
        )

    def _calculate_spread(self, order: Order, market_state: MarketState) -> float:
        """Calculate effective spread in basis points (base * vol * liq * time)."""
        asset_class = getattr(order, "asset_class", "").upper()
        base_bps = BASE_SPREAD_BPS.get(asset_class, _DEFAULT_SPREAD_BPS)

        # Volatility adjustment: higher vol => wider spread
        volatility = getattr(market_state, "volatility", 0.02)
        vol_adj = max(0.5, min(3.0, volatility / 0.02))

        # Liquidity adjustment: lower ADV => wider spread
        adv = getattr(market_state, "adv", 1e9)
        liq_adj = max(1.0, min(3.0, 1e8 / adv)) if adv > 0 else 3.0

        # Time-of-day adjustment
        hour = getattr(market_state, "hour", 12)
        time_adj = self._time_spread_multiplier(asset_class, hour)

        return base_bps * vol_adj * liq_adj * time_adj

    def _calculate_impact(self, order: Order, market_state: MarketState) -> float:
        """Market impact in price units: sigma * sqrt(Q/ADV) * 0.1 (Almgren-Chriss)."""
        mid_price = getattr(market_state, "mid_price", 0.0)
        volatility = getattr(market_state, "volatility", 0.02)
        adv = getattr(market_state, "adv", 1e9)
        qty = abs(getattr(order, "quantity", 0))

        if adv <= 0 or mid_price <= 0:
            return 0.0

        sigma_price = mid_price * volatility
        participation = qty / adv
        return max(0.0, sigma_price * math.sqrt(participation) * _IMPACT_COEFF)

    def _simulate_latency(self, broker: str) -> int:
        """Simulate network latency (ms) from normal distribution, clipped."""
        config = LATENCY.get(broker, LATENCY["IBKR"])
        raw = self._rng.gauss(config.mean, config.std)
        return int(round(max(config.min_ms, min(config.max_ms, raw))))

    @staticmethod
    def _time_spread_multiplier(asset_class: str, hour: int) -> float:
        """Spread multiplier based on time of day and asset class."""
        if asset_class.startswith("CRYPTO"):
            return 1.5 if 2 <= hour <= 6 else 1.0

        if asset_class.startswith(("EQUITY", "FUTURES")):
            if hour < 9 or hour >= 17:
                return 2.0
            if hour in (9, 16):
                return 1.3
            return 1.0

        if asset_class.startswith("FX"):
            return 1.4 if (hour >= 21 or hour <= 4) else 1.0

        return 1.0
