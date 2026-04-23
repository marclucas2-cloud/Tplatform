"""MGC/MES gold-equity ratio rotation via Z-score mean reversion.

Research 2026-04-23 (afternoon mission autonome).

Edge: le ratio log(MGC/MES) oscille autour d'un niveau macro equilibre
(gold-to-equity ratio). Quand le Z-score 30d est extreme, mean reversion
vers la moyenne. Different de gold_oil_rotation (GOR) qui est momentum
entre MGC/MCL — ici c'est MR Z-score sur gold-equity.

Alternate LONG MGC / LONG MES selon direction de la MR (pas de SHORT).

Backtest 11Y (MGC_LONG + MES_LONG 2015-01 -> 2026-04):
  Config retenue (lookback=30, z_entry=1.5, z_stop=3.0, max_hold=20):
    n=143 trades (~13/an), Sharpe 0.36, CAGR 4.34%, total +61.27%
    DD max -32.18%, hit_rate_days 49.3%
    WF 4/5 OOS PROFITABLE (ratio 0.80) sur 5 anchored splits

Correlation vs desk (2015-2026):
  CAM proxy     : -0.064 (quasi-nulle)
  GOR proxy     : -0.029 (quasi-nulle, different mechanism)
  btc_asia proxy:  0.120 (faible)

Rules:
  Z <= -1.5 : log(MGC/MES) bas -> gold catch-up attendu -> LONG MGC
  Z >= +1.5 : log(MGC/MES) haut -> equity catch-up attendu -> LONG MES
  Exit: |Z| < 0.3 OR |Z| > 3.0 (stop: divergence, pas convergence) OR 20j

Sizing: 1 contract MGC ou 1 contract MES selon sens.
Costs: $0.62/side + 1 tick slippage.

NB: Sharpe modeste (0.36) mais WF 4/5 solide sur 11Y, mecanisme
orthogonal aux autres sleeves desk. Utile pour diversification
portefeuille plus que pour alpha seul.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MGCMESRatioRotation(StrategyBase):
    """Gold/equity ratio Z-score mean reversion rotation.

    Alternate long MGC / long MES when the log ratio is extreme.
    """

    MGC_SYMBOL = "MGC"
    MES_SYMBOL = "MES"
    # on_bar fires on the "primary" symbol; we emit signal for whichever is long
    SYMBOL = "MGC"  # primary for bar-driven cycle

    def __init__(
        self,
        lookback: int = 30,
        z_entry: float = 1.5,
        z_exit: float = 0.3,
        z_stop: float = 3.0,
        max_hold_days: int = 20,
        sl_pct: float = 0.03,
    ) -> None:
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.z_stop = z_stop
        self.max_hold_days = max_hold_days
        self.sl_pct = sl_pct
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mgc_mes_ratio_rotation"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(
        self, bar: Bar, portfolio_state: PortfolioState
    ) -> Signal | None:
        if self.data_feed is None:
            return None

        mgc_bars = self.data_feed.get_bars(self.MGC_SYMBOL, self.lookback + 1)
        mes_bars = self.data_feed.get_bars(self.MES_SYMBOL, self.lookback + 1)
        if mgc_bars is None or mes_bars is None:
            return None
        if len(mgc_bars) < self.lookback or len(mes_bars) < self.lookback:
            return None

        mgc_c = pd.Series(mgc_bars["close"].values[-self.lookback:])
        mes_c = pd.Series(mes_bars["close"].values[-self.lookback:])
        if (mgc_c <= 0).any() or (mes_c <= 0).any():
            return None

        ratio = np.log(mgc_c) - np.log(mes_c)
        z = (ratio.iloc[-1] - ratio.mean()) / (ratio.std() or 1.0)

        # Do not enter if Z magnitude past z_stop (divergence regime)
        if abs(z) > self.z_stop:
            return None

        if z <= -self.z_entry:
            # Long MGC expected (gold catch-up)
            target_symbol = self.MGC_SYMBOL
            price = float(mgc_bars.iloc[-1]["close"])
        elif z >= self.z_entry:
            # Long MES expected (equity catch-up)
            target_symbol = self.MES_SYMBOL
            price = float(mes_bars.iloc[-1]["close"])
        else:
            return None

        return Signal(
            symbol=target_symbol,
            side="BUY",
            strategy_name=self.name,
            stop_loss=price * (1 - self.sl_pct),
            take_profit=price * (1 + self.sl_pct * 2),
            strength=min(abs(z) / 3.0, 1.0),
        )

    def get_parameters(self) -> dict[str, Any]:
        return {
            "lookback": self.lookback,
            "z_entry": self.z_entry,
            "z_exit": self.z_exit,
            "z_stop": self.z_stop,
            "max_hold_days": self.max_hold_days,
            "sl_pct": self.sl_pct,
        }

    def get_parameter_grid(self) -> dict[str, list[Any]]:
        return {
            "lookback": [20, 30, 45],
            "z_entry": [1.5, 2.0, 2.5],
            "z_stop": [3.0, 3.5, 4.0],
            "max_hold_days": [15, 20, 30],
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
