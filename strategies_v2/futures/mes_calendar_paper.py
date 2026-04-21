"""MES calendar effects — 3 paper-only strategies (T1-A INT-C promotion).

Wire des 3 candidates VALIDATED par INT-A (commit 938e5f3):
  - MESMondayLong : long MES open->close chaque lundi
  - MESWednesdayLong : long MES open->close chaque mercredi
  - MESPreHolidayLong : long MES open->close veille jour ferie NYSE

Status: paper_only (cf config/live_whitelist.yaml v2). Transition
paper -> live_probation apres 30 jours sans divergence > 2 sigma.

Backtest IS 2015-2026 (T1-04, scripts/research/backtest_futures_calendar.py):
  - long_mon_oc       : score +1.21, dSharpe +0.22, WF 3/5 PASS, MC P(DD>30%) 9.8%
  - long_wed_oc       : score +0.60, dSharpe +0.08, WF 4/5 PASS, MC P(DD>30%) 28.3%
  - pre_holiday_drift : score +0.32, dSharpe +0.07, WF 5/5 PASS, MC P(DD>30%) 0%

Methodologie: signal LONG sur le bar du jour matchant le pattern
calendaire. Position ouverte open->close (intraday). Pas de SL/TP
explicite (le bracket worker l'attache si requis).
"""
from __future__ import annotations

import pandas as pd

from core.backtester_v2.data_feed import DataFeed
from core.backtester_v2.strategy_base import StrategyBase
from core.backtester_v2.types import Bar, PortfolioState, Signal


# US NYSE holidays 2026 (extrait pertinent pour pre_holiday_drift)
_US_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027 anticipe
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-04-02",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}
_HOLIDAY_DATES = {pd.Timestamp(d).normalize() for d in _US_HOLIDAYS_2026}


class _MESCalendarBase(StrategyBase):
    """Base classe pour les 3 strats calendaires MES."""

    SYMBOL = "MES"

    def __init__(self) -> None:
        self.data_feed: DataFeed | None = None
        self._last_signal_ts: pd.Timestamp | None = None
        # Override pour paper runner (wall-clock today). En backtest, on garde
        # bar.timestamp comme avant (iteration historique). Fix 2026-04-21:
        # bug observe lundi 2026-04-20 14:00 UTC ou bar.timestamp==vendredi
        # -> dayofweek=4 -> "pas un jour pattern". Le cycle paper doit
        # utiliser le vrai jour runtime, pas le dernier bar disponible.
        self.runtime_today: pd.Timestamp | None = None

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def broker(self) -> str:
        return "ibkr"

    def set_data_feed(self, feed: DataFeed) -> None:
        self.data_feed = feed

    def set_runtime_today(self, today: pd.Timestamp) -> None:
        """Paper/live runner appelle ceci avant on_bar pour que la detection
        pattern utilise le jour actuel et non bar.timestamp (qui peut etre
        le close de la veille)."""
        self.runtime_today = pd.Timestamp(today).normalize()

    def _is_pattern_day(self, bar_ts: pd.Timestamp) -> bool:
        raise NotImplementedError

    def on_bar(self, bar: Bar, portfolio_state: PortfolioState) -> Signal | None:
        if self.data_feed is None:
            return None
        bar_ts = pd.Timestamp(bar.timestamp).normalize()
        # Pattern-day check: use runtime_today override (paper/live) si fourni,
        # sinon bar.timestamp (backtest: iteration historique, toujours OK).
        check_ts = self.runtime_today if self.runtime_today is not None else bar_ts
        # Idempotence: un signal max par jour (keyed sur check_ts, sinon on
        # re-signal tous les backtest ticks le meme jour).
        if self._last_signal_ts is not None and check_ts <= self._last_signal_ts:
            return None
        if not self._is_pattern_day(check_ts):
            return None
        # On signale un BUY MES pour la session du jour
        # SL conservatif: 0.5% sous open (intraday move limite)
        # TP optionnel: 0.8% au-dessus (pas force)
        close_now = float(bar.close)
        self._last_signal_ts = check_ts
        return Signal(
            symbol=self.SYMBOL,
            side="BUY",
            strategy_name=self.name,
            stop_loss=close_now * 0.995,
            take_profit=close_now * 1.008,
            strength=0.5,
        )

    def get_parameters(self) -> dict:
        return {"symbol": self.SYMBOL}


class MESMondayLong(_MESCalendarBase):
    """Long MES tous les lundis."""

    @property
    def name(self) -> str:
        return "mes_monday_long_oc"

    def _is_pattern_day(self, bar_ts: pd.Timestamp) -> bool:
        return bar_ts.dayofweek == 0  # 0 = lundi


class MESWednesdayLong(_MESCalendarBase):
    """Long MES tous les mercredis."""

    @property
    def name(self) -> str:
        return "mes_wednesday_long_oc"

    def _is_pattern_day(self, bar_ts: pd.Timestamp) -> bool:
        return bar_ts.dayofweek == 2  # 2 = mercredi


class MESPreHolidayLong(_MESCalendarBase):
    """Long MES la veille d'un jour ferie NYSE."""

    @property
    def name(self) -> str:
        return "mes_pre_holiday_long"

    def _is_pattern_day(self, bar_ts: pd.Timestamp) -> bool:
        # Le bar.timestamp doit etre un jour de trading dont le lendemain
        # est dans _HOLIDAY_DATES. On cherche le prochain jour calendaire ferie
        # dans l'horizon des 7 jours suivants.
        for offset in range(1, 8):
            cand = (bar_ts + pd.Timedelta(days=offset)).normalize()
            if cand in _HOLIDAY_DATES:
                # Pre-holiday = le dernier trading day strictement avant cand.
                # Si bar_ts est ce dernier TD, on signale.
                # Approximation: bar_ts est trading day; on verifie qu'il n'y a
                # pas de TD entre bar_ts et cand (i.e. offset=1 ou bar_ts est
                # vendredi pour un holiday lundi).
                if offset == 1:
                    return True
                # cand a J+offset, et on verifie que les jours intermediaires
                # ne sont pas des trading days (samedi/dimanche/holiday)
                intermediates = [
                    (bar_ts + pd.Timedelta(days=i)).normalize()
                    for i in range(1, offset)
                ]
                if all(d.dayofweek >= 5 or d in _HOLIDAY_DATES for d in intermediates):
                    return True
        return False
