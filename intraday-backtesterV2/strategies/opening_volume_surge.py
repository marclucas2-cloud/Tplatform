"""
STRAT-03 : Opening Volume Surge Continuation

Edge structurel :
La premiere barre 5M apres l'ouverture avec un volume > 3x la moyenne 20j et un
mouvement > 0.5% indique un flux institutionnel directionnel. Contrairement a l'ORB
(qui attend le breakout d'un range), cette strategie entre directement dans la direction
du surge si le volume le confirme. Plus selectif que l'ORB car exige 3x volume.

Selectivite : ~2-5 signaux/jour
Parametres : volume > 3x, move > 0.5%, stop 0.8%, target 1.2%
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


class OpeningVolumeSurgeStrategy(BaseStrategy):
    name = "Opening Volume Surge"

    VOLUME_MULT = 3.0          # Volume > 3x moyenne 20 barres
    MIN_MOVE_PCT = 0.005       # Move > 0.5% dans les 5 premieres minutes
    STOP_PCT = 0.008           # 0.8% stop
    TARGET_PCT = 0.012         # 1.2% target (R:R = 1.5:1)
    MIN_PRICE = 15.0
    MIN_VOLUME_ABS = 100_000   # Volume minimum absolu de la barre
    MAX_GAP_PCT = 0.05         # Exclure les gaps > 5% (trop risque)
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0
        candidates = []

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < 25:
                continue

            close = df["close"]
            if close.iloc[-1] < self.MIN_PRICE:
                continue

            # Isoler la premiere barre 5M (9:30-9:35)
            first_bars = df[df.index.time == dt_time(9, 30)]
            if first_bars.empty:
                # Chercher 9:35 (premiere barre tradable)
                first_bars = df[(df.index.time >= dt_time(9, 30)) & (df.index.time <= dt_time(9, 40))]
                if first_bars.empty:
                    continue

            first_bar = first_bars.iloc[0]
            first_bar_time = first_bars.index[0]

            # Volume de la premiere barre
            first_vol = first_bar["volume"]
            if first_vol < self.MIN_VOLUME_ABS:
                continue

            # Moyenne volume historique (barres precedentes meme heure ou toutes)
            prev_bars = df[df.index < first_bar_time]
            if len(prev_bars) < 20:
                # Utiliser les barres du jour actuel si pas assez d'historique
                avg_vol = df["volume"].rolling(20, min_periods=5).mean().iloc[-1]
                if pd.isna(avg_vol) or avg_vol == 0:
                    continue
            else:
                avg_vol = prev_bars["volume"].tail(20).mean()
                if avg_vol == 0:
                    continue

            vol_ratio = first_vol / avg_vol
            if vol_ratio < self.VOLUME_MULT:
                continue

            # Move de la premiere barre
            open_price = first_bar["open"]
            close_price = first_bar["close"]
            move_pct = (close_price - open_price) / open_price

            if abs(move_pct) < self.MIN_MOVE_PCT:
                continue

            # Exclure les gaps excessifs
            if len(prev_bars) > 0:
                prev_close = prev_bars["close"].iloc[-1]
                gap = abs((open_price - prev_close) / prev_close)
                if gap > self.MAX_GAP_PCT:
                    continue

            # Signal a la deuxieme barre (9:35) pour eviter chase
            signal_bars = df[(df.index.time >= dt_time(9, 35)) & (df.index.time <= dt_time(9, 45))]
            if signal_bars.empty:
                continue

            signal_bar = signal_bars.iloc[0]
            entry_price = signal_bar["close"]
            ts = signal_bars.index[0]

            candidates.append({
                "ticker": ticker,
                "entry_price": entry_price,
                "move_pct": move_pct,
                "vol_ratio": vol_ratio,
                "ts": ts,
            })

        # Trier par force du signal (volume ratio * move pct)
        candidates.sort(key=lambda x: abs(x["move_pct"]) * x["vol_ratio"], reverse=True)

        for c in candidates[:self.MAX_TRADES_PER_DAY]:
            entry_price = c["entry_price"]
            ts = c["ts"]

            if c["move_pct"] > 0:
                stop = entry_price * (1 - self.STOP_PCT)
                target = entry_price * (1 + self.TARGET_PCT)
                signals.append(Signal("LONG", c["ticker"], entry_price, stop, target, ts,
                                      {"strategy": "vol_surge", "vol_ratio": round(c["vol_ratio"], 1),
                                       "move_pct": round(c["move_pct"] * 100, 2)}))
            else:
                stop = entry_price * (1 + self.STOP_PCT)
                target = entry_price * (1 - self.TARGET_PCT)
                signals.append(Signal("SHORT", c["ticker"], entry_price, stop, target, ts,
                                      {"strategy": "vol_surge", "vol_ratio": round(c["vol_ratio"], 1),
                                       "move_pct": round(c["move_pct"] * 100, 2)}))

        return signals
