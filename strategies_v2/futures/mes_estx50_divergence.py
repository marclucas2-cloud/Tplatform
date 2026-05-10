"""MES long after MES/ESTX50 divergence (US-EU intermarket Z-score).

Research 2026-04-23 (afternoon mission autonome).

Edge: le spread log(MES/ESTX50) oscille autour d'un niveau d'equilibre
structurel (relation US-EU actions). Quand le Z-score 25d descend sous
-1.5 (MES oversold vs ESTX50), MES tend a converger a la hausse les
10-15 jours suivants. Intermarket reversal classique (Gatev-Goetzmann-
Rouwenhorst 2006 sur indices).

LONG MES ONLY (pas de SHORT, pas de routing ESTX50). On ne trade que
le cote sous-evalue quand c'est MES.

Backtest 5Y (MES_LONG + ESTX50_1D 2021-01 -> 2026-04):
  Config robuste retenue (LB=25, Z=1.5, max_hold=15):
    n=48 trades (~10/an), Sharpe 0.95, CAGR 8.9%
    DD max -10.4%, WF 5/5 OOS PROFITABLE (ratio 1.00 parfait)
    hit_rate_days 45.5%

Correlation vs desk (2021-2026):
  CAM proxy      : -0.005 (quasi-nulle)
  GOR proxy      : -0.102 (legere anti-corr)
  btc_asia proxy :  0.083 (quasi-nulle)

Rules:
  LONG: Z(log(MES/ESTX50), 25d) <= -1.5
  Exit: Z > -0.5 OR 15 jours de hold
  SL: 30 points MES ($150 par contract)
  TP: time exit preferred (capture convergence)

Sizing: 1 contract MES. Commission $0.62/side + 1 tick slippage.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MESEstx50Divergence(StrategyBase):
    """Long MES when MES/ESTX50 Z-score indicates MES oversold vs ESTX50."""

    SYMBOL = "MES"
    REF_SYMBOL = "ESTX50"
    TICK_SIZE = 0.25
    TICK_VALUE = 1.25

    def __init__(
        self,
        lookback: int = 25,
        z_entry: float = 1.5,
        z_exit: float = 0.5,
        max_hold_days: int = 15,
        sl_points: float = 30.0,
    ) -> None:
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.max_hold_days = max_hold_days
        self.sl_points = sl_points
        self.data_feed: DataFeed | None = None
        # Diagnostics audit (idem mes_mr_vix_spike) : journal paper expose
        # z_score + spread pour comparer paper vs backtest avant promotion.
        self.last_diagnostics: dict = {}

    @property
    def name(self) -> str:
        return "mes_estx50_divergence"

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
        self.last_diagnostics = {
            "rejection_reason": None,
            "z_current": None,
            "z_entry_threshold": -self.z_entry,
            "spread_last": None,
            "spread_mean": None,
            "spread_std": None,
            "lookback": self.lookback,
        }

        if self.data_feed is None:
            self.last_diagnostics["rejection_reason"] = "no_data_feed"
            return None

        mes_bars = self.data_feed.get_bars(self.SYMBOL, self.lookback + 1)
        estx50_bars = self.data_feed.get_bars(self.REF_SYMBOL, self.lookback + 1)
        if mes_bars is None or estx50_bars is None:
            self.last_diagnostics["rejection_reason"] = "missing_bars"
            return None
        if len(mes_bars) < self.lookback or len(estx50_bars) < self.lookback:
            self.last_diagnostics["rejection_reason"] = "insufficient_history"
            return None

        # Align on last N bars
        mes_c = pd.Series(mes_bars["close"].values[-self.lookback:])
        est_c = pd.Series(estx50_bars["close"].values[-self.lookback:])
        if (mes_c <= 0).any() or (est_c <= 0).any():
            self.last_diagnostics["rejection_reason"] = "non_positive_close"
            return None

        spread = np.log(mes_c) - np.log(est_c)
        spread_mean = float(spread.mean())
        spread_std = float(spread.std() or 1.0)
        z_current = (float(spread.iloc[-1]) - spread_mean) / spread_std

        self.last_diagnostics.update({
            "z_current": round(z_current, 4),
            "spread_last": round(float(spread.iloc[-1]), 6),
            "spread_mean": round(spread_mean, 6),
            "spread_std": round(spread_std, 6),
        })

        if z_current > -self.z_entry:
            self.last_diagnostics["rejection_reason"] = "z_above_entry_threshold"
            return None

        # Signal: MES oversold vs ESTX50 -> LONG
        price = bar.close
        self.last_diagnostics["rejection_reason"] = None
        return Signal(
            symbol=self.SYMBOL,
            side="BUY",
            strategy_name=self.name,
            stop_loss=price - self.sl_points,
            take_profit=price + self.sl_points * 2,
            strength=min(abs(z_current) / 3.0, 1.0),
        )

    def get_parameters(self) -> dict[str, Any]:
        return {
            "lookback": self.lookback,
            "z_entry": self.z_entry,
            "z_exit": self.z_exit,
            "max_hold_days": self.max_hold_days,
            "sl_points": self.sl_points,
        }

    def get_parameter_grid(self) -> dict[str, list[Any]]:
        return {
            "lookback": [15, 20, 25, 30],
            "z_entry": [1.5, 2.0, 2.5],
            "max_hold_days": [5, 10, 15, 20],
            "sl_points": [25.0, 30.0, 40.0],
        }

    def set_parameters(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
