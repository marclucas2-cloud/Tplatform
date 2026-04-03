"""IBKR commission model — FX, US/EU equities, micro futures."""

from __future__ import annotations

from core.backtester_v2.cost_models.base import CostModel


class IBKRCostModel(CostModel):
    """Interactive Brokers tiered commission schedule.

    Rate card (as of 2025):
        FX:             $2/trade OR 0.2 bps of notional (whichever larger)
        US Equities:    $0.005/share (min $1, max 1% of trade value)
        EU Equities:    0.05% of trade value (min EUR 3)
        Futures micro:  $0.62/contract (MES, MCL, MNQ, etc.)
    """

    # --- FX ---
    FX_FLAT_FEE: float = 2.0
    FX_BPS_RATE: float = 0.00002  # 0.2 bps commission
    FX_SPREAD_BPS: float = 0.0001  # ~1.0 bps spread (avg major pairs)

    # --- US Equities ---
    US_PER_SHARE: float = 0.005
    US_MIN_COMMISSION: float = 1.0
    US_MAX_PCT: float = 0.01  # 1% of trade value

    # --- EU Equities ---
    EU_PCT_RATE: float = 0.0005  # 0.05%
    EU_MIN_COMMISSION: float = 3.0  # EUR 3 (treated as USD here)

    # --- Futures micro ---
    FUTURES_PER_CONTRACT: float = 0.62

    def calculate_commission(self, order: Order, fill_price: float) -> float:
        """Calculate IBKR commission based on asset class.

        Args:
            order: Order with symbol, quantity, asset_class attributes.
            fill_price: Actual fill price.

        Returns:
            Commission in USD.
        """
        asset_class = getattr(order, "asset_class", "").upper()
        qty = abs(getattr(order, "quantity", 0))
        notional = qty * fill_price

        if asset_class in ("FX", "FX_MAJOR", "FX_CROSS", "FOREX"):
            return self._fx_commission(notional)
        elif asset_class in ("EQUITY_US", "US_EQUITY", "EQUITY_LARGE"):
            return self._us_equity_commission(qty, notional)
        elif asset_class in ("EQUITY_EU", "EU_EQUITY"):
            return self._eu_equity_commission(notional)
        elif asset_class in ("FUTURES", "FUTURES_MICRO"):
            return self._futures_commission(qty)
        else:
            # Fallback: treat as US equity
            return self._us_equity_commission(qty, notional)

    def _fx_commission(self, notional: float) -> float:
        """FX: $2 flat OR 0.2 bps (whichever larger) + spread cost ~1 bps."""
        bps_fee = notional * self.FX_BPS_RATE
        spread_cost = notional * self.FX_SPREAD_BPS
        return max(self.FX_FLAT_FEE, bps_fee) + spread_cost

    def _us_equity_commission(self, qty: float, notional: float) -> float:
        """US equities: $0.005/share, min $1, max 1% of trade."""
        raw = qty * self.US_PER_SHARE
        capped = min(raw, notional * self.US_MAX_PCT)
        return max(capped, self.US_MIN_COMMISSION)

    def _eu_equity_commission(self, notional: float) -> float:
        """EU equities: 0.05% of trade, min EUR 3."""
        return max(notional * self.EU_PCT_RATE, self.EU_MIN_COMMISSION)

    def _futures_commission(self, qty: float) -> float:
        """Micro futures: $0.62/contract."""
        return qty * self.FUTURES_PER_CONTRACT
