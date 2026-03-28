"""Funding / borrow cost model for crypto margin and short positions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


# Historical average daily borrow rates (annualized / 365)
_DEFAULT_BORROW_RATES: Dict[str, float] = {
    "BTC": 0.0002,    # 0.02%/day  (~7.3%/yr)
    "ETH": 0.00024,   # 0.024%/day (~8.8%/yr)
    "SOL": 0.0007,    # 0.07%/day  (~25.6%/yr)
    "AVAX": 0.0006,   # 0.06%/day  (~21.9%/yr)
    "LINK": 0.0005,   # 0.05%/day  (~18.3%/yr)
    "DOGE": 0.0008,   # 0.08%/day  (~29.2%/yr)
    "MATIC": 0.0006,  # 0.06%/day
    "ARB": 0.0007,    # 0.07%/day
    "OP": 0.0007,     # 0.07%/day
    "USDT": 0.0001,   # 0.01%/day  (stablecoin, minimal)
    "USDC": 0.0001,   # 0.01%/day
}


class FundingCostModel:
    """Borrow interest model for crypto margin/short positions.

    Applies hourly interest charges based on historical average
    borrow rates per asset. Rates can be overridden via config.
    """

    def __init__(
        self,
        rate_overrides: Optional[Dict[str, float]] = None,
        default_rate: float = 0.0005,
    ) -> None:
        """Initialize funding cost model.

        Args:
            rate_overrides: Custom daily rates per asset (e.g. {"BTC": 0.0003}).
            default_rate: Fallback daily rate for unknown assets (0.05%/day).
        """
        self._rates: Dict[str, float] = {**_DEFAULT_BORROW_RATES}
        if rate_overrides:
            self._rates.update(rate_overrides)
        self._default_rate = default_rate

    def get_rate(self, asset: str, timestamp: Optional[datetime] = None) -> float:
        """Get daily borrow rate for an asset.

        Args:
            asset: Asset symbol (e.g. "BTC", "ETH").
            timestamp: Optional timestamp (for future rate-regime support).

        Returns:
            Daily borrow rate as a decimal (e.g. 0.0002 = 0.02%).
        """
        return self._rates.get(asset.upper(), self._default_rate)

    def apply_hourly_interest(
        self,
        position: Any,
        timestamp: datetime,
    ) -> float:
        """Calculate one hour of borrow interest for a position.

        Args:
            position: Object with `symbol` (str), `quantity` (float),
                      `avg_price` (float) attributes.
            timestamp: Current simulation timestamp.

        Returns:
            Interest cost in quote currency (positive = cost).
        """
        symbol = getattr(position, "symbol", "").upper()
        # Strip common suffixes (e.g. BTCUSDT -> BTC)
        base_asset = self._extract_base(symbol)

        qty = abs(getattr(position, "quantity", 0))
        avg_price = getattr(position, "avg_price", 0.0)
        notional = qty * avg_price

        daily_rate = self.get_rate(base_asset, timestamp)
        hourly_rate = daily_rate / 24.0

        return notional * hourly_rate

    @staticmethod
    def _extract_base(symbol: str) -> str:
        """Extract base asset from pair symbol.

        Args:
            symbol: e.g. "BTCUSDT", "ETH/USDT", "SOL-PERP".

        Returns:
            Base asset string (e.g. "BTC", "ETH", "SOL").
        """
        # Handle slash-separated
        if "/" in symbol:
            return symbol.split("/")[0].upper()
        # Handle dash-separated (perp markers)
        if "-" in symbol:
            return symbol.split("-")[0].upper()
        # Handle suffixes: USDT, USD, BUSD
        for suffix in ("USDT", "BUSD", "USD", "PERP"):
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                return symbol[: -len(suffix)].upper()
        return symbol.upper()
