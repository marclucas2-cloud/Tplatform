"""
Stratégie 14 : Post-Earnings Momentum Drift (PEAD)
L'un des anomalies les plus robustes en finance : le prix continue
de dériver dans la direction de la surprise earnings pendant des jours.

Version intraday :
- Le jour des earnings, si gap > 3% et volume > 5x moyenne → momentum continuation
- Entrer 30 min après l'ouverture (laisser le noise initial se dissiper)
- Pas de counter-trend : toujours dans la direction du gap
- Stop serré à 1% — le drift fait le travail

Proxy : détecter les "earnings days" via les anomalies de volume/gap
(sans calendrier earnings externe)
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class EarningsDriftStrategy(BaseStrategy):
    name = "Earnings Drift"

    def __init__(self, min_gap_pct: float = 3.0, min_vol_ratio: float = 3.0,
                 entry_delay_minutes: int = 30, stop_pct: float = 0.01,
                 target_pct: float = 0.02):
        self.min_gap_pct = min_gap_pct
        self.min_vol_ratio = min_vol_ratio
        self.entry_delay = entry_delay_minutes
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self._prev_day_data = {}  # ticker -> {close, avg_vol}

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker in ["SPY", "QQQ", "TLT", "GLD"]:
                continue

            if len(df) < 20:
                continue

            today_open = df.iloc[0]["open"]
            first_hour_vol = df.between_time("09:30", "10:30")["volume"].sum() if not df.between_time("09:30", "10:30").empty else 0

            # Vérifier si on a les données de la veille
            prev = self._prev_day_data.get(ticker)
            self._prev_day_data[ticker] = {
                "close": df.iloc[-1]["close"],
                "avg_vol": df["volume"].mean(),
                "total_vol": df["volume"].sum(),
            }

            if prev is None:
                continue

            # Gap d'ouverture
            gap_pct = ((today_open - prev["close"]) / prev["close"]) * 100

            if abs(gap_pct) < self.min_gap_pct:
                continue

            # Volume anormal (proxy pour earnings day)
            if prev["avg_vol"] == 0:
                continue
            vol_ratio = first_hour_vol / (prev["total_vol"] * 0.15)  # Normaliser première heure

            if vol_ratio < self.min_vol_ratio:
                continue

            # C'est probablement un jour d'earnings !
            # Attendre entry_delay minutes après l'ouverture
            entry_time_start = "10:00" if self.entry_delay == 30 else "09:45"
            entry_time_end = "10:15" if self.entry_delay == 30 else "10:00"

            entry_bars = df.between_time(entry_time_start, entry_time_end)
            if entry_bars.empty:
                continue

            entry_bar = entry_bars.iloc[0]
            ts = entry_bars.index[0]

            # Vérifier que le momentum continue (pas de fade)
            first_30min = df.between_time("09:30", "10:00")
            if first_30min.empty:
                continue

            first_30_move = (first_30min.iloc[-1]["close"] - first_30min.iloc[0]["open"]) / first_30min.iloc[0]["open"]

            # Le mouvement des 30 premières minutes doit être dans la direction du gap
            if gap_pct > 0 and first_30_move < 0:
                continue  # Gap up mais fade → pas de drift
            if gap_pct < 0 and first_30_move > 0:
                continue  # Gap down mais bounce → pas de drift

            entry = entry_bar["close"]
            action = "LONG" if gap_pct > 0 else "SHORT"

            signals.append(Signal(
                action=action,
                ticker=ticker,
                entry_price=entry,
                stop_loss=entry * (1 - self.stop_pct) if action == "LONG" else entry * (1 + self.stop_pct),
                take_profit=entry * (1 + self.target_pct) if action == "LONG" else entry * (1 - self.target_pct),
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "gap_pct": round(gap_pct, 2),
                    "vol_ratio": round(vol_ratio, 1),
                    "first_30min_move": round(first_30_move * 100, 3),
                },
            ))

        return signals
