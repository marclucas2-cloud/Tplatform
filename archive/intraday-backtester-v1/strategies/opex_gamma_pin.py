"""
Stratégie 8 : OpEx Gamma Pinning
Les jours d'expiration d'options, les prix gravitent autour des strikes
avec le plus d'open interest — "max pain".

Hypothèse :
- Les market makers hedgent leur gamma, créant un effet d'aimant
  vers le strike avec le plus d'OI
- Le vendredi OpEx (3ème vendredi du mois), les mouvements sont compressés
- Stratégie : mean reversion agressive vers le "round number" le plus proche

Règles :
- Identifier les vendredis OpEx (3ème vendredi de chaque mois)
- Aussi les 0DTE tous les lundis/mercredis/vendredis (SPY/QQQ)
- Entrer en mean reversion quand le prix s'éloigne de > 0.3% du round number
- Stop : 0.5% — Target : retour au round number
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date
from calendar import monthrange
from backtest_engine import BaseStrategy, Signal
import config


def get_opex_fridays(start_year: int, end_year: int) -> set:
    """Calcule tous les 3èmes vendredis (OpEx monthly) entre start et end year."""
    opex_dates = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Trouver le 3ème vendredi
            first_day_weekday = dt_date(year, month, 1).weekday()
            # Vendredi = 4
            first_friday = 1 + (4 - first_day_weekday) % 7
            third_friday = first_friday + 14
            opex_dates.add(dt_date(year, month, third_friday))
    return opex_dates


def nearest_round_number(price: float, step: float = None) -> float:
    """Trouve le round number le plus proche (strike probable)."""
    if step is None:
        if price > 500:
            step = 10
        elif price > 100:
            step = 5
        elif price > 50:
            step = 2.5
        else:
            step = 1
    return round(price / step) * step


class OpExGammaPinStrategy(BaseStrategy):
    name = "OpEx Gamma Pin"

    def __init__(self, deviation_pct: float = 0.003, stop_pct: float = 0.005):
        self.deviation_pct = deviation_pct
        self.stop_pct = stop_pct
        self.opex_dates = get_opex_fridays(2024, 2026)
        # 0DTE days : tous les lundi/mercredi/vendredi pour SPY
        # On simplifie en ne gardant que les OpEx mensuels + tous les vendredis

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # Vérifier si c'est un jour OpEx ou un vendredi (weekly options)
        is_opex = date in self.opex_dates
        is_friday = date.weekday() == 4  # Vendredi

        if not is_opex and not is_friday:
            return signals

        for ticker in self.get_required_tickers():
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 20:
                continue

            # Période de pinning : surtout l'après-midi (13:00-15:30)
            afternoon = df.between_time("13:00", "15:30")
            if afternoon.empty:
                continue

            # Calculer le "magnet price" — round number le plus proche du VWAP
            if "vwap" in df.columns and df["vwap"].notna().any():
                anchor = df["vwap"].dropna().iloc[-1]
            else:
                anchor = df["close"].mean()

            magnet = nearest_round_number(anchor)
            signal_found = False

            for ts, bar in afternoon.iterrows():
                if signal_found:
                    break

                deviation = (bar["close"] - magnet) / magnet

                # Prix trop loin au-dessus du magnet → SHORT (mean reversion)
                if deviation > self.deviation_pct:
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 + self.stop_pct),
                        take_profit=magnet,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "magnet_price": magnet,
                            "deviation_pct": round(deviation * 100, 3),
                            "is_monthly_opex": is_opex,
                        },
                    ))
                    signal_found = True

                # Prix trop loin en dessous → LONG
                elif deviation < -self.deviation_pct:
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 - self.stop_pct),
                        take_profit=magnet,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "magnet_price": magnet,
                            "deviation_pct": round(deviation * 100, 3),
                            "is_monthly_opex": is_opex,
                        },
                    ))
                    signal_found = True

        return signals
