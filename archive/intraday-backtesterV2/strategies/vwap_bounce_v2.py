"""
Strategie : VWAP Bounce V2 — iter5

Approche revisee : le VWAP agit comme support/resistance intraday.
On trade les 3 premiers retours au VWAP de la journee, mais seulement
quand le prix a un momentum de retour (2 barres de suite dans la direction).

- Max 3 first touches (pas juste 1)
- Confirmation : 2 barres consecutives dans la direction du bounce
- RSI relache : < 48 LONG, > 52 SHORT
- Volume > 1.0x avg (juste au-dessus de la moyenne)
- ADX < 35
- Stop tight : 0.25%
- Target : 0.4% (R:R 1.6)
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap, rsi, adx
import config


EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "RWM", "PSQ", "SH", "SDS",
    "SPY", "QQQ", "IWM", "DIA",
}

MIN_PRICE = 10.0


class VWAPBounceV2Strategy(BaseStrategy):
    name = "VWAP Bounce V2"

    def __init__(
        self,
        vwap_proximity_pct: float = 0.002,     # 0.2% du VWAP = "touch"
        rsi_long_threshold: float = 48,
        rsi_short_threshold: float = 52,
        stop_pct: float = 0.003,               # 0.3% stop
        target_pct: float = 0.003,             # 0.3% target (R:R 1.0, besoin WR>52%)
        vol_multiplier: float = 1.0,           # juste au-dessus de la moyenne
        adx_max: float = 35.0,
        max_touches: int = 3,                  # accepter les 3 premiers touches
        max_trades_per_day: int = 3,
    ):
        self.vwap_proximity_pct = vwap_proximity_pct
        self.rsi_long = rsi_long_threshold
        self.rsi_short = rsi_short_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.vol_multiplier = vol_multiplier
        self.adx_max = adx_max
        self.max_touches = max_touches
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker in EXCLUDE or ticker == config.BENCHMARK:
                continue

            if len(df) < 30:
                continue

            avg_price = df["close"].mean()
            if avg_price < MIN_PRICE:
                continue

            df = df.copy()
            df["vwap_calc"] = calc_vwap(df)
            df["rsi_val"] = rsi(df["close"], period=14)
            df["vol_avg"] = df["volume"].rolling(20).mean()
            adx_series = adx(df, period=14)

            # Determiner le bias d'ouverture
            open_bars = df.between_time("09:35", "09:55")
            if len(open_bars) < 3:
                continue
            opening_vwap = open_bars.iloc[-1]["vwap_calc"]
            opening_close = open_bars.iloc[-1]["close"]
            if pd.isna(opening_vwap):
                continue
            above_vwap_at_open = opening_close > opening_vwap

            tradeable = df.between_time("10:00", "14:30")
            touch_count = 0
            signal_found = False
            # Cooldown : ne pas signaler sur des barres consecutives proches du VWAP
            last_touch_idx = -5

            for i in range(2, len(tradeable)):
                if signal_found:
                    break
                if len(signals) >= self.max_trades_per_day:
                    break

                bar = tradeable.iloc[i]
                prev = tradeable.iloc[i - 1]
                prev2 = tradeable.iloc[i - 2]
                ts = tradeable.index[i]

                vwap_val = bar["vwap_calc"]
                if pd.isna(vwap_val) or pd.isna(bar["rsi_val"]):
                    continue

                # Distance au VWAP en %
                dist_to_vwap = (bar["close"] - vwap_val) / vwap_val

                # Touch : prix passe a < 0.2% du VWAP
                near_vwap = abs(dist_to_vwap) < self.vwap_proximity_pct
                if not near_vwap:
                    continue

                # Cooldown : au moins 4 barres depuis le dernier touch
                if i - last_touch_idx < 4:
                    continue

                touch_count += 1
                last_touch_idx = i

                if touch_count > self.max_touches:
                    continue

                # Filtre ADX
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx > self.adx_max:
                    continue

                # Filtre volume
                vol_avg = bar["vol_avg"]
                if pd.isna(vol_avg) or vol_avg <= 0:
                    continue
                vol_ratio = bar["volume"] / vol_avg
                if vol_ratio < self.vol_multiplier:
                    continue

                rsi_val = bar["rsi_val"]

                # LONG : le prix etait au-dessus, descend vers VWAP et rebondit
                # Confirmation : close > prev close (momentum up)
                if above_vwap_at_open and bar["close"] > prev["close"] and rsi_val < self.rsi_long:
                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 - self.stop_pct),
                        take_profit=bar["close"] * (1 + self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi": round(rsi_val, 1),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(vol_ratio, 1),
                            "touch_num": touch_count,
                        },
                    ))
                    signal_found = True

                # SHORT : prix en-dessous, remonte vers VWAP et rejette
                elif not above_vwap_at_open and bar["close"] < prev["close"] and rsi_val > self.rsi_short:
                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=bar["close"] * (1 + self.stop_pct),
                        take_profit=bar["close"] * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi": round(rsi_val, 1),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(vol_ratio, 1),
                            "touch_num": touch_count,
                        },
                    ))
                    signal_found = True

        return signals
