"""
STRAT-05 : Intraday Momentum Persistence

Edge structurel :
Les stocks qui montent > 1.5% dans la premiere heure (9:30-10:30) avec un volume
croissant ont une probabilite elevee de continuer dans la meme direction le reste
de la journee. Contrairement a l'Opening Drive (qui entre apres 10 min), cette
strategie attend une HEURE complete de confirmation, puis entre sur le premier
pullback de 0.3% maximum. Tres selectif.

Selectivite : ~1-3 signaux/jour
Parametres : move 1ere h > 1.5%, pullback < 0.3%, stop = low 1ere h, target 2x
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap, adx, rsi


class IntradayMomentumPersistenceStrategy(BaseStrategy):
    name = "Intraday Momentum Persistence"

    MIN_FIRST_HOUR_MOVE = 0.015  # 1.5% move minimum premiere heure
    MAX_PULLBACK_PCT = 0.003     # 0.3% pullback max avant entree
    STOP_PCT = 0.010             # 1.0% stop (ou low/high 1ere heure)
    RR_RATIO = 2.0               # R:R = 2:1
    MIN_PRICE = 15.0
    MIN_VOLUME_FIRST_HOUR = 200_000  # Volume minimum 1ere heure
    MAX_ADX = 50                 # ADX < 50 (pas de trend ultra violent)
    MIN_RSI = 40                 # RSI entre 40-60 = pas d'extreme
    MAX_RSI = 60
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0
        candidates = []

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < 30:
                continue

            close = df["close"]
            if close.iloc[-1] < self.MIN_PRICE:
                continue

            # Isoler la premiere heure (9:30-10:30)
            first_hour = df[(df.index.time >= dt_time(9, 30)) & (df.index.time < dt_time(10, 30))]
            if len(first_hour) < 6:  # Au moins 6 barres 5M
                continue

            fh_open = first_hour.iloc[0]["open"]
            fh_high = first_hour["high"].max()
            fh_low = first_hour["low"].min()
            fh_close = first_hour.iloc[-1]["close"]
            fh_volume = first_hour["volume"].sum()

            # Move de la premiere heure
            fh_move = (fh_close - fh_open) / fh_open

            if abs(fh_move) < self.MIN_FIRST_HOUR_MOVE:
                continue

            if fh_volume < self.MIN_VOLUME_FIRST_HOUR:
                continue

            # Barres apres la premiere heure (10:30-10:45 pour entree)
            entry_window = df[(df.index.time >= dt_time(10, 30)) & (df.index.time <= dt_time(11, 0))]
            if entry_window.empty:
                continue

            # Chercher un pullback dans la fenetre d'entree
            for ts, bar in entry_window.iterrows():
                if ts.time() > dt_time(15, 0):
                    break

                current_price = bar["close"]

                if fh_move > 0:
                    # Trend haussier : chercher pullback depuis le high 1ere heure
                    pullback = (fh_high - current_price) / fh_high
                    if 0 < pullback <= self.MAX_PULLBACK_PCT:
                        # Pullback acceptable -> entree LONG
                        stop = max(fh_low, current_price * (1 - self.STOP_PCT))
                        risk = current_price - stop
                        target = current_price + risk * self.RR_RATIO
                        candidates.append({
                            "ticker": ticker,
                            "entry_price": current_price,
                            "action": "LONG",
                            "stop": stop,
                            "target": target,
                            "ts": ts,
                            "fh_move": fh_move,
                            "fh_volume": fh_volume,
                        })
                        break
                else:
                    # Trend baissier : chercher pullback depuis le low 1ere heure
                    pullback = (current_price - fh_low) / fh_low
                    if 0 < pullback <= self.MAX_PULLBACK_PCT:
                        stop = min(fh_high, current_price * (1 + self.STOP_PCT))
                        risk = stop - current_price
                        target = current_price - risk * self.RR_RATIO
                        candidates.append({
                            "ticker": ticker,
                            "entry_price": current_price,
                            "action": "SHORT",
                            "stop": stop,
                            "target": target,
                            "ts": ts,
                            "fh_move": fh_move,
                            "fh_volume": fh_volume,
                        })
                        break

        # Trier par force du momentum
        candidates.sort(key=lambda x: abs(x["fh_move"]) * x["fh_volume"], reverse=True)

        for c in candidates[:self.MAX_TRADES_PER_DAY]:
            signals.append(Signal(
                c["action"], c["ticker"], c["entry_price"],
                c["stop"], c["target"], c["ts"],
                {"strategy": "momentum_persist",
                 "fh_move_pct": round(c["fh_move"] * 100, 2)}
            ))

        return signals
