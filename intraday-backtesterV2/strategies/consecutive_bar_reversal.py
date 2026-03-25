"""
Strategie : Consecutive Bar Reversal

Edge structurel :
5+ bougies consecutives rouges (ou vertes) en 5M = epuisement du mouvement.
Le momentum s'essouffle, surtout si le volume decline pendant la sequence.
On fade a la 5eme barre. Plus il y a de barres, plus la reversal est probable.

Iteration barre par barre. Stop 0.7%, target 0.8%.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class ConsecutiveBarReversalStrategy(BaseStrategy):
    name = "Consecutive Bar Reversal"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    MIN_CONSECUTIVE = 5         # 5+ barres dans une direction
    STOP_PCT = 0.007            # 0.7%
    TARGET_PCT = 0.008          # 0.8%
    REQUIRE_DECLINING_VOLUME = True  # Volume doit decliner sur la sequence

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < self.MIN_CONSECUTIVE + 10:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:00-15:30)
            tradeable = df.between_time("10:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.MIN_CONSECUTIVE:
                    continue

                entry_price = bar["close"]

                # Compter les barres rouges consecutives (incluant la barre courante)
                consecutive_red = 0
                for i in range(idx, max(idx - 10, -1), -1):
                    if df["close"].iloc[i] < df["open"].iloc[i]:
                        consecutive_red += 1
                    else:
                        break

                # Compter les barres vertes consecutives
                consecutive_green = 0
                for i in range(idx, max(idx - 10, -1), -1):
                    if df["close"].iloc[i] > df["open"].iloc[i]:
                        consecutive_green += 1
                    else:
                        break

                # === LONG : 5+ barres rouges (fade the selling) ===
                if consecutive_red >= self.MIN_CONSECUTIVE:
                    # Verifier volume declinant
                    if self.REQUIRE_DECLINING_VOLUME:
                        vol_sequence = df["volume"].iloc[idx - consecutive_red + 1:idx + 1]
                        if len(vol_sequence) >= 3:
                            # Le volume de la derniere moitie doit etre inferieur a la premiere
                            mid = len(vol_sequence) // 2
                            first_half_vol = vol_sequence.iloc[:mid].mean()
                            second_half_vol = vol_sequence.iloc[mid:].mean()
                            if first_half_vol > 0 and second_half_vol >= first_half_vol:
                                continue  # Volume ne decline pas — skip

                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = entry_price * (1 + self.TARGET_PCT)

                    candidates.append({
                        "score": consecutive_red,  # Plus de barres = meilleur
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "direction": "fade_red",
                                "consecutive_bars": consecutive_red,
                            },
                        ),
                    })
                    signal_found = True
                    continue

                # === SHORT : 5+ barres vertes (fade the buying) ===
                if consecutive_green >= self.MIN_CONSECUTIVE:
                    if self.REQUIRE_DECLINING_VOLUME:
                        vol_sequence = df["volume"].iloc[idx - consecutive_green + 1:idx + 1]
                        if len(vol_sequence) >= 3:
                            mid = len(vol_sequence) // 2
                            first_half_vol = vol_sequence.iloc[:mid].mean()
                            second_half_vol = vol_sequence.iloc[mid:].mean()
                            if first_half_vol > 0 and second_half_vol >= first_half_vol:
                                continue

                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = entry_price * (1 - self.TARGET_PCT)

                    candidates.append({
                        "score": consecutive_green,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "direction": "fade_green",
                                "consecutive_bars": consecutive_green,
                            },
                        ),
                    })
                    signal_found = True

        # Trier par nombre de barres consecutives (plus = meilleur)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
