"""U6-01+02: Global Portfolio Sizer — size on $45K total, not $10K per broker.

Old: Signal "BTC momentum" → Kelly on $10K Binance → $125 → SKIP
New: Signal "BTC momentum" → Kelly on $45K global → $562 → execute on Binance

The position_size is calculated on total NAV.
Execution happens on the appropriate broker.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("alloc.global_sizer")


@dataclass
class GlobalSizingResult:
    """Result of global sizing calculation."""
    strategy: str
    raw_size_global: float       # Size based on $45K
    broker: str
    broker_equity: float
    capped_size: float           # After broker equity cap
    kelly_fraction: float
    regime_multiplier: float
    final_size: float
    capped: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "raw_size_global": round(self.raw_size_global, 2),
            "broker": self.broker,
            "broker_equity": round(self.broker_equity, 2),
            "capped_size": round(self.capped_size, 2),
            "final_size": round(self.final_size, 2),
            "capped": self.capped,
        }


# Strategy → broker mapping
STRATEGY_BROKER_MAP = {
    # Crypto → Binance
    "btc_eth_dual_momentum": "binance",
    "margin_mean_reversion": "binance",
    "altcoin_momentum": "binance",
    "vol_breakout": "binance",
    "btc_dom_rotation": "binance",
    "borrow_carry": "binance",
    "liquidation_momentum": "binance",
    "weekend_gap": "binance",
    # FX → IBKR
    "fx_carry_vs": "ibkr",
    "fx_carry_audjpy": "ibkr",
    "fx_carry_eurjpy": "ibkr",
    "fx_carry_usdjpy": "ibkr",
    "fx_vol_scaling": "ibkr",
    # EU → IBKR
    "eu_gap_open": "ibkr",
    "bce_momentum": "ibkr",
    "brent_lag": "ibkr",
    "auto_sector_german": "ibkr",
    # US → Alpaca
    "dow_seasonal": "alpaca",
    "vix_expansion_short": "alpaca",
    "cross_asset_momentum": "alpaca",
}


class GlobalPortfolioSizer:
    """Sizes positions based on total portfolio NAV, not per-broker.

    Usage:
        sizer = GlobalPortfolioSizer(
            equity_by_broker={"binance": 10000, "ibkr": 10000, "alpaca": 30000},
        )
        result = sizer.size(
            strategy="btc_eth_dual_momentum",
            allocation_pct=0.15,
            kelly_fraction=0.25,
            regime_multiplier=0.8,
        )
        print(result.final_size)  # $1,350 (on $45K) instead of $300 (on $10K)
    """

    def __init__(
        self,
        equity_by_broker: Dict[str, float] = None,
        max_broker_exposure_pct: float = 0.80,
    ):
        self._equity = equity_by_broker or {}
        self._max_broker_pct = max_broker_exposure_pct

    def update_equity(self, equity_by_broker: Dict[str, float]):
        self._equity = equity_by_broker

    @property
    def nav_total(self) -> float:
        return sum(self._equity.values())

    def size(
        self,
        strategy: str,
        allocation_pct: float,
        kelly_fraction: float = 0.25,
        regime_multiplier: float = 1.0,
        broker_override: str = None,
    ) -> GlobalSizingResult:
        """Size a position based on global NAV.

        Args:
            strategy: Strategy name
            allocation_pct: Target allocation (e.g., 0.15 for 15%)
            kelly_fraction: Kelly fraction (e.g., 0.25 for 1/4 Kelly)
            regime_multiplier: Regime adjustment (0.0 to 1.2)
            broker_override: Force a specific broker
        """
        nav = self.nav_total
        if nav <= 0:
            return GlobalSizingResult(
                strategy=strategy, raw_size_global=0, broker="",
                broker_equity=0, capped_size=0, kelly_fraction=kelly_fraction,
                regime_multiplier=regime_multiplier, final_size=0,
            )

        # Global sizing
        raw_size = nav * allocation_pct * kelly_fraction * regime_multiplier

        # Determine broker
        broker = broker_override or STRATEGY_BROKER_MAP.get(strategy, "alpaca")
        broker_equity = self._equity.get(broker, 0)

        # Cap to broker equity
        max_on_broker = broker_equity * self._max_broker_pct
        capped = raw_size > max_on_broker
        capped_size = min(raw_size, max_on_broker)

        if capped:
            logger.info(
                "Position capped by broker equity: $%.0f → $%.0f (%s max $%.0f)",
                raw_size, capped_size, broker, max_on_broker,
            )

        return GlobalSizingResult(
            strategy=strategy,
            raw_size_global=raw_size,
            broker=broker,
            broker_equity=broker_equity,
            capped_size=capped_size,
            kelly_fraction=kelly_fraction,
            regime_multiplier=regime_multiplier,
            final_size=capped_size,
            capped=capped,
        )
