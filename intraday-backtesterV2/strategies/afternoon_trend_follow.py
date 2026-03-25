"""
STRAT-13 : Afternoon Trend Follow

Edge : Apres 13:30 ET, si le stock montre un trend clair (ADX > 25, EMA 9 > EMA 21)
et qu'il a bouge < 1% depuis l'open (pas de fatigue), le move de l'apres-midi continue.
Les algos TWAP institutionnels executent souvent l'apres-midi.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio


class AfternoonTrendFollowStrategy(BaseStrategy):
    name = "Afternoon Trend Follow"

    EMA_FAST = 9
    EMA_SLOW = 21
    ADX_MIN = 20
    MAX_MORNING_MOVE = 0.015   # Pas trop de move le matin (< 1.5%)
    STOP_PCT = 0.005
    TARGET_PCT = 0.010
    VOLUME_MULT = 1.0
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < 30:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            close = df["close"]
            day_open = df.iloc[0]["open"]

            ema_fast = close.ewm(span=self.EMA_FAST, adjust=False).mean()
            ema_slow = close.ewm(span=self.EMA_SLOW, adjust=False).mean()
            adx_vals = adx(df, 14)
            vol_r = volume_ratio(df["volume"], 20)

            signal_found = False

            # Scanner l'apres-midi (13:30-15:15)
            afternoon = df.between_time("13:30", "15:15")
            for ts, bar in afternoon.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.EMA_SLOW + 5:
                    continue

                # Morning move pas trop grand
                morning_move = abs(bar["close"] - day_open) / day_open
                if morning_move > self.MAX_MORNING_MOVE:
                    continue

                adx_now = adx_vals.iloc[idx]
                if pd.isna(adx_now) or adx_now < self.ADX_MIN:
                    continue

                fast = ema_fast.iloc[idx]
                slow = ema_slow.iloc[idx]
                entry_price = bar["close"]

                if fast > slow:
                    # Trend haussier
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "pm_trend", "adx": round(adx_now, 1)}))
                    trades_today += 1
                    signal_found = True
                elif fast < slow:
                    # Trend baissier
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "pm_trend", "adx": round(adx_now, 1)}))
                    trades_today += 1
                    signal_found = True

        return signals
