"""
Strategie : Mean Reversion 3-Sigma

Edge structurel :
Bollinger Bands a 3.0 std + RSI(7) < 15 ou > 85 = extremes statistiques.
Tres selectif (~1-2 trades/jour). Le prix revient quasi-systematiquement
vers la bande mediane apres un ecart de 3 sigmas.

Iteration barre par barre. Stop 1%, target = middle band.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, bollinger_bands, adx, volume_ratio


LEVERAGED_ETFS = {
    "SQQQ", "TQQQ", "SOXL", "SOXS", "UVXY", "SVXY", "SPXU", "SPXS",
    "UPRO", "TZA", "TNA", "LABU", "LABD", "NUGT", "DUST", "JNUG", "JDST",
    "FAS", "FAZ", "ERX", "ERY", "TECL", "TECS", "CURE", "DRIP", "GUSH",
    "UCO", "SCO", "BOIL", "KOLD", "UDOW", "SDOW", "FNGU", "FNGD",
}


class MeanReversion3SigmaStrategy(BaseStrategy):
    name = "Mean Reversion 3-Sigma"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    BB_PERIOD = 20
    BB_STD = 3.0
    RSI_PERIOD = 7
    RSI_LONG_THRESHOLD = 15      # RSI < 15 pour LONG
    RSI_SHORT_THRESHOLD = 85     # RSI > 85 pour SHORT
    STOP_PCT = 0.01              # 1% stop loss
    ADX_MAX = 30                 # Pas de trend trop fort
    VOL_RATIO_MIN = 1.5          # Volume confirmation

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < self.BB_PERIOD + 15:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()

            # Indicateurs
            upper, middle, lower = bollinger_bands(df["close"], self.BB_PERIOD, self.BB_STD)
            df["bb_upper"] = upper
            df["bb_middle"] = middle
            df["bb_lower"] = lower
            df["rsi"] = rsi(df["close"], self.RSI_PERIOD)
            df["adx"] = adx(df, 14)
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:00-15:30 pour warmup indicateurs)
            tradeable = df.between_time("10:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar["bb_upper"]) or pd.isna(bar["rsi"]) or pd.isna(bar["adx"]):
                    continue

                # Filtre : pas de trend trop fort
                if bar["adx"] > self.ADX_MAX:
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]

                # LONG : prix sous bande basse + RSI extreme bas
                if entry_price <= bar["bb_lower"] and bar["rsi"] < self.RSI_LONG_THRESHOLD:
                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = bar["bb_middle"]
                    # Verifier que le target est au-dessus de l'entree
                    if take_profit <= entry_price:
                        continue
                    score = self.RSI_LONG_THRESHOLD - bar["rsi"]
                    candidates.append({
                        "score": score,
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "rsi": round(bar["rsi"], 1),
                                "adx": round(bar["adx"], 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                                "bb_dist_pct": round((bar["bb_lower"] - entry_price) / entry_price * 100, 2),
                            },
                        ),
                    })
                    signal_found = True

                # SHORT : prix au-dessus bande haute + RSI extreme haut
                elif entry_price >= bar["bb_upper"] and bar["rsi"] > self.RSI_SHORT_THRESHOLD:
                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = bar["bb_middle"]
                    # Verifier que le target est sous l'entree
                    if take_profit >= entry_price:
                        continue
                    score = bar["rsi"] - self.RSI_SHORT_THRESHOLD
                    candidates.append({
                        "score": score,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "rsi": round(bar["rsi"], 1),
                                "adx": round(bar["adx"], 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                                "bb_dist_pct": round((entry_price - bar["bb_upper"]) / entry_price * 100, 2),
                            },
                        ),
                    })
                    signal_found = True

        # Trier par score (RSI le plus extreme) et prendre les meilleurs
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
