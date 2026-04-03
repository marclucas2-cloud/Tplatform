"""Execution simulator for BacktesterV2.

Simulates realistic order fills with configurable commission,
slippage, and latency models.
"""

from __future__ import annotations


import numpy as np

from core.backtester_v2.types import BacktestConfig, Bar, Fill, Order


class ExecutionSimulator:
    """Simulates order execution with costs and slippage.

    Args:
        config: Backtest configuration with broker/execution settings.
        rng: Random number generator for reproducible slippage.
    """

    def __init__(
        self, config: BacktestConfig, rng: np.random.Generator
    ) -> None:
        self._config = config
        self._rng = rng

    def simulate_fill(
        self, order: Order, bar: Bar
    ) -> Fill:
        """Simulate filling an order against a bar.

        Args:
            order: The order to fill.
            bar: The current bar (used for price/volume reference).

        Returns:
            A Fill with realistic costs applied.
        """
        broker_cfg = self._config.brokers.get(
            order.broker, self._config.brokers.get("default", {})
        )
        exec_cfg = self._config.execution

        # Base fill price
        if order.order_type == "LIMIT" and order.limit_price is not None:
            # Check if limit would have been hit
            if order.side == "BUY" and order.limit_price < bar.low:
                return self._reject(order, bar, "Limit not reached")
            if order.side == "SELL" and order.limit_price > bar.high:
                return self._reject(order, bar, "Limit not reached")
            base_price = order.limit_price
        else:
            # Market order: fill at open of next bar (approximated by bar close)
            base_price = bar.close

        # Slippage
        slippage_bps = broker_cfg.get("slippage_bps", 2.0)
        slippage_pct = slippage_bps / 10_000
        # Add random component (0 to 1x the base slippage)
        actual_slippage = slippage_pct * (1.0 + self._rng.uniform(0, 0.5))
        if order.side == "BUY":
            fill_price = base_price * (1 + actual_slippage)
        else:
            fill_price = base_price * (1 - actual_slippage)

        # Commission
        comm_per_share = broker_cfg.get("commission_per_share", 0.005)
        commission = comm_per_share * abs(order.quantity)

        # Latency
        latency_ms = exec_cfg.get("latency_ms", 1.0)

        return Fill(
            order=order,
            price=round(fill_price, 6),
            quantity=order.quantity,
            commission=round(commission, 4),
            slippage_bps=round(actual_slippage * 10_000, 2),
            latency_ms=latency_ms,
            timestamp=bar.timestamp,
            rejected=False,
            reason="",
        )

    @staticmethod
    def _reject(order: Order, bar: Bar, reason: str) -> Fill:
        """Create a rejected fill.

        Args:
            order: The rejected order.
            bar: Reference bar for timestamp.
            reason: Rejection reason.

        Returns:
            A Fill with rejected=True and zero quantity.
        """
        return Fill(
            order=order,
            price=0.0,
            quantity=0.0,
            commission=0.0,
            slippage_bps=0.0,
            latency_ms=0.0,
            timestamp=bar.timestamp,
            rejected=True,
            reason=reason,
        )
