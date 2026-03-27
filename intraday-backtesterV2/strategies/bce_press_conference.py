"""
EU-005 : BCE Press Conference Drift

Edge:
    La BCE produit 2 events distincts chaque jour de meeting (~8 fois/an) :
    (1) Decision de taux a 13:45 CET (07:45 ET)
    (2) Conference de presse Lagarde a 14:30 CET (08:30 ET)

    La conference de presse peut REVERSER la reaction initiale quand Lagarde
    nuance la decision avec ses commentaires. Ce pattern est exploitable :
    - Si la conference commence a bouger dans la direction OPPOSEE de la
      reaction initiale (reversal > 0.2%), on entre en reversal
    - Si la conference CONFIRME la direction avec > 0.3% de move additionnel,
      on entre en continuation

    Les banques europeennes (BNP, SocGen, Deutsche Bank, ING) sont les plus
    sensibles aux decisions BCE car elles sont directement impactees par les
    taux directeurs.

Regles:
    - Jour BCE uniquement (EventCalendar.is_bce_day)
    - Mesurer le move 13:45-14:30 CET = 07:45-08:30 ET (reaction decision)
    - A 14:35-14:45 CET = 08:35-08:45 ET :
        - Reversal > 0.2% en direction opposee → entrer reversal
        - Continuation > 0.3% dans la meme direction → entrer continuation
    - Stop-loss : 1.5% depuis l'entree
    - Take-profit : 3.0% depuis l'entree (ou fermeture a 17:30 CET = 11:30 ET)
    - Tickers : banques EU tradees sur US markets via ADR/ETF proxies
    - ~8 events/an x 5 ans = 40 trades potentiels
    - Filtre : skip si decision unanime (pas de surprise → pas de conference drift)

Note timezone:
    Les horaires BCE sont en CET. Conversion vers ET (Eastern Time) :
    CET = ET + 6h (hiver) ou ET + 6h (ete, CEST = ET + 6h aussi).
    - 13:45 CET → 07:45 ET (pre-market, pas tradable directement)
    - 14:30 CET → 08:30 ET (pre-market)
    - On utilise les proxies US qui ouvrent a 09:30 ET et captent le move.
    - Decision reaction = mouvement entre 09:30 et 09:45 ET (premiers 15 min)
    - Press conference signal = mouvement 09:45-10:00 ET
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date
from backtest_engine import BaseStrategy, Signal

# Import optionnel du calendrier centralise
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

# Fallback : dates BCE 2021-2026
BCE_DATES_FALLBACK = [
    # 2021
    "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
    "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
    # 2022
    "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
    "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
    # 2023
    "2023-02-02", "2023-03-16", "2023-05-04", "2023-06-15",
    "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
    # 2024
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    # 2025
    "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
    "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
    # 2026
    "2026-01-22", "2026-03-05", "2026-04-16", "2026-06-04",
    "2026-07-16", "2026-09-10", "2026-10-29", "2026-12-10",
]

# Tickers banques EU — proxies tradables sur US markets
# BNP.PA, GLE.PA, DBK.DE, ING.AS ne sont pas directement sur Alpaca.
# On utilise les ETFs/ADRs bancaires europeens accessibles.
EU_BANK_TICKERS = ["EUFN", "DB", "ING", "BBVA", "SAN"]
# EUFN = iShares MSCI Europe Financials ETF (proxy le plus liquide)
# DB = Deutsche Bank ADR
# ING = ING Group ADR
# BBVA = Banco Bilbao ADR
# SAN = Banco Santander ADR

# Benchmark pour mesurer la reaction BCE
BENCHMARK_TICKER = "EUFN"


class BCEPressConferenceStrategy(BaseStrategy):
    """BCE Press Conference Drift — reversal ou continuation post-Lagarde.

    Exploite le decalage entre la reaction a la decision de taux (13:45 CET)
    et la conference de presse (14:30 CET) ou Lagarde peut nuancer/reverser
    le message initial.
    """

    name = "BCE Press Conference"

    # --- Parametres ---
    # Seuils de move en ET (apres conversion des heures CET)
    REVERSAL_THRESHOLD = 0.002   # 0.2% reversal minimum pour entrer
    CONTINUATION_THRESHOLD = 0.003  # 0.3% continuation pour entrer
    MIN_DECISION_MOVE = 0.001    # Decision doit avoir bouge > 0.1% (pas unanime)
    STOP_PCT = 0.015             # 1.5% stop-loss
    TARGET_PCT = 0.030           # 3.0% take-profit
    MAX_TRADES_PER_DAY = 2       # Max 2 positions (best + secondary)

    # Horaires en ET (convertis depuis CET)
    # Decision reaction : 09:30-09:45 ET (open du marche US, capte la reaction)
    DECISION_START = dt_time(9, 30)
    DECISION_END = dt_time(9, 45)
    # Press conference signal : 09:45-10:00 ET
    PRESS_CONF_START = dt_time(9, 45)
    PRESS_CONF_END = dt_time(10, 0)
    # Deadline de sortie : 11:30 ET (= 17:30 CET)
    EXIT_DEADLINE = dt_time(11, 30)

    def __init__(self, calendar=None):
        """
        Args:
            calendar: EventCalendar instance (optionnel, fallback sur dates hard-coded)
        """
        self._calendar = calendar if calendar is not None else _default_calendar
        if self._calendar is None:
            self._bce_dates = set(
                pd.to_datetime(d).date() for d in BCE_DATES_FALLBACK
            )
        else:
            self._bce_dates = None

    def _is_bce_day(self, d) -> bool:
        """Verifie si c'est un jour de meeting BCE."""
        if self._calendar is not None:
            return self._calendar.is_bce_day(d)
        if isinstance(d, pd.Timestamp):
            d = d.date()
        return d in self._bce_dates

    def get_required_tickers(self) -> list[str]:
        return EU_BANK_TICKERS + ["SPY"]

    def _measure_decision_reaction(self, df: pd.DataFrame) -> float:
        """Mesure le move de la reaction a la decision BCE (09:30-09:45 ET).

        Returns:
            Pourcentage de move (positif = haussier, negatif = baissier).
            0.0 si pas assez de donnees.
        """
        decision_bars = df.between_time(
            self.DECISION_START.strftime("%H:%M"),
            self.DECISION_END.strftime("%H:%M"),
        )
        if len(decision_bars) < 2:
            return 0.0

        open_price = decision_bars.iloc[0]["open"]
        close_price = decision_bars.iloc[-1]["close"]

        if open_price <= 0:
            return 0.0

        return (close_price - open_price) / open_price

    def _measure_press_conference_move(self, df: pd.DataFrame) -> float:
        """Mesure le move pendant le debut de la conference (09:45-10:00 ET).

        Returns:
            Pourcentage de move. 0.0 si pas assez de donnees.
        """
        # Prix a la fin de la reaction decision (debut conference)
        decision_bars = df.between_time(
            self.DECISION_START.strftime("%H:%M"),
            self.DECISION_END.strftime("%H:%M"),
        )
        if decision_bars.empty:
            return 0.0
        reference_price = decision_bars.iloc[-1]["close"]

        # Prix apres le debut de la conference
        press_bars = df.between_time(
            self.PRESS_CONF_START.strftime("%H:%M"),
            self.PRESS_CONF_END.strftime("%H:%M"),
        )
        if press_bars.empty:
            return 0.0
        press_price = press_bars.iloc[-1]["close"]

        if reference_price <= 0:
            return 0.0

        return (press_price - reference_price) / reference_price

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        """
        Genere des signaux BCE Press Conference pour un jour donne.

        Logique :
        1. Verifier que c'est un jour BCE
        2. Mesurer la reaction a la decision (09:30-09:45 ET)
        3. Skip si decision unanime (move < 0.1%)
        4. Mesurer le move conference de presse (09:45-10:00 ET)
        5. Si reversal > 0.2% → entrer en reversal
        6. Si continuation > 0.3% → entrer en continuation
        7. Stop 1.5%, TP 3.0%

        Args:
            data: {ticker: DataFrame intraday du jour}
            date: date du jour de trading

        Returns:
            Liste de Signal objects
        """
        signals = []

        # Guard : BCE day only
        if not self._is_bce_day(date):
            return signals

        # Guard : besoin du benchmark au minimum
        if BENCHMARK_TICKER not in data:
            return signals

        benchmark_df = data[BENCHMARK_TICKER]
        if len(benchmark_df) < 10:
            return signals

        # --- Mesurer la reaction a la decision sur le benchmark ---
        decision_move = self._measure_decision_reaction(benchmark_df)

        # Filtre : skip si decision unanime (move trop faible)
        if abs(decision_move) < self.MIN_DECISION_MOVE:
            return signals

        decision_direction = "bullish" if decision_move > 0 else "bearish"

        # --- Mesurer le move conference de presse sur le benchmark ---
        press_move = self._measure_press_conference_move(benchmark_df)

        # Determiner le type de signal
        signal_type = None

        # Cas 1 : Reversal — conference bouge en direction opposee
        if decision_move > 0 and press_move < -self.REVERSAL_THRESHOLD:
            signal_type = "reversal"
        elif decision_move < 0 and press_move > self.REVERSAL_THRESHOLD:
            signal_type = "reversal"

        # Cas 2 : Continuation — conference confirme la direction
        if signal_type is None:
            if decision_move > 0 and press_move > self.CONTINUATION_THRESHOLD:
                signal_type = "continuation"
            elif decision_move < 0 and press_move < -self.CONTINUATION_THRESHOLD:
                signal_type = "continuation"

        # Pas de signal clair
        if signal_type is None:
            return signals

        # --- Determiner la direction du trade ---
        if signal_type == "reversal":
            # On entre contre la reaction initiale (dans la direction conference)
            trade_direction = "LONG" if press_move > 0 else "SHORT"
        else:
            # Continuation : on entre dans la meme direction que la decision
            trade_direction = "LONG" if decision_move > 0 else "SHORT"

        # --- Generer signaux pour les banques EU ---
        for ticker in EU_BANK_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 10:
                continue

            # Point d'entree : premiere barre apres 10:00 ET (fin observation)
            entry_bars = df.between_time("10:00", "10:15")
            if entry_bars.empty:
                continue

            entry_price = entry_bars.iloc[0]["close"]
            ts = entry_bars.index[0]

            if entry_price <= 0:
                continue

            # Calculer stop et target
            if trade_direction == "LONG":
                stop_loss = entry_price * (1 - self.STOP_PCT)
                take_profit = entry_price * (1 + self.TARGET_PCT)
            else:
                stop_loss = entry_price * (1 + self.STOP_PCT)
                take_profit = entry_price * (1 - self.TARGET_PCT)

            # Confidence basee sur la magnitude du press move
            press_abs = abs(press_move)
            if press_abs > 0.008:
                confidence = "high"
            elif press_abs > 0.004:
                confidence = "medium-high"
            else:
                confidence = "medium"

            metadata = {
                "strategy": self.name,
                "event_type": "BCE",
                "signal_type": signal_type,
                "decision_move_pct": round(decision_move * 100, 4),
                "press_conference_move_pct": round(press_move * 100, 4),
                "decision_direction": decision_direction,
                "trade_direction": trade_direction,
                "confidence": confidence,
                "reaction_magnitude": round(press_abs * 100, 4),
            }

            signals.append(Signal(
                action=trade_direction,
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata=metadata,
            ))

        # Limiter au max de trades par jour (prendre les plus liquides d'abord)
        return signals[:self.MAX_TRADES_PER_DAY]
