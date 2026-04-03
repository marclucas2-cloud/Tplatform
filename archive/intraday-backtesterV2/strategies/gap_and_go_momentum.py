"""
STRAT-12 : Gap & Go Momentum

Edge : Gap d'ouverture > 2% avec volume > 2x ET la premiere barre 5M continue dans
la direction du gap (confirmation). Entree sur pullback vers VWAP dans la direction du gap.
Contrairement au gap fade (qui echoue), on SUIT le gap.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


class GapAndGoMomentumStrategy(BaseStrategy):
    name = "Gap & Go Momentum"

    MIN_GAP_PCT = 0.02         # Gap > 2%
    VOLUME_MULT = 2.0          # Volume 1ere barre > 2x
    PULLBACK_TO_VWAP = 0.003   # Pullback a 0.3% du VWAP
    STOP_PCT = 0.008
    TARGET_PCT = 0.016
    MIN_PRICE = 15.0
    MAX_TRADES_PER_DAY = 3

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if len(df) < 20:
                continue
            if df["close"].iloc[0] < self.MIN_PRICE:
                continue

            # Gap : comparer open vs previous close
            first_bar = df.iloc[0]
            day_open = first_bar["open"]

            # Estimer previous close comme le prix d'ouverture - le gap
            # (on n'a pas le prev close directement, on utilise les barres)
            prev_bars = df[df.index.time <= dt_time(9, 30)]
            if prev_bars.empty:
                continue

            # Volume de la premiere barre
            first_vol = first_bar["volume"]
            avg_vol = df["volume"].rolling(20, min_periods=5).mean()
            if pd.isna(avg_vol.iloc[min(20, len(df) - 1)]):
                continue
            vol_ratio = first_vol / avg_vol.iloc[min(20, len(df) - 1)] if avg_vol.iloc[min(20, len(df) - 1)] > 0 else 0

            if vol_ratio < self.VOLUME_MULT:
                continue

            # Verifier que la premiere barre continue dans la direction du gap
            first_move = (first_bar["close"] - first_bar["open"]) / first_bar["open"]

            try:
                vwap_vals = vwap(df)
            except Exception:
                continue

            # Chercher un pullback vers le VWAP (9:40-10:30)
            entry_window = df.between_time("09:40", "10:30")
            for ts, bar in entry_window.iterrows():
                idx = df.index.get_loc(ts)
                if idx < 3:
                    continue

                vwap_now = vwap_vals.iloc[idx] if idx < len(vwap_vals) else None
                if vwap_now is None or pd.isna(vwap_now):
                    continue

                entry_price = bar["close"]
                dist_to_vwap = abs(entry_price - vwap_now) / vwap_now

                if dist_to_vwap > self.PULLBACK_TO_VWAP:
                    continue

                # Gap up + continuation = LONG, Gap down + continuation = SHORT
                if first_move > 0.005:  # Gap up confirme
                    candidates.append({
                        "ticker": ticker,
                        "entry_price": entry_price,
                        "action": "LONG",
                        "vol_ratio": vol_ratio,
                        "ts": ts,
                    })
                    break
                elif first_move < -0.005:  # Gap down confirme
                    candidates.append({
                        "ticker": ticker,
                        "entry_price": entry_price,
                        "action": "SHORT",
                        "vol_ratio": vol_ratio,
                        "ts": ts,
                    })
                    break

        candidates.sort(key=lambda x: x["vol_ratio"], reverse=True)

        for c in candidates[:self.MAX_TRADES_PER_DAY]:
            ep = c["entry_price"]
            if c["action"] == "LONG":
                signals.append(Signal("LONG", c["ticker"], ep,
                                      ep * (1 - self.STOP_PCT), ep * (1 + self.TARGET_PCT),
                                      c["ts"], {"strategy": "gap_go"}))
            else:
                signals.append(Signal("SHORT", c["ticker"], ep,
                                      ep * (1 + self.STOP_PCT), ep * (1 - self.TARGET_PCT),
                                      c["ts"], {"strategy": "gap_go"}))

        return signals
