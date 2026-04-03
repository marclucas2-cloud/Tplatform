"""
STRAT-01 : Volatility Squeeze Breakout

Edge structurel :
Quand la volatilite se contracte (Bollinger Band Width au minimum sur 20 barres),
l'energie accumulee se libere en breakout directionnel. Direction determinee par
la position du prix relatif au VWAP.

Iteration barre par barre pour detecter le breakout au moment ou il se produit.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap, bollinger_bands, volume_ratio, adx


class VolatilitySqueezeBreakoutStrategy(BaseStrategy):
    name = "Volatility Squeeze Breakout"

    BB_PERIOD = 20
    BB_STD = 2.0
    SQUEEZE_LOOKBACK = 20
    WIDTH_PERCENTILE = 20      # Assoupli : 20e percentile (au lieu de 10)
    VOLUME_MULT = 1.3          # Assoupli : 1.3x
    STOP_PCT = 0.008
    TARGET_PCT = 0.016
    MIN_PRICE = 10.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker, df in data.items():
            if trades_today >= self.MAX_TRADES_PER_DAY:
                break
            if len(df) < self.BB_PERIOD + self.SQUEEZE_LOOKBACK + 5:
                continue

            close = df["close"]
            if close.iloc[0] < self.MIN_PRICE:
                continue

            # Pre-calculer les indicateurs sur tout le jour
            upper, middle, lower = bollinger_bands(close, self.BB_PERIOD, self.BB_STD)
            bb_width = (upper - lower) / middle
            vol_r = volume_ratio(df["volume"], 20)

            try:
                vwap_vals = vwap(df)
            except Exception:
                vwap_vals = middle

            signal_found = False

            # Iterer barre par barre (10:00-15:30)
            tradeable = df.between_time("10:00", "15:30")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.BB_PERIOD + self.SQUEEZE_LOOKBACK:
                    continue

                w = bb_width.iloc[idx]
                if pd.isna(w):
                    continue

                # Check squeeze : width actuelle parmi les plus basses sur lookback
                recent_widths = bb_width.iloc[max(0, idx - self.SQUEEZE_LOOKBACK):idx]
                if recent_widths.empty:
                    continue
                pct_rank = (recent_widths < w).sum() / len(recent_widths) * 100

                if pct_rank > self.WIDTH_PERCENTILE:
                    continue  # Pas un squeeze

                # Volume confirmation
                if pd.isna(vol_r.iloc[idx]) or vol_r.iloc[idx] < self.VOLUME_MULT:
                    continue

                entry_price = bar["close"]
                u = upper.iloc[idx]
                l = lower.iloc[idx]

                if pd.isna(u) or pd.isna(l):
                    continue

                # VWAP direction
                vwap_now = vwap_vals.iloc[idx] if idx < len(vwap_vals) else middle.iloc[idx]

                if entry_price > u and entry_price > vwap_now:
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vol_squeeze", "bb_width": round(w, 5)}))
                    trades_today += 1
                    signal_found = True
                elif entry_price < l and entry_price < vwap_now:
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vol_squeeze", "bb_width": round(w, 5)}))
                    trades_today += 1
                    signal_found = True

        return signals
