"""
STRAT-02 : RSI Divergence Reversal

Edge structurel :
Divergence prix/RSI = signal classique d'epuisement. Quand le prix fait un nouveau
high mais le RSI ne confirme pas (bearish divergence), le retournement est probable.

Iteration barre par barre, lookback etendu a 24 barres.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, volume_ratio


class RSIDivergenceStrategy(BaseStrategy):
    name = "RSI Divergence Reversal"

    RSI_PERIOD = 14
    LOOKBACK = 24              # Etendu a 24 barres (~2h en 5M)
    MIN_RSI_DIVERGENCE = 3.0   # RSI doit diverger d'au moins 3 points
    RSI_HIGH = 60              # Assoupli : RSI > 60
    RSI_LOW = 40               # Assoupli : RSI < 40
    STOP_PCT = 0.007           # 0.7% stop
    TARGET_PCT = 0.014         # 1.4% target (R:R = 2:1)
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < self.RSI_PERIOD + self.LOOKBACK + 10:
                continue

            close = df["close"]
            if close.iloc[0] < self.MIN_PRICE:
                continue

            rsi_vals = rsi(close, self.RSI_PERIOD)
            signal_found = False

            # Iterer barre par barre (10:00-15:30)
            tradeable = df.between_time("10:00", "15:30")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.RSI_PERIOD + self.LOOKBACK:
                    continue

                current_rsi = rsi_vals.iloc[idx]
                if pd.isna(current_rsi):
                    continue

                entry_price = bar["close"]

                # Lookback window pour chercher les pivots
                price_window = close.iloc[max(0, idx - self.LOOKBACK):idx + 1]
                rsi_window = rsi_vals.iloc[max(0, idx - self.LOOKBACK):idx + 1]

                if len(price_window) < 5:
                    continue

                # Bearish divergence : prix new high, RSI lower
                if current_rsi > self.RSI_HIGH:
                    # Prix au plus haut de la fenetre ?
                    if entry_price >= price_window.max() * 0.999:
                        # RSI a-t-il ete plus haut avant ?
                        prev_rsi_max = rsi_window.iloc[:-3].max()
                        if not pd.isna(prev_rsi_max) and current_rsi < prev_rsi_max - self.MIN_RSI_DIVERGENCE:
                            stop = entry_price * (1 + self.STOP_PCT)
                            target = entry_price * (1 - self.TARGET_PCT)
                            signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                                  {"strategy": "rsi_div", "type": "bearish",
                                                   "rsi": round(current_rsi, 1)}))
                            trades_today += 1
                            signal_found = True
                            continue

                # Bullish divergence : prix new low, RSI higher
                if current_rsi < self.RSI_LOW:
                    if entry_price <= price_window.min() * 1.001:
                        prev_rsi_min = rsi_window.iloc[:-3].min()
                        if not pd.isna(prev_rsi_min) and current_rsi > prev_rsi_min + self.MIN_RSI_DIVERGENCE:
                            stop = entry_price * (1 - self.STOP_PCT)
                            target = entry_price * (1 + self.TARGET_PCT)
                            signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                                  {"strategy": "rsi_div", "type": "bullish",
                                                   "rsi": round(current_rsi, 1)}))
                            trades_today += 1
                            signal_found = True

        return signals
