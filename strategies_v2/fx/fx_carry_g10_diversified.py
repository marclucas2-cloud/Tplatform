"""FX G10 Carry Diversified strategy for BacktesterV2.

Diversified carry trade across 6 JPY-funded pairs with vol-scaled
position sizing and monthly rebalancing. Extended version of the existing
carry strategy with broader pair coverage and carry differential changes
as the rebalance trigger.

Hypothesis: The forward premium puzzle implies carry is systematically
over-compensated. Diversifying across 6 high-yield-vs-JPY pairs reduces
single-pair crash risk while maintaining aggregate carry income.
Vol-scaling (Barroso & Santa-Clara 2015) dampens drawdowns during
risk-off events.

Pairs:
  AUDJPY  -- RBA vs BOJ, classic risk-on carry
  USDJPY  -- Fed vs BOJ, largest rate differential
  EURJPY  -- ECB vs BOJ, moderate carry
  NZDJPY  -- RBNZ vs BOJ, high carry, smaller market
  CADJPY  -- BOC vs BOJ, commodity-linked carry
  NOKJPY  -- Norges Bank vs BOJ, oil-linked carry

Position sizing: target 5% annualized vol per pair, capped [0.1x, 3.0x].
Rebalance: monthly, triggered by carry differential changes.

Costs: $2/trade IBKR + 0.8-1.5 bps spread = ~0.05% RT per pair.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal

logger = logging.getLogger(__name__)

# Carry pair configurations with estimated daily swap rates (bps)
CARRY_PAIRS = {
    "AUDJPY": {
        "swap_daily_bps": 3.5,
        "ibkr_symbol": "AUD.JPY",
        "min_order_size": 1000,
    },
    "USDJPY": {
        "swap_daily_bps": 4.0,
        "ibkr_symbol": "USD.JPY",
        "min_order_size": 1000,
    },
    "EURJPY": {
        "swap_daily_bps": 2.0,
        "ibkr_symbol": "EUR.JPY",
        "min_order_size": 1000,
    },
    "NZDJPY": {
        "swap_daily_bps": 3.0,
        "ibkr_symbol": "NZD.JPY",
        "min_order_size": 1000,
    },
    "CADJPY": {
        "swap_daily_bps": 2.5,
        "ibkr_symbol": "CAD.JPY",
        "min_order_size": 1000,
    },
    "NOKJPY": {
        "swap_daily_bps": 2.0,
        "ibkr_symbol": "NOK.JPY",
        "min_order_size": 5000,  # NOK is lower value per unit
    },
}

SUPPORTED_PAIRS = list(CARRY_PAIRS.keys())

STRATEGY_CONFIG = {
    "name": "FX Carry G10 Diversified",
    "id": "FX-CARRY-G10",
    "pairs": SUPPORTED_PAIRS,
    "market_type": "fx",
    "broker": "ibkr",
    "timeframe": "1D",
    "frequency": "monthly",
    "allocation_pct": 0.20,
    "max_leverage": 3.0,
    "min_capital": 5000,
}


class FXCarryG10Diversified(StrategyBase):
    """FX G10 Carry Diversified -- vol-scaled carry across 6 JPY-funded pairs."""

    def __init__(self, symbol: str = "AUDJPY") -> None:
        if symbol not in SUPPORTED_PAIRS:
            raise ValueError(
                f"Unsupported pair {symbol}. Must be one of {SUPPORTED_PAIRS}"
            )
        self._symbol = symbol

        # Parameters (tunable via WF)
        self.vol_lookback: int = 20
        self.target_vol_ann: float = 0.05
        self.sizing_min: float = 0.1
        self.sizing_max: float = 3.0
        self.rebalance_interval: int = 21  # ~monthly in trading days
        self.sl_vol_mult: float = 3.0
        self.max_dd_pct: float = 0.08

        # State
        self._bars_since_rebalance: int = 0
        self._last_sizing: Optional[float] = None
        self._equity_high: float = 0.0

        self.data_feed: Optional[DataFeed] = None

    @property
    def name(self) -> str:
        return f"fx_carry_g10_{self._symbol.lower()}"

    @property
    def asset_class(self) -> str:
        return "fx"

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
    ) -> Optional[Signal]:
        if self.data_feed is None:
            return None

        sym = self._symbol
        self._bars_since_rebalance += 1

        # Drawdown kill switch
        equity = portfolio_state.equity
        if equity > self._equity_high:
            self._equity_high = equity
        if self._equity_high > 0:
            dd = (equity - self._equity_high) / self._equity_high
            if dd < -self.max_dd_pct:
                return None  # stop trading until drawdown recovers

        # Only rebalance at the specified interval
        if self._bars_since_rebalance < self.rebalance_interval:
            return None

        self._bars_since_rebalance = 0

        # Need enough history for vol calculation
        bars_df = self.data_feed.get_bars(sym, self.vol_lookback + 5)
        if bars_df is None or len(bars_df) < self.vol_lookback + 1:
            return None

        # Compute realized vol
        returns = bars_df["close"].pct_change().dropna()
        if len(returns) < self.vol_lookback:
            return None

        vol = float(returns.tail(self.vol_lookback).std())
        if vol <= 0 or np.isnan(vol):
            return None

        # Vol-target sizing
        target_daily = self.target_vol_ann / np.sqrt(252)
        sizing = float(np.clip(target_daily / vol, self.sizing_min, self.sizing_max))

        # Skip rebalance if sizing has not changed materially (>20%)
        if self._last_sizing is not None:
            change = abs(sizing - self._last_sizing) / self._last_sizing
            if change < 0.20:
                return None

        self._last_sizing = sizing
        price = bar.close

        # Stop loss: sl_vol_mult * daily vol below entry
        daily_vol_pct = vol
        sl_distance = price * daily_vol_pct * self.sl_vol_mult
        stop_loss = price - sl_distance

        # Carry is always long (buy high-yield, sell JPY)
        strength = min(sizing / self.sizing_max, 1.0)

        return Signal(
            symbol=sym,
            side="BUY",
            strategy_name=self.name,
            stop_loss=stop_loss,
            take_profit=None,  # carry trades ride with vol-scaling, no fixed TP
            strength=strength,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_eod(self, timestamp) -> None:
        """No special EOD action -- rebalance counter advances on_bar."""
        pass

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self._symbol,
            "vol_lookback": self.vol_lookback,
            "target_vol_ann": self.target_vol_ann,
            "sizing_min": self.sizing_min,
            "sizing_max": self.sizing_max,
            "rebalance_interval": self.rebalance_interval,
            "sl_vol_mult": self.sl_vol_mult,
            "max_dd_pct": self.max_dd_pct,
        }

    def set_parameters(self, params: Dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_parameter_grid(self) -> Dict[str, List[Any]]:
        return {
            "vol_lookback": [15, 20, 30],
            "target_vol_ann": [0.03, 0.05, 0.07],
            "rebalance_interval": [15, 21, 42],
            "sl_vol_mult": [2.5, 3.0, 4.0],
            "sizing_min": [0.1, 0.2],
            "sizing_max": [2.0, 3.0, 5.0],
        }
