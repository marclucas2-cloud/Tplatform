"""
Strategie : Range-Bound Scalp

Edge structurel :
Apres 11:00, quand le marche est en range (ADX < 20 et BB width < 0.5%),
les prix oscillent entre les bandes de Bollinger de maniere previsible.
On achete a la bande basse et on vend a la bande haute.

Tres tight stops (0.3%), petits targets (0.4%). High win-rate, low R:R.
Iteration barre par barre.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import bollinger_bands, adx, volume_ratio


class RangeBoundScalpStrategy(BaseStrategy):
    name = "Range-Bound Scalp"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    BB_PERIOD = 20
    BB_STD = 2.0
    ADX_MAX = 20                # ADX < 20 = marche en range
    BB_WIDTH_MAX = 0.005        # BB width < 0.5% du prix = range serre
    STOP_PCT = 0.003            # 0.3% stop (tight)
    TARGET_PCT = 0.004          # 0.4% target
    VOL_RATIO_MIN = 0.8         # Volume minimum (pas trop strict pour les ranges)
    BB_TOUCH_MARGIN = 0.001     # Marge pour "toucher" la bande (0.1%)

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < self.BB_PERIOD + 10:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()

            # Indicateurs
            upper, middle, lower = bollinger_bands(df["close"], self.BB_PERIOD, self.BB_STD)
            df["bb_upper"] = upper
            df["bb_middle"] = middle
            df["bb_lower"] = lower
            df["adx"] = adx(df, 14)
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre apres 11:00 (range se forme)
            tradeable = df.between_time("11:00", "15:30")
            signal_found = False
            trades_for_ticker = 0

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break
                if trades_for_ticker >= 1:  # 1 signal max par ticker
                    break

                if pd.isna(bar["bb_upper"]) or pd.isna(bar["adx"]):
                    continue

                # Filtre : ADX bas = range
                if bar["adx"] > self.ADX_MAX:
                    continue

                # Filtre : BB width serre
                bb_width = (bar["bb_upper"] - bar["bb_lower"]) / bar["bb_middle"] if bar["bb_middle"] > 0 else 1.0
                if bb_width > self.BB_WIDTH_MAX:
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]

                # LONG : prix pres de la bande basse
                lower_zone = bar["bb_lower"] * (1 + self.BB_TOUCH_MARGIN)
                if entry_price <= lower_zone:
                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = entry_price * (1 + self.TARGET_PCT)

                    candidates.append({
                        "score": (self.ADX_MAX - bar["adx"]) / self.ADX_MAX,  # ADX plus bas = meilleur
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "adx": round(bar["adx"], 1),
                                "bb_width_pct": round(bb_width * 100, 2),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True
                    trades_for_ticker += 1
                    continue

                # SHORT : prix pres de la bande haute
                upper_zone = bar["bb_upper"] * (1 - self.BB_TOUCH_MARGIN)
                if entry_price >= upper_zone:
                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = entry_price * (1 - self.TARGET_PCT)

                    candidates.append({
                        "score": (self.ADX_MAX - bar["adx"]) / self.ADX_MAX,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "adx": round(bar["adx"], 1),
                                "bb_width_pct": round(bb_width * 100, 2),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True
                    trades_for_ticker += 1

        # Trier par score (range le plus plat = meilleur)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
