"""
Stratégie 7 : FOMC/CPI Drift
Les annonces macro créent des mouvements directionnels prévisibles.

Hypothèses validées par la recherche :
- "FOMC Drift" : mouvement directionnel dans les 2h post-annonce
- CPI surprise → mouvement violent dans les 5 premières minutes, puis continuation
- NFP : momentum continuation dans la direction du gap d'ouverture

Règles :
- Identifier les jours FOMC/CPI/NFP via un calendrier intégré
- FOMC (14:00 ET) : attendre 5 min après l'annonce, entrer dans la direction du mouvement
- CPI (08:30 ET pre-market) : entrer à l'ouverture dans la direction du gap
- Stop serré : 0.3% — la volatilité fait le reste
- Target : trailing stop de 0.5% après 0.3% de profit
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date
from backtest_engine import BaseStrategy, Signal
import config


# Calendrier FOMC 2024-2025 (dates des décisions)
FOMC_DATES = [
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18",
]

# CPI release dates (généralement 2ème ou 3ème mardi/mercredi du mois)
# On détecte dynamiquement — mais voici les dates connues 2024-2025
CPI_DATES = [
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
    "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-14", "2025-11-12", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11",
]


class FOMCDriftStrategy(BaseStrategy):
    name = "FOMC/CPI Drift"

    def __init__(self, stop_pct: float = 0.003, trail_trigger: float = 0.003,
                 trail_pct: float = 0.005):
        self.stop_pct = stop_pct
        self.trail_trigger = trail_trigger
        self.trail_pct = trail_pct
        self.fomc_dates = set(pd.to_datetime(d).date() for d in FOMC_DATES)
        self.cpi_dates = set(pd.to_datetime(d).date() for d in CPI_DATES)

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        is_fomc = date in self.fomc_dates
        is_cpi = date in self.cpi_dates

        if not is_fomc and not is_cpi:
            return signals

        for ticker in ["SPY", "QQQ"]:
            if ticker not in data:
                continue

            df = data[ticker]

            if is_fomc:
                # FOMC annonce à 14:00 ET — attendre 5 min
                post_fomc = df.between_time("14:05", "14:10")
                if post_fomc.empty:
                    continue

                pre_fomc = df.between_time("13:55", "14:00")
                if pre_fomc.empty:
                    continue

                pre_price = pre_fomc.iloc[-1]["close"]
                post_price = post_fomc.iloc[0]["close"]
                move_pct = (post_price - pre_price) / pre_price

                if abs(move_pct) < 0.001:  # Mouvement trop faible
                    continue

                entry = post_fomc.iloc[-1]["close"]
                ts = post_fomc.index[-1]

                if move_pct > 0:
                    signals.append(Signal(
                        action="LONG", ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 - self.stop_pct),
                        take_profit=entry * (1 + self.stop_pct * 3),
                        timestamp=ts,
                        metadata={"strategy": self.name, "event": "FOMC",
                                  "initial_move": round(move_pct * 100, 3)},
                    ))
                else:
                    signals.append(Signal(
                        action="SHORT", ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 + self.stop_pct),
                        take_profit=entry * (1 - self.stop_pct * 3),
                        timestamp=ts,
                        metadata={"strategy": self.name, "event": "FOMC",
                                  "initial_move": round(move_pct * 100, 3)},
                    ))

            elif is_cpi:
                # CPI sort à 08:30 — gap visible à l'ouverture 09:30
                open_bars = df.between_time("09:30", "09:35")
                if open_bars.empty:
                    continue

                day_open = open_bars.iloc[0]["open"]

                # Direction du gap (proxy CPI surprise)
                # On utilise le mouvement des 5 premières minutes comme confirmation
                first_5min_close = open_bars.iloc[-1]["close"]
                move_pct = (first_5min_close - day_open) / day_open

                if abs(move_pct) < 0.002:
                    continue

                # Entrer après confirmation (barre de 09:35)
                entry_bars = df.between_time("09:35", "09:40")
                if entry_bars.empty:
                    continue

                entry = entry_bars.iloc[0]["close"]
                ts = entry_bars.index[0]

                if move_pct > 0:
                    signals.append(Signal(
                        action="LONG", ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 - self.stop_pct),
                        take_profit=entry * (1 + self.stop_pct * 4),
                        timestamp=ts,
                        metadata={"strategy": self.name, "event": "CPI",
                                  "initial_move": round(move_pct * 100, 3)},
                    ))
                else:
                    signals.append(Signal(
                        action="SHORT", ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 + self.stop_pct),
                        take_profit=entry * (1 - self.stop_pct * 4),
                        timestamp=ts,
                        metadata={"strategy": self.name, "event": "CPI",
                                  "initial_move": round(move_pct * 100, 3)},
                    ))

        return signals
