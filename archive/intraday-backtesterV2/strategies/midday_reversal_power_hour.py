"""
Midday Reversal Power Hour

Edge : Meme logique que Midday Reversal mais appliquee au power hour (14:00-15:30 ET).
Les mouvements du jour se retracent souvent dans la derniere heure et demie.
Si un ticker a bouge > 2.5% depuis l'open et montre un pattern de renversement
(RSI extreme + volume en baisse), on entre en contre-tendance.

Stop 0.8%, target 1.2%. Max 2 trades/jour.
Iteration barre par barre 14:00-15:30.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, volume_ratio


class MiddayReversalPowerHourStrategy(BaseStrategy):
    name = "Midday Rev Power Hour"

    MIN_DAY_MOVE = 0.025       # 2.5% move minimum depuis l'open
    MAX_RETRACEMENT = 0.50     # Pas deja retrace > 50%
    STOP_PCT = 0.008           # 0.8%
    TARGET_PCT = 0.012         # 1.2%
    MIN_PRICE = 10.0
    RSI_PERIOD = 14
    VOL_DECLINE_RATIO = 0.8    # Volume actuel < 80% du volume moyen = declining
    MAX_TRADES_PER_DAY = 2

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
            day_open = df.iloc[0]["open"]
            day_high = df["high"].max()
            day_low = df["low"].min()

            # Calculer le mouvement depuis l'open avant 14:00
            pre_power = df[df.index.time < dt_time(14, 0)]
            if pre_power.empty or len(pre_power) < 10:
                continue

            pre_close = pre_power.iloc[-1]["close"]
            day_move = (pre_close - day_open) / day_open

            if abs(day_move) < self.MIN_DAY_MOVE:
                continue

            # Indicateurs sur le df complet
            rsi_vals = rsi(df["close"], self.RSI_PERIOD)
            vol_ratio_vals = volume_ratio(df["volume"], 20)

            signal_found = False

            # Scanner le power hour (14:00-15:30 ET)
            power_hour = df.between_time("14:00", "15:30")
            for ts, bar in power_hour.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                entry_price = bar["close"]
                rsi_now = rsi_vals.iloc[idx] if idx < len(rsi_vals) else 50
                vol_r = vol_ratio_vals.iloc[idx] if idx < len(vol_ratio_vals) else 1.0

                if pd.isna(rsi_now) or pd.isna(vol_r):
                    continue

                # Volume en baisse = essoufflement du mouvement
                if vol_r > self.VOL_DECLINE_RATIO:
                    continue

                if day_move > 0:
                    # Journee haussiere -> reversal SHORT pendant power hour
                    # RSI doit etre eleve (overbought)
                    if rsi_now < 60:
                        continue
                    retracement = (day_high - entry_price) / (day_high - day_open) if (day_high - day_open) > 0 else 0
                    if 0.05 < retracement < self.MAX_RETRACEMENT:
                        stop = entry_price * (1 + self.STOP_PCT)
                        target = entry_price * (1 - self.TARGET_PCT)
                        signals.append(Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop,
                            take_profit=target,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "day_move_pct": round(day_move * 100, 2),
                                "rsi": round(rsi_now, 1),
                                "vol_ratio": round(vol_r, 2),
                            },
                        ))
                        trades_today += 1
                        signal_found = True

                else:
                    # Journee baissiere -> reversal LONG pendant power hour
                    # RSI doit etre bas (oversold)
                    if rsi_now > 40:
                        continue
                    retracement = (entry_price - day_low) / (day_open - day_low) if (day_open - day_low) > 0 else 0
                    if 0.05 < retracement < self.MAX_RETRACEMENT:
                        stop = entry_price * (1 - self.STOP_PCT)
                        target = entry_price * (1 + self.TARGET_PCT)
                        signals.append(Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop,
                            take_profit=target,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "day_move_pct": round(day_move * 100, 2),
                                "rsi": round(rsi_now, 1),
                                "vol_ratio": round(vol_r, 2),
                            },
                        ))
                        trades_today += 1
                        signal_found = True

        return signals
