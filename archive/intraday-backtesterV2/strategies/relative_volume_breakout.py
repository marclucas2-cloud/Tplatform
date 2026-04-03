"""
Strategie : Relative Volume Breakout

Edge structurel :
Quand le volume d'une barre est >3x sa moyenne des 20 dernieres barres ET que
la barre ferme pres de son high (>80% du range pour bullish) ou de son low
(<20% pour bearish), le momentum continue dans la direction de la barre.
C'est un signal de flux institutionnel agressif.

Regles :
- Volume de la barre > 3x la moyenne des 20 barres precedentes
- Close dans le top 20% du range (bullish) ou bottom 20% (bearish)
- Entry au close de la barre
- Stop : 0.7% — Target : 1.4% (R:R 1:2)
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
VOL_MULT_THRESHOLD = 3.0
CLOSE_HIGH_PCT = 0.80   # Close > 80% du range = bullish
CLOSE_LOW_PCT = 0.20    # Close < 20% du range = bearish
STOP_PCT = 0.007         # 0.7%
TARGET_PCT = 0.014       # 1.4%


class RelativeVolumeBreakoutStrategy(BaseStrategy):
    name = "Relative Volume Breakout"

    def __init__(
        self,
        vol_mult: float = VOL_MULT_THRESHOLD,
        close_high_pct: float = CLOSE_HIGH_PCT,
        close_low_pct: float = CLOSE_LOW_PCT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.vol_mult = vol_mult
        self.close_high_pct = close_high_pct
        self.close_low_pct = close_low_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 25:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # Calculer le volume ratio rolling
            df = df.copy()
            df["vol_avg"] = df["volume"].rolling(20, min_periods=10).mean()
            df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

            # Scanner apres 9:45 pour eviter le bruit de l'ouverture
            tradeable = df.between_time("09:45", "15:30")
            if tradeable.empty:
                continue

            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                if pd.isna(bar.get("vol_ratio")):
                    continue

                # Volume > 3x la moyenne
                if bar["vol_ratio"] < self.vol_mult:
                    continue

                # Position du close dans le range de la barre
                bar_range = bar["high"] - bar["low"]
                if bar_range <= 0:
                    continue

                close_position = (bar["close"] - bar["low"]) / bar_range

                entry_price = bar["close"]

                # Bullish : close pres du high
                if close_position > self.close_high_pct:
                    stop_loss = entry_price * (1 - self.stop_pct)
                    take_profit = entry_price * (1 + self.target_pct)
                    action = "LONG"
                # Bearish : close pres du low
                elif close_position < self.close_low_pct:
                    stop_loss = entry_price * (1 + self.stop_pct)
                    take_profit = entry_price * (1 - self.target_pct)
                    action = "SHORT"
                else:
                    continue  # Close au milieu = pas de signal clair

                score = bar["vol_ratio"] * abs(close_position - 0.5)

                candidates.append({
                    "score": score,
                    "signal": Signal(
                        action=action,
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "vol_ratio": round(bar["vol_ratio"], 2),
                            "close_position": round(close_position, 3),
                            "bar_range_pct": round(bar_range / entry_price * 100, 3),
                        },
                    ),
                })
                signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
