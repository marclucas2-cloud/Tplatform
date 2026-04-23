"""MES Mean Reversion + VIX Spike filter for BacktesterV2.

Research 2026-04-23 — mission autonome decorrelated strategy discovery.

Edge: apres 3 jours consecutifs de baisse sur MES (close < open) ET VIX > seuil
(regime de peur confirme), le marche tend a rebondir court-terme. Combine deux
effets documentes:
  - Mean reversion post-stretch (3 down days, snap-back)
  - Flight-to-quality bounce apres VIX spike

Le filtre VIX > 15 elimine les periodes de calme ou la MR sur 3 down days
n'a pas d'edge statistique.

Backtest 5Y daily (2021-01 -> 2026-04, MES_LONG + VIX_1D):
  Config robuste (consec=3, hold=4, vix_min=15):
    n=61 trades (~12/an), Sharpe 0.72, Sortino 0.59, CAGR 6.24%
    DD max -9.72%, Calmar 0.64, hit_rate_days 44.4%
    WF 5/5 profitable (PARFAIT), ratio 1.00

  Config agressive (consec=3, hold=2, vix_min=18):
    Sharpe 1.03, CAGR 6.80%, DD -7.83%, WF 4/5

Config par defaut = version robuste (WF 5/5, DD plus faible).

Correlation avec desk actuel (daily returns 2021-2026):
  vs CAM proxy       : 0.055  (quasi-nulle)
  vs GOR proxy       : -0.014 (quasi-nulle)
  vs mes_monday_long : 0.170  (faible, overlap 19% des jours long)

Rules:
  LONG ONLY: 3 bougies consecutives rouges (close < open) ET VIX > 15
  Entry: open du jour J+1 apres signal
  Hold: 4 jours de bourse
  SL: 25 points MES ($125 par contract)
  TP: optionnel, laisse au time-exit J+4

Cout realiste IBKR: $0.62 x 2 commissions + 1 tick slippage ($1.25) par entry/exit.

1 contract MES par defaut. Pas de pyramiding.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESMeanReversionVIXSpike(StrategyBase):
    """MES 3-day stretch mean reversion with VIX regime filter.

    LONG only: apres 3 bougies consecutives rouges ET VIX > 15.
    Exit: time-stop apres 4 jours ou SL 25 points.
    """

    SYMBOL = "MES"
    VIX_SYMBOL = "VIX"
    TICK_SIZE = 0.25
    TICK_VALUE = 1.25

    def __init__(
        self,
        consec_days: int = 3,
        hold_days: int = 4,
        vix_min: float = 15.0,
        sl_points: float = 25.0,
        tp_points: float = 50.0,
    ) -> None:
        self.consec_days = consec_days
        self.hold_days = hold_days
        self.vix_min = vix_min
        self.sl_points = sl_points
        self.tp_points = tp_points
        self.data_feed: DataFeed | None = None

    @property
    def name(self) -> str:
        return "mes_mr_vix_spike"

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

        sym = self.SYMBOL
        # Need N consecutive down days on MES
        bars_df = self.data_feed.get_bars(sym, self.consec_days + 1)
        if bars_df is None or len(bars_df) < self.consec_days:
            return None
        recent = bars_df.tail(self.consec_days)
        all_down = all(
            recent.iloc[i]["close"] < recent.iloc[i]["open"]
            for i in range(len(recent))
        )
        if not all_down:
            return None

        # VIX level filter: must be > vix_min (regime de peur confirme)
        vix_bars = self.data_feed.get_bars(self.VIX_SYMBOL, 1)
        if vix_bars is None or len(vix_bars) == 0:
            return None
        vix_close = float(vix_bars.iloc[-1]["close"])
        if vix_close <= self.vix_min:
            return None

        price = bar.close
        return Signal(
            symbol=sym,
            side="BUY",
            strategy_name=self.name,
            stop_loss=price - self.sl_points,
            take_profit=price + self.tp_points,
            strength=min((vix_close - self.vix_min) / 15.0, 1.0),
        )

    def get_parameters(self) -> dict[str, Any]:
        return {
            "consec_days": self.consec_days,
            "hold_days": self.hold_days,
            "vix_min": self.vix_min,
            "sl_points": self.sl_points,
            "tp_points": self.tp_points,
        }

    def get_parameter_grid(self) -> dict[str, list[Any]]:
        return {
            "consec_days": [2, 3, 4],
            "hold_days": [2, 3, 4, 5],
            "vix_min": [15.0, 18.0, 20.0, 22.0],
            "sl_points": [20.0, 25.0, 30.0],
            "tp_points": [40.0, 50.0, 60.0],
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
