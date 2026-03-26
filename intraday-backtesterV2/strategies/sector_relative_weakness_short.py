"""
Sector Relative Weakness Short — SHORT ONLY

Edge : A 10:30 ET, identifier le sector ETF qui sous-performe SPY de > 1%.
La faiblesse sectorielle relative est un signal de rotation institutionnelle.
Les gros fonds vendent ce secteur, et le mouvement se prolonge dans l'apres-midi.

On short directement l'ETF sectoriel (pas les composants) :
- Spread serre (ETFs liquides)
- Pas de short borrow cost significatif
- Le mouvement sectoriel est plus previsible que celui d'un single stock

Regles :
- A 10:30 ET, calculer perf SPY et perf de chaque ETF sectoriel depuis l'open
- Condition : sector ETF underperform SPY de > 1% (= vrai signal de faiblesse)
- Filtre : si SPY est en hausse > 0.5%, skip (marche bullish, la faiblesse sectorielle
  est relative mais pas absolue — trop risque)
- Short l'ETF le plus faible. SL = 0.8%, TP = 1.5% ou EOD
- Max 1 trade/jour
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal


SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC"]
SIGNAL_TIME = dt_time(10, 30)
SIGNAL_WINDOW_END = dt_time(11, 0)  # Fenetre pour entrer si 10:30 exact manque


class SectorRelativeWeaknessShortStrategy(BaseStrategy):
    name = "Sector Relative Weakness Short"

    UNDERPERFORMANCE_THRESHOLD = 0.008  # Sector doit sous-performer SPY de > 0.8%
    SPY_MAX_UP = 0.008                  # Si SPY > +0.8%, skip
    STOP_PCT = 0.006                    # 0.6% stop tight
    TARGET_PCT = 0.012                  # 1.2% target (R:R = 2:1)
    MAX_TRADES_PER_DAY = 1

    def get_required_tickers(self) -> list[str]:
        return ["SPY"] + SECTOR_ETFS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        if "SPY" not in data:
            return []

        spy_df = data["SPY"]
        if len(spy_df) < 10:
            return []

        spy_open = spy_df.iloc[0]["open"]
        if spy_open <= 0:
            return []

        # Chercher la barre a 10:30 ET (ou la plus proche dans la fenetre 10:30-11:00)
        spy_window = spy_df.between_time("10:30", "11:00")
        if spy_window.empty:
            return []

        # Prendre la premiere barre de la fenetre (la plus proche de 10:30)
        check_ts = spy_window.index[0]
        spy_at_check = spy_window.iloc[0]
        spy_perf = (spy_at_check["close"] - spy_open) / spy_open

        # Filtre : SPY trop bullish = pas de weakness reelle
        if spy_perf > self.SPY_MAX_UP:
            return []

        # Scanner les ETFs sectoriels pour trouver le plus faible vs SPY
        candidates = []

        for etf in SECTOR_ETFS:
            if etf not in data:
                continue

            etf_df = data[etf]
            if len(etf_df) < 10:
                continue

            etf_open = etf_df.iloc[0]["open"]
            if etf_open <= 0:
                continue

            # Trouver la barre la plus proche du timestamp de check
            etf_at_check = etf_df[etf_df.index <= check_ts]
            if etf_at_check.empty:
                continue

            etf_bar = etf_at_check.iloc[-1]
            etf_perf = (etf_bar["close"] - etf_open) / etf_open

            # Underperformance relative = perf_etf - perf_spy (negatif = ETF est plus faible)
            relative_weakness = spy_perf - etf_perf

            if relative_weakness > self.UNDERPERFORMANCE_THRESHOLD:
                candidates.append({
                    "ticker": etf,
                    "etf_perf": etf_perf,
                    "relative_weakness": relative_weakness,
                    "price": etf_bar["close"],
                    "timestamp": etf_at_check.index[-1],
                })

        if not candidates:
            return []

        # Prendre le secteur le plus faible
        candidates.sort(key=lambda c: c["relative_weakness"], reverse=True)
        best = candidates[0]

        entry_price = best["price"]
        stop_loss = entry_price * (1 + self.STOP_PCT)
        take_profit = entry_price * (1 - self.TARGET_PCT)

        return [Signal(
            action="SHORT",
            ticker=best["ticker"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=best["timestamp"],
            metadata={
                "strategy": self.name,
                "spy_perf_pct": round(spy_perf * 100, 2),
                "etf_perf_pct": round(best["etf_perf"] * 100, 2),
                "relative_weakness_pct": round(best["relative_weakness"] * 100, 2),
                "sector": best["ticker"],
            },
        )]
