"""
STRAT-08 : Midday Reversal (Lunch Hour Mean Reversion)

Edge : Le midi (11:30-13:00 ET), le volume chute et les mouvements du matin se
retracent partiellement. Entree en reversal si le stock a bouge > 1.5% depuis l'open
et commence a retracer pendant la pause dejeuner.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi


class MiddayReversalStrategy(BaseStrategy):
    name = "Midday Reversal"

    MIN_MORNING_MOVE = 0.012   # 1.2% move minimum depuis l'open
    MAX_RETRACEMENT = 0.50     # Ne pas entrer si deja retrace > 50%
    STOP_PCT = 0.008
    TARGET_PCT = 0.012
    MIN_PRICE = 10.0
    RSI_PERIOD = 14
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

            # Open du jour
            morning = df[df.index.time <= dt_time(10, 30)]
            if morning.empty:
                continue

            day_open = morning.iloc[0]["open"]
            morning_high = morning["high"].max()
            morning_low = morning["low"].min()

            # Direction du matin
            morning_close = morning.iloc[-1]["close"]
            morning_move = (morning_close - day_open) / day_open

            if abs(morning_move) < self.MIN_MORNING_MOVE:
                continue

            rsi_vals = rsi(df["close"], self.RSI_PERIOD)
            signal_found = False

            # Scanner la pause dejeuner (11:30-13:30)
            lunch = df.between_time("11:30", "13:30")
            for ts, bar in lunch.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                entry_price = bar["close"]
                rsi_now = rsi_vals.iloc[idx] if idx < len(rsi_vals) else 50

                if morning_move > 0:
                    # Matin haussier -> reversal SHORT au dejeuner
                    retracement = (morning_high - entry_price) / (morning_high - day_open)
                    if 0.1 < retracement < self.MAX_RETRACEMENT and rsi_now > 55:
                        stop = entry_price * (1 + self.STOP_PCT)
                        target = entry_price * (1 - self.TARGET_PCT)
                        signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                              {"strategy": "midday_rev", "am_move": round(morning_move * 100, 2)}))
                        trades_today += 1
                        signal_found = True
                else:
                    # Matin baissier -> reversal LONG au dejeuner
                    retracement = (entry_price - morning_low) / (day_open - morning_low) if (day_open - morning_low) > 0 else 0
                    if 0.1 < retracement < self.MAX_RETRACEMENT and rsi_now < 45:
                        stop = entry_price * (1 - self.STOP_PCT)
                        target = entry_price * (1 + self.TARGET_PCT)
                        signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                              {"strategy": "midday_rev", "am_move": round(morning_move * 100, 2)}))
                        trades_today += 1
                        signal_found = True

        return signals
