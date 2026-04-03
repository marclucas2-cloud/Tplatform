"""
STRAT-11 : High/Low of Day Breakout

Edge : Quand le prix casse le high ou low du jour en cours apres 10:30 avec du volume,
il continue dans la direction du breakout. Filtre : le high/low doit etre teste
au moins 2 fois avant le breakout (resistance/support confirme).
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class HighOfDayBreakoutStrategy(BaseStrategy):
    name = "High of Day Breakout"

    MIN_TOUCHES = 2            # High/low teste au moins 2x
    TOUCH_TOLERANCE = 0.001    # 0.1% tolerance pour le test
    VOLUME_MULT = 1.5
    STOP_PCT = 0.006
    TARGET_PCT = 0.012
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < 20:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            vol_r = volume_ratio(df["volume"], 20)
            signal_found = False

            # Scanner apres 10:30
            tradeable = df.between_time("10:30", "15:00")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < 10:
                    continue

                # Calculer HOD/LOD jusqu'a la barre precedente
                prior = df.iloc[:idx]
                hod = prior["high"].max()
                lod = prior["low"].min()

                if pd.isna(vol_r.iloc[idx]) or vol_r.iloc[idx] < self.VOLUME_MULT:
                    continue

                entry_price = bar["close"]

                # Compter les touches du HOD
                high_touches = 0
                for j in range(len(prior)):
                    if abs(prior["high"].iloc[j] - hod) / hod < self.TOUCH_TOLERANCE:
                        high_touches += 1

                # Breakout au-dessus du HOD
                if entry_price > hod * (1 + self.TOUCH_TOLERANCE) and high_touches >= self.MIN_TOUCHES:
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "hod_break", "touches": high_touches}))
                    trades_today += 1
                    signal_found = True
                    continue

                # Compter les touches du LOD
                low_touches = 0
                for j in range(len(prior)):
                    if abs(prior["low"].iloc[j] - lod) / lod < self.TOUCH_TOLERANCE:
                        low_touches += 1

                # Breakdown en-dessous du LOD
                if entry_price < lod * (1 - self.TOUCH_TOLERANCE) and low_touches >= self.MIN_TOUCHES:
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "hod_break", "touches": low_touches}))
                    trades_today += 1
                    signal_found = True

        return signals
