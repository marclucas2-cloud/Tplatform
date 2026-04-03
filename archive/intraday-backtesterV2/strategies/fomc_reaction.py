"""
STRAT-009 : FOMC Reaction Strategy (intraday, BaseStrategy)

Edge:
    La Fed annonce sa decision de taux a 14:00 ET, 8 fois par an.
    La volatilite est comprimee les heures precedant l'annonce (dealers
    couvrent leur gamma). A 14:00, la compression explose. Les 2 premieres
    heures post-annonce montrent un biais de continuation : historiquement
    65% du temps, la direction initiale (5 premieres minutes) se prolonge
    jusqu'a la cloture.

    On attend 5 minutes pour laisser le bruit initial s'installer, puis on
    entre dans la direction du mouvement si celui-ci est > 0.3%.
    Mouvements < 0.1% = "non-event" — on skip.

Regles:
    - Jour FOMC uniquement (EventCalendar.is_fomc_day)
    - Filtre VIX > 35 : marche trop chaotique pour un directionnel, skip
    - Entree a 14:05 ET dans la direction du move initial (14:00 -> 14:05)
    - Stop-loss : 1.5x la taille du move initial, contre la position
    - Take-profit : 2.0x la taille du move initial (ou fermeture 15:55 ET)
    - Tickers : SPY (primaire), QQQ (confirmation secondaire)
    - ~8 events/an x 6 ans = 48 trades potentiels
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date
from backtest_engine import BaseStrategy, Signal

# Import optionnel du calendrier centralise — fallback sur dates hard-coded
try:
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from core.event_calendar import EventCalendar
    _default_calendar = EventCalendar()
except Exception:
    _default_calendar = None

# Fallback : dates FOMC 2020-2026 si EventCalendar indisponible
FOMC_DATES_FALLBACK = [
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10",
    "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]


class FOMCReactionStrategy(BaseStrategy):
    """FOMC Reaction — continuation intraday post-annonce avec filtre VIX.

    Herite de BaseStrategy et implemente generate_signals() pour le moteur
    de backtest evenementiel.
    """

    name = "FOMC Reaction"

    # --- Parametres ---
    MIN_MOVE_PCT = 0.003       # 0.3% minimum pour entrer (continuation)
    SKIP_MOVE_PCT = 0.001      # < 0.1% = skip (non-event)
    STOP_MULTIPLIER = 1.5      # Stop = 1.5x le move initial
    TP_MULTIPLIER = 2.0        # TP = 2.0x le move initial
    VIX_MAX = 35.0             # Skip si VIX > 35
    MAX_TRADES_PER_DAY = 2     # SPY + QQQ max

    def __init__(self, calendar=None):
        """
        Args:
            calendar: EventCalendar instance (optionnel, fallback sur dates hard-coded)
        """
        self._calendar = calendar if calendar is not None else _default_calendar
        if self._calendar is None:
            self._fomc_dates = set(
                pd.to_datetime(d).date() for d in FOMC_DATES_FALLBACK
            )
        else:
            self._fomc_dates = None  # utilise le calendrier

    def _is_fomc_day(self, d) -> bool:
        """Verifie si c'est un jour FOMC."""
        if self._calendar is not None:
            return self._calendar.is_fomc_day(d)
        if isinstance(d, pd.Timestamp):
            d = d.date()
        return d in self._fomc_dates

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ", "VIX"]

    def _get_vix_level(self, data: dict[str, pd.DataFrame], timestamp) -> float:
        """Retourne le niveau VIX a un timestamp donne. -1 si indisponible."""
        for vix_ticker in ["^VIX", "VIX", "VIXY"]:
            if vix_ticker not in data:
                continue
            vix_df = data[vix_ticker]
            if vix_df.empty:
                continue
            vix_at = vix_df[vix_df.index <= timestamp]
            if not vix_at.empty:
                return vix_at.iloc[-1]["close"]
        return -1.0  # VIX non disponible — on ne filtre pas

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Genere des signaux FOMC Reaction pour un jour donne.

        Logique :
        1. Verifier que c'est un jour FOMC
        2. Verifier VIX < 35
        3. Mesurer le move initial SPY 14:00->14:05
        4. Si move > 0.3%, entrer en continuation
        5. Stop = 1.5x move, TP = 2.0x move

        Args:
            data: {ticker: DataFrame intraday du jour} avec colonnes OHLCV
            date: date du jour de trading

        Returns:
            Liste de Signal objects (0-2 max: SPY et/ou QQQ)
        """
        signals = []

        # Guard : FOMC day only
        if not self._is_fomc_day(date):
            return signals

        # Guard : SPY requis au minimum
        if "SPY" not in data:
            return signals

        spy_df = data["SPY"]
        if len(spy_df) < 20:
            return signals

        # --- Filtre VIX ---
        pre_announcement = spy_df[spy_df.index.time <= dt_time(14, 0)]
        if not pre_announcement.empty:
            vix_level = self._get_vix_level(data, pre_announcement.index[-1])
            if vix_level > 0 and vix_level > self.VIX_MAX:
                return signals  # VIX trop eleve — skip
        else:
            vix_level = -1.0

        # --- Generer signaux pour SPY et QQQ ---
        for ticker in ["SPY", "QQQ"]:
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 20:
                continue

            # Prix juste avant l'annonce (derniere barre <= 14:00)
            pre_bars = df[df.index.time <= dt_time(14, 0)]
            if pre_bars.empty:
                continue
            pre_price = pre_bars.iloc[-1]["close"]

            # Prix 5 min apres (14:05-14:10)
            post_bars = df.between_time("14:05", "14:10")
            if post_bars.empty:
                continue
            post_price = post_bars.iloc[0]["close"]

            # Calculer le move initial
            initial_move_pct = (post_price - pre_price) / pre_price
            initial_move_abs = abs(initial_move_pct)

            # Skip si move trop faible (non-event)
            if initial_move_abs < self.SKIP_MOVE_PCT:
                continue

            # Skip si move dans la zone grise (entre 0.1% et 0.3%)
            if initial_move_abs < self.MIN_MOVE_PCT:
                continue

            # --- Signal de continuation ---
            entry_price = post_price
            ts = post_bars.index[0]

            # Stop et TP bases sur la taille du move initial
            move_size = abs(post_price - pre_price)
            stop_distance = move_size * self.STOP_MULTIPLIER
            tp_distance = move_size * self.TP_MULTIPLIER

            # Determiner la confidence
            if initial_move_abs > 0.008:
                confidence = "high"
            elif initial_move_abs > 0.005:
                confidence = "medium-high"
            else:
                confidence = "medium"

            # QQQ confirme la direction de SPY ?
            qqq_confirms = False
            if ticker == "SPY" and "QQQ" in data:
                qqq_df = data["QQQ"]
                qqq_pre = qqq_df[qqq_df.index.time <= dt_time(14, 0)]
                qqq_post = qqq_df.between_time("14:05", "14:10")
                if not qqq_pre.empty and not qqq_post.empty:
                    qqq_move = (
                        qqq_post.iloc[0]["close"] - qqq_pre.iloc[-1]["close"]
                    ) / qqq_pre.iloc[-1]["close"]
                    qqq_confirms = (qqq_move > 0) == (initial_move_pct > 0)

            metadata = {
                "strategy": self.name,
                "event_type": "FOMC",
                "initial_move_pct": round(initial_move_pct * 100, 4),
                "initial_move_abs": round(move_size, 4),
                "confidence": confidence,
                "qqq_confirms": qqq_confirms,
                "vix_level": round(vix_level, 2) if vix_level > 0 else None,
                "reaction_magnitude": round(initial_move_abs * 100, 4),
            }

            if initial_move_pct > 0:
                # Mouvement haussier → LONG continuation
                signals.append(Signal(
                    action="LONG",
                    ticker=ticker,
                    entry_price=entry_price,
                    stop_loss=entry_price - stop_distance,
                    take_profit=entry_price + tp_distance,
                    timestamp=ts,
                    metadata=metadata,
                ))
            else:
                # Mouvement baissier → SHORT continuation
                signals.append(Signal(
                    action="SHORT",
                    ticker=ticker,
                    entry_price=entry_price,
                    stop_loss=entry_price + stop_distance,
                    take_profit=entry_price - tp_distance,
                    timestamp=ts,
                    metadata=metadata,
                ))

        return signals[:self.MAX_TRADES_PER_DAY]
