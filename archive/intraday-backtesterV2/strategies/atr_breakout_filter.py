"""
STRAT-09 : ATR Breakout Filter

Edge : Les jours ou le prix casse > 1x ATR daily (calcule sur les barres intraday),
c'est un signe de momentum exceptionnel. Entry dans la direction du breakout
uniquement sur les stocks les plus volatiles (ATR > 2%).
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class ATRBreakoutFilterStrategy(BaseStrategy):
    name = "ATR Breakout Filter"

    ATR_PERIOD = 14
    ATR_MULT = 1.0             # Prix casse > 1x ATR depuis l'open
    MIN_ATR_PCT = 0.015        # ATR daily > 1.5%
    VOLUME_MULT = 1.5
    STOP_PCT = 0.008
    TARGET_PCT = 0.016         # R:R = 2:1
    MIN_PRICE = 15.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0
        candidates = []

        for ticker, df in data.items():
            if len(df) < 20:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            # ATR intraday (sur les barres 5M)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"] - df["close"].shift()).abs(),
            ], axis=1).max(axis=1)
            atr = tr.rolling(self.ATR_PERIOD).mean()

            day_open = df.iloc[0]["open"]
            vol_r = volume_ratio(df["volume"], 20)

            # ATR daily en %
            avg_atr_pct = atr.dropna().mean() / day_open if day_open > 0 else 0
            if avg_atr_pct < self.MIN_ATR_PCT:
                continue

            # Scanner apres 10:00
            tradeable = df.between_time("10:00", "15:00")
            for ts, bar in tradeable.iterrows():
                idx = df.index.get_loc(ts)
                if idx < self.ATR_PERIOD + 3:
                    continue

                current_atr = atr.iloc[idx]
                if pd.isna(current_atr) or current_atr == 0:
                    continue

                move_from_open = bar["close"] - day_open
                atr_multiple = abs(move_from_open) / current_atr

                if atr_multiple < self.ATR_MULT:
                    continue

                if pd.isna(vol_r.iloc[idx]) or vol_r.iloc[idx] < self.VOLUME_MULT:
                    continue

                candidates.append({
                    "ticker": ticker,
                    "entry_price": bar["close"],
                    "direction": "LONG" if move_from_open > 0 else "SHORT",
                    "atr_mult": atr_multiple,
                    "ts": ts,
                })
                break  # 1 signal par ticker

        candidates.sort(key=lambda x: x["atr_mult"], reverse=True)

        for c in candidates[:self.MAX_TRADES_PER_DAY]:
            ep = c["entry_price"]
            if c["direction"] == "LONG":
                signals.append(Signal("LONG", c["ticker"], ep,
                                      ep * (1 - self.STOP_PCT), ep * (1 + self.TARGET_PCT),
                                      c["ts"], {"strategy": "atr_break", "atr_mult": round(c["atr_mult"], 2)}))
            else:
                signals.append(Signal("SHORT", c["ticker"], ep,
                                      ep * (1 + self.STOP_PCT), ep * (1 - self.TARGET_PCT),
                                      c["ts"], {"strategy": "atr_break", "atr_mult": round(c["atr_mult"], 2)}))

        return signals
