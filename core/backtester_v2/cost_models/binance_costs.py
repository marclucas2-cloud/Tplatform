"""Binance commission model — spot/margin with optional BNB discount."""

from __future__ import annotations

from core.backtester_v2.cost_models.base import CostModel


class BinanceCostModel(CostModel):
    """Binance tiered commission schedule.

    Rate card (VIP 0, 2025):
        Spot/Margin maker:  0.10%
        Spot/Margin taker:  0.10%
        BNB discount:       25% off  -> 0.075%
        USDT-M Futures maker: 0.02%
        USDT-M Futures taker: 0.04%
    """

    # Spot / margin
    SPOT_MAKER_RATE: float = 0.0010  # 0.10%
    SPOT_TAKER_RATE: float = 0.0010  # 0.10%
    BNB_DISCOUNT: float = 0.25  # 25% off

    # USDT-M futures
    FUTURES_MAKER_RATE: float = 0.0002  # 0.02%
    FUTURES_TAKER_RATE: float = 0.0004  # 0.04%

    def __init__(self, bnb_discount: bool = False) -> None:
        """Initialize Binance cost model.

        Args:
            bnb_discount: Whether BNB fee discount is active (25% off).
        """
        self.bnb_discount = bnb_discount

    def calculate_commission(self, order: Order, fill_price: float) -> float:
        """Calculate Binance commission.

        Args:
            order: Order with quantity, asset_class, order_type attributes.
            fill_price: Actual fill price.

        Returns:
            Commission in quote currency (usually USDT/USD).
        """
        qty = abs(getattr(order, "quantity", 0))
        notional = qty * fill_price
        asset_class = getattr(order, "asset_class", "").upper()

        if asset_class in ("CRYPTO_FUTURES", "PERP"):
            return self._futures_commission(notional, order)
        else:
            return self._spot_commission(notional, order)

    def _spot_commission(self, notional: float, order: Order) -> float:
        """Spot/margin commission with optional BNB discount."""
        order_type = getattr(order, "order_type", "MARKET").upper()

        if order_type == "LIMIT":
            rate = self.SPOT_MAKER_RATE
        else:
            rate = self.SPOT_TAKER_RATE

        if self.bnb_discount:
            rate *= (1.0 - self.BNB_DISCOUNT)

        return notional * rate

    def _futures_commission(self, notional: float, order: Order) -> float:
        """USDT-M futures commission (no BNB discount on futures)."""
        order_type = getattr(order, "order_type", "MARKET").upper()

        if order_type == "LIMIT":
            return notional * self.FUTURES_MAKER_RATE
        else:
            return notional * self.FUTURES_TAKER_RATE
