"""
STRAT-10 : EMA Crossover 5M (Trend Following)

Edge : EMA 9/21 crossover sur 5M avec filtre ADX > 20 (confirmation de trend).
Simple mais robuste. Entree au croisement, sortie au croisement inverse ou SL/TP.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio


class EMACrossover5MStrategy(BaseStrategy):
    name = "EMA Crossover 5M"

    EMA_FAST = 9
    EMA_SLOW = 21
    ADX_MIN = 20               # Filtre trend
    STOP_PCT = 0.006
    TARGET_PCT = 0.012
    VOLUME_MULT = 1.2
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < self.EMA_SLOW + 10:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            close = df["close"]
            ema_fast = close.ewm(span=self.EMA_FAST, adjust=False).mean()
            ema_slow = close.ewm(span=self.EMA_SLOW, adjust=False).mean()
            adx_vals = adx(df, 14)
            vol_r = volume_ratio(df["volume"], 20)

            signal_found = False
            tradeable = df.between_time("09:50", "15:15")

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.EMA_SLOW + 3:
                    continue

                fast_now = ema_fast.iloc[idx]
                slow_now = ema_slow.iloc[idx]
                fast_prev = ema_fast.iloc[idx - 1]
                slow_prev = ema_slow.iloc[idx - 1]
                adx_now = adx_vals.iloc[idx]

                if pd.isna(adx_now) or adx_now < self.ADX_MIN:
                    continue
                if pd.isna(vol_r.iloc[idx]) or vol_r.iloc[idx] < self.VOLUME_MULT:
                    continue

                entry_price = bar["close"]

                # Golden cross : fast crosses above slow
                if fast_prev <= slow_prev and fast_now > slow_now:
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "ema_cross", "adx": round(adx_now, 1)}))
                    trades_today += 1
                    signal_found = True

                # Death cross : fast crosses below slow
                elif fast_prev >= slow_prev and fast_now < slow_now:
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "ema_cross", "adx": round(adx_now, 1)}))
                    trades_today += 1
                    signal_found = True

        return signals
