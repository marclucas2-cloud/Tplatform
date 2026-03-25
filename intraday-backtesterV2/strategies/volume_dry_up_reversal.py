"""
STRAT-07 : Volume Dry-Up Reversal

Edge : Quand le volume chute a < 0.5x la moyenne pendant un mouvement directionnel,
le mouvement s'epuise. Entree en reversal quand le volume revient > 1.2x.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, vwap, volume_ratio


class VolumeDryUpReversalStrategy(BaseStrategy):
    name = "Volume Dry-Up Reversal"

    DRY_UP_RATIO = 0.5         # Volume < 0.5x = dry up
    REVIVAL_RATIO = 1.2        # Volume revient > 1.2x
    MIN_MOVE_PCT = 0.008       # Move > 0.8% avant le dry up
    LOOKBACK = 12              # 12 barres lookback
    STOP_PCT = 0.006
    TARGET_PCT = 0.010
    MIN_PRICE = 10.0
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

            vol_r = volume_ratio(df["volume"], 20)
            rsi_vals = rsi(df["close"], 14)
            signal_found = False

            tradeable = df.between_time("10:00", "15:30")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.LOOKBACK + 5:
                    continue

                if pd.isna(vol_r.iloc[idx]):
                    continue

                # Phase 1 : Detecter un dry-up recent (dans les 3 dernieres barres)
                recent_dry = False
                for j in range(1, 4):
                    if idx - j >= 0 and not pd.isna(vol_r.iloc[idx - j]):
                        if vol_r.iloc[idx - j] < self.DRY_UP_RATIO:
                            recent_dry = True
                            break

                if not recent_dry:
                    continue

                # Phase 2 : Volume revival (barre actuelle)
                if vol_r.iloc[idx] < self.REVIVAL_RATIO:
                    continue

                # Phase 3 : Il y a eu un mouvement directionnel significatif
                lookback_prices = df["close"].iloc[max(0, idx - self.LOOKBACK):idx]
                if lookback_prices.empty:
                    continue

                move = (bar["close"] - lookback_prices.iloc[0]) / lookback_prices.iloc[0]

                if abs(move) < self.MIN_MOVE_PCT:
                    continue

                entry_price = bar["close"]

                if move > 0:
                    # Prix est monte + dry up + revival = reversal SHORT
                    stop = entry_price * (1 + self.STOP_PCT)
                    target = entry_price * (1 - self.TARGET_PCT)
                    signals.append(Signal("SHORT", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vol_dryup", "move_pct": round(move * 100, 2)}))
                else:
                    # Prix est descendu + dry up + revival = reversal LONG
                    stop = entry_price * (1 - self.STOP_PCT)
                    target = entry_price * (1 + self.TARGET_PCT)
                    signals.append(Signal("LONG", ticker, entry_price, stop, target, ts,
                                          {"strategy": "vol_dryup", "move_pct": round(move * 100, 2)}))

                trades_today += 1
                signal_found = True

        return signals
