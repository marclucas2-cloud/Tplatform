"""
STRAT-06 : Range Compression Breakout (Inside Bar 5M)

Edge : Les bougies inside (high < prev high AND low > prev low) signalent
une compression. 3 inside bars consecutives + breakout = signal fort.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class RangeCompressionBreakoutStrategy(BaseStrategy):
    name = "Range Compression Breakout"

    MIN_INSIDE_BARS = 2        # Minimum 2 inside bars consecutives
    STOP_PCT = 0.007
    TARGET_PCT = 0.014         # R:R = 2:1
    VOLUME_MULT = 1.3
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

            tradeable = df.between_time("09:45", "15:30")
            for i in range(self.MIN_INSIDE_BARS + 1, len(tradeable)):
                if signal_found:
                    break

                ts = tradeable.index[i]
                idx = df.index.get_loc(ts)
                if idx < self.MIN_INSIDE_BARS + 2:
                    continue

                # Compter les inside bars consecutives avant cette barre
                inside_count = 0
                for j in range(1, min(6, idx)):
                    prev_h = df["high"].iloc[idx - j - 1]
                    prev_l = df["low"].iloc[idx - j - 1]
                    curr_h = df["high"].iloc[idx - j]
                    curr_l = df["low"].iloc[idx - j]
                    if curr_h < prev_h and curr_l > prev_l:
                        inside_count += 1
                    else:
                        break

                if inside_count < self.MIN_INSIDE_BARS:
                    continue

                # Breakout : la barre actuelle casse le range de la barre avant les insides
                mother_idx = idx - inside_count - 1
                if mother_idx < 0:
                    continue
                mother_high = df["high"].iloc[mother_idx]
                mother_low = df["low"].iloc[mother_idx]
                current_close = df["close"].iloc[idx]

                if pd.isna(vol_r.iloc[idx]) or vol_r.iloc[idx] < self.VOLUME_MULT:
                    continue

                if current_close > mother_high:
                    stop = current_close * (1 - self.STOP_PCT)
                    target = current_close * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, current_close, stop, target, ts,
                                          {"strategy": "range_compress", "inside_bars": inside_count}))
                    trades_today += 1
                    signal_found = True
                elif current_close < mother_low:
                    stop = current_close * (1 + self.STOP_PCT)
                    target = current_close * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, current_close, stop, target, ts,
                                          {"strategy": "range_compress", "inside_bars": inside_count}))
                    trades_today += 1
                    signal_found = True

        return signals
