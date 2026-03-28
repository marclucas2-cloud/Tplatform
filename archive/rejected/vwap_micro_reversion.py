"""
STRAT-04 : VWAP Micro-Deviation Reversion

Edge structurel :
Utilise un VWAP rolling court (20 barres = ~1h40) au lieu du VWAP journalier complet.
Quand le prix s'ecarte de > 1.2 ecarts-types du VWAP micro, il revient rapidement.

Iteration barre par barre avec filtres assouplis.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, adx


class VWAPMicroReversionStrategy(BaseStrategy):
    name = "VWAP Micro-Deviation"

    VWAP_LOOKBACK = 20
    ENTRY_SD = 1.2             # Assoupli : 1.2 SD
    STOP_SD = 2.0              # Assoupli : 2.0 SD
    TARGET_SD = 0.3
    RSI_PERIOD = 14
    RSI_CONFIRM_LOW = 40       # Assoupli
    RSI_CONFIRM_HIGH = 60      # Assoupli
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3
    MIN_VOLUME = 20_000        # Assoupli

    # Top liquid tickers pour le live (evite de scanner 200+ tickers via API)
    LIVE_TICKERS = [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
        "NFLX", "AVGO", "CRM", "ORCL", "QCOM", "INTC", "BA",
        "JPM", "GS", "MS", "BAC", "C",
        "XOM", "CVX", "COP", "OXY",
        "SPY", "QQQ", "IWM", "DIA",
        "COIN", "MARA", "MSTR",
    ]

    def get_required_tickers(self) -> list[str]:
        return self.LIVE_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < self.VWAP_LOOKBACK + 15:
                continue

            close = df["close"]
            if close.iloc[0] < self.MIN_PRICE:
                continue

            # Pre-calculer indicateurs
            rsi_vals = rsi(close, self.RSI_PERIOD)

            # Rolling VWAP
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            tp_vol = typical_price * df["volume"]
            cum_tp_vol = tp_vol.rolling(self.VWAP_LOOKBACK, min_periods=self.VWAP_LOOKBACK).sum()
            cum_vol = df["volume"].rolling(self.VWAP_LOOKBACK, min_periods=self.VWAP_LOOKBACK).sum()
            r_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
            deviation = close - r_vwap
            r_std = deviation.rolling(self.VWAP_LOOKBACK, min_periods=self.VWAP_LOOKBACK).std()

            signal_found = False

            # Iterer barre par barre (10:30-15:30)
            tradeable = df.between_time("10:30", "15:30")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.VWAP_LOOKBACK + 5:
                    continue

                vwap_now = r_vwap.iloc[idx]
                std_now = r_std.iloc[idx]
                rsi_now = rsi_vals.iloc[idx]

                if pd.isna(vwap_now) or pd.isna(std_now) or std_now == 0 or pd.isna(rsi_now):
                    continue

                if bar["volume"] < self.MIN_VOLUME:
                    continue

                entry_price = bar["close"]
                zscore = (entry_price - vwap_now) / std_now

                if zscore < -self.ENTRY_SD and rsi_now < self.RSI_CONFIRM_LOW:
                    stop = vwap_now - self.STOP_SD * std_now
                    target = vwap_now - self.TARGET_SD * std_now
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vwap_micro", "zscore": round(zscore, 2)}))
                    trades_today += 1
                    signal_found = True

                elif zscore > self.ENTRY_SD and rsi_now > self.RSI_CONFIRM_HIGH:
                    stop = vwap_now + self.STOP_SD * std_now
                    target = vwap_now + self.TARGET_SD * std_now
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vwap_micro", "zscore": round(zscore, 2)}))
                    trades_today += 1
                    signal_found = True

        return signals
