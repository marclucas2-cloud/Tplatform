"""EU Sector Rotation strategy for BacktesterV2.

Daily momentum rotation between German Auto (CON.DE, BMW.DE) and French
Luxury (MC.PA, CDI.PA) sectors. Buys the sector with highest 20-day
momentum, sells the weakest. Long-only with weekly rebalancing.

Hypothesis: European sector leadership persists over multi-week windows
due to institutional flow inertia and macro regime persistence. German
auto and French luxury are structurally uncorrelated (export vs domestic
consumption drivers), making rotation effective.

Entry:
  - Compute 20-day momentum (return) for each sector basket
  - Buy the sector with the highest average 20-day return
  - Weekly rebalance (every 5 trading days)
  - Long-only: no shorting sectors

Symbols:
  German Auto: CON.DE (Continental), BMW.DE (BMW)
  French Luxury: MC.PA (LVMH), CDI.PA (Christian Dior)

Timeframe: Daily bars
Expected: ~2-4 rebalances/month, Sharpe target 0.8-1.5
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

# Sector definitions
GERMAN_AUTO = ["CON.DE", "BMW.DE"]
FRENCH_LUXURY = ["MC.PA", "CDI.PA"]
ALL_SYMBOLS = GERMAN_AUTO + FRENCH_LUXURY

STRATEGY_CONFIG = {
    "name": "EU Sector Rotation",
    "id": "EU-SECT-ROT",
    "symbols": ALL_SYMBOLS,
    "market_type": "eu_equity",
    "broker": "ibkr",
    "timeframe": "1D",
    "frequency": "weekly",
    "allocation_pct": 0.10,
    "long_only": True,
}


class EUSectorRotation(StrategyBase):
    """EU Sector Rotation -- momentum-based rotation between DE auto and FR luxury."""

    def __init__(self) -> None:
        # Parameters (tunable via WF)
        self.momentum_period: int = 20
        self.rebalance_interval: int = 5  # trading days
        self.sl_pct: float = 0.04  # 4% stop loss from entry
        self.tp_pct: float = 0.08  # 8% take profit

        # State
        self._bars_since_rebalance: int = 0
        self._current_sector: str | None = None  # "auto" or "luxury"
        self._last_signal_symbol: str | None = None

        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "eu_sector_rotation"

    @property
    def asset_class(self) -> str:
        return "eu_equity"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        self._bars_since_rebalance += 1

        # Only rebalance at the specified interval
        if self._bars_since_rebalance < self.rebalance_interval:
            return None

        self._bars_since_rebalance = 0

        # Compute average momentum for each sector
        auto_mom = self._sector_momentum(GERMAN_AUTO)
        luxury_mom = self._sector_momentum(FRENCH_LUXURY)

        if auto_mom is None or luxury_mom is None:
            return None

        # Determine winning sector
        if auto_mom > luxury_mom:
            target_sector = "auto"
            target_symbols = GERMAN_AUTO
        else:
            target_sector = "luxury"
            target_symbols = FRENCH_LUXURY

        # If already in the winning sector, no action needed
        if target_sector == self._current_sector:
            return None

        self._current_sector = target_sector

        # Pick the strongest symbol within the winning sector
        best_symbol = self._pick_strongest(target_symbols)
        if best_symbol is None:
            return None

        self._last_signal_symbol = best_symbol

        # Get current price for SL/TP
        latest = self.data_feed.get_latest_bar(best_symbol)
        if latest is None:
            return None

        price = latest.close

        # Signal strength based on momentum differential
        mom_diff = abs(auto_mom - luxury_mom)
        strength = min(mom_diff / 0.10, 1.0)  # normalize: 10% diff = max strength

        return Signal(
            symbol=best_symbol,
            side="BUY",  # long-only strategy
            strategy_name=self.name,
            stop_loss=price * (1.0 - self.sl_pct),
            take_profit=price * (1.0 + self.tp_pct),
            strength=strength,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sector_momentum(self, symbols: List[str]) -> float | None:
        """Compute average 20-day momentum (return) for a list of symbols.

        Args:
            symbols: List of symbol tickers in the sector.

        Returns:
            Average momentum as a float, or None if insufficient data.
        """
        momentums = []
        for sym in symbols:
            bars = self.data_feed.get_bars(sym, self.momentum_period + 1)
            if bars is None or len(bars) < self.momentum_period + 1:
                continue
            first_close = float(bars.iloc[0]["close"])
            last_close = float(bars.iloc[-1]["close"])
            if first_close <= 0:
                continue
            mom = (last_close - first_close) / first_close
            momentums.append(mom)

        if not momentums:
            return None

        return sum(momentums) / len(momentums)

    def _pick_strongest(self, symbols: List[str]) -> str | None:
        """Pick the symbol with the highest momentum within a sector.

        Args:
            symbols: List of candidate symbols.

        Returns:
            Best symbol ticker, or None if no data available.
        """
        best_mom = -float("inf")
        best_sym = None

        for sym in symbols:
            bars = self.data_feed.get_bars(sym, self.momentum_period + 1)
            if bars is None or len(bars) < self.momentum_period + 1:
                continue
            first_close = float(bars.iloc[0]["close"])
            last_close = float(bars.iloc[-1]["close"])
            if first_close <= 0:
                continue
            mom = (last_close - first_close) / first_close
            if mom > best_mom:
                best_mom = mom
                best_sym = sym

        return best_sym

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp) -> None:
        """No special EOD logic -- rebalance counter advances on_bar."""
        pass

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "momentum_period": self.momentum_period,
            "rebalance_interval": self.rebalance_interval,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "momentum_period": [10, 20, 40],
            "rebalance_interval": [3, 5, 10],
            "sl_pct": [0.03, 0.04, 0.05],
            "tp_pct": [0.06, 0.08, 0.10],
        }
