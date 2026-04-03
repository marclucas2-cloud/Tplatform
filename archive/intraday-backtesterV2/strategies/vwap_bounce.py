"""
Stratégie 2 : VWAP Bounce
Le prix rebondit sur le VWAP avec confirmation RSI.

Règles :
- LONG quand prix touche VWAP par le haut, rebondit, RSI(14) < 40
- SHORT quand prix touche VWAP par le bas, rejette, RSI(14) > 60
- Volume de confirmation > moyenne 10 périodes
- Stop-loss : 0.3% de l'autre côté du VWAP
- Target : 0.6% (R:R 1:2)
"""
import pandas as pd
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap, rsi
import config


class VWAPBounceStrategy(BaseStrategy):
    name = "VWAP Bounce"

    def __init__(self, rsi_long_threshold: float = 40, rsi_short_threshold: float = 60,
                 stop_pct: float = 0.003, target_pct: float = 0.006):
        self.rsi_long = rsi_long_threshold
        self.rsi_short = rsi_short_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK or len(df) < 30:
                continue

            # Calculer VWAP et RSI
            df = df.copy()
            df["vwap_calc"] = calc_vwap(df)
            df["rsi"] = rsi(df["close"], period=14)
            df["vol_avg"] = df["volume"].rolling(10).mean()

            # Scanner les barres (après les 15 premières minutes)
            tradeable = df.between_time("09:45", "15:30")
            signal_found = False

            for i in range(1, len(tradeable)):
                if signal_found:
                    break

                bar = tradeable.iloc[i]
                prev = tradeable.iloc[i - 1]
                ts = tradeable.index[i]

                vwap_val = bar["vwap_calc"]
                if pd.isna(vwap_val) or pd.isna(bar["rsi"]):
                    continue

                vol_ok = bar["volume"] > bar["vol_avg"] if not pd.isna(bar["vol_avg"]) else False

                # LONG : prix était sous VWAP, remonte et touche, RSI bas
                if (prev["close"] <= vwap_val and bar["close"] > vwap_val
                        and bar["rsi"] < self.rsi_long and vol_ok):
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 - self.stop_pct),
                        take_profit=bar["close"] * (1 + self.target_pct),
                        timestamp=ts,
                        metadata={"strategy": self.name, "rsi": round(bar["rsi"], 1)},
                    ))
                    signal_found = True

                # SHORT : prix était au-dessus, descend sous VWAP, RSI haut
                elif (prev["close"] >= vwap_val and bar["close"] < vwap_val
                      and bar["rsi"] > self.rsi_short and vol_ok):
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 + self.stop_pct),
                        take_profit=bar["close"] * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={"strategy": self.name, "rsi": round(bar["rsi"], 1)},
                    ))
                    signal_found = True

        return signals
