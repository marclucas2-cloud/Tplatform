"""MCL overnight Monday trend — paper-only strategy (T3-A1 validation).

Source de validation:
  - scripts/research/backtest_t3a_mcl_overnight.py (backtest 2015-2026, 2833 bars)
  - docs/research/wf_reports/T3A-01_mcl_overnight.md
  - docs/research/wf_reports/INT-B_discovery_batch.md
    -> Sharpe +0.80, MaxDD -4.3%, WF 4/5 OOS pass, MC P(DD>30%) 0.0%, VALIDATED

Thesis:
  Crude oil reprices overnight on macro/OPEC/geopolitics more than during
  the US day session. The gap (prev_close -> open) on a Monday following
  10 days of positive drift captures that overnight drift cleanly.

Backtest signal (research, scripts/research/backtest_t3a_mcl_overnight.py:57-62):
  - trigger: bar.dayofweek == 0 (Monday)
  - filter:  close.pct_change(10) > 0 evaluated at Monday close
  - pnl:     (open_monday - close_friday) * $100 per contract (weekend gap)
  - cost:    $2.70 round-trip

Runtime trigger shift (review N2 fix 2026-04-18):
  Pour capturer FIDELEMENT le weekend gap du backtest, le signal est emis
  au close du VENDREDI (dayofweek == 4), pas du lundi. Le bar vendredi est
  le dernier closed dispo quand le cycle tourne vendredi soir (22h Paris,
  apres close US futures). L'ordre BUY MCL place ce moment-la part en MARKET
  et est execute a l'open de la session futures suivante = dimanche soir
  Asie = capture le weekend gap.

  Equivalence: close.pct_change(10) evalue au vendredi est correlated >0.99
  avec celui evalue au lundi (1 bar de diff sur fenetre de 10). Approximation
  acceptable en paper_only. A re-valider par backtest "friday_trigger" avant
  promotion live.

Data source caveat (review N2 2026-04-18):
  Le backtest utilise data/futures/MCL_LONG.parquet (2833 bars, fresh).
  Le worker charge data/futures/MCL_1D.parquet (1317 bars, stale ~10j).
  La strat verifie donc data freshness < 5 jours avant d'emettre un signal;
  si stale, skip silencieux (log). Fix data pipeline requis avant live.

Status: paper_only. Transition paper -> live_probation apres 30 jours sans
divergence > 1 sigma (kill criteria resserres vs default 2 sigma cf whitelist).
"""
from __future__ import annotations

import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


class MCLOvernightMonTrend(StrategyBase):
    """Long MCL trigger vendredi si trend 10j > 0 (capture weekend gap)."""

    SYMBOL = "MCL"
    TRIGGER_DAYOFWEEK = 4  # vendredi (capture weekend gap -> lundi open)
    MAX_BAR_AGE_DAYS = 5   # refuse data stale (fix data pipeline requis)

    def __init__(
        self,
        lookback: int = 10,
        sl_pct: float = 0.012,
        tp_pct: float = 0.018,
    ) -> None:
        self.lookback = lookback
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.data_feed: DataFeed | None = None
        self._last_signal_ts: pd.Timestamp | None = None

    @property
    def name(self) -> str:
        return "mcl_overnight_mon_trend10"

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self.data_feed is None:
            return None
        ts = pd.Timestamp(bar.timestamp).normalize()
        # Idempotence: un signal max par vendredi
        if self._last_signal_ts is not None and ts <= self._last_signal_ts:
            return None
        # Pattern day: vendredi (dernier trading day avant weekend)
        if ts.dayofweek != self.TRIGGER_DAYOFWEEK:
            return None
        # Data freshness: skip si le bar est vieux (MCL_1D stale bug)
        now = pd.Timestamp.utcnow().tz_localize(None).normalize()
        if (now - ts).days > self.MAX_BAR_AGE_DAYS:
            return None
        # Trend filter: besoin de lookback+1 closes
        bars = self.data_feed.get_bars(self.SYMBOL, self.lookback + 2)
        if bars is None or len(bars) < self.lookback + 1:
            return None
        close = bars["close"].astype(float)
        trend = float(close.iloc[-1] / close.iloc[-self.lookback - 1] - 1.0)
        if trend <= 0:
            return None
        close_now = float(bar.close)
        self._last_signal_ts = ts
        return Signal(
            symbol=self.SYMBOL,
            side="BUY",
            strategy_name=self.name,
            stop_loss=close_now * (1.0 - self.sl_pct),
            take_profit=close_now * (1.0 + self.tp_pct),
            strength=min(trend * 10.0, 1.0),
        )

    def get_parameters(self) -> dict:
        return {
            "symbol": self.SYMBOL,
            "lookback": self.lookback,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
        }
