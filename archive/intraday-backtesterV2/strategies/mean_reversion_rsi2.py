"""
Strategie : Mean Reversion RSI(2)

Edge structurel :
Le RSI avec une periode ultra-courte de 2 barres detecte les micro-reversions
extremes. Quand RSI(2) atteint < 5 ou > 95, le prix est statistiquement
en overextension et tend a revert vers la moyenne rapidement.

Connors RSI(2) est un indicateur bien documente dans la litterature quantitative.
L'edge est que les mouvements extremes sur 2 barres sont souvent du bruit
de marche et non du signal directionnel.

Regles :
- RSI(2) < 5 → LONG (oversold extreme)
- RSI(2) > 95 → SHORT (overbought extreme)
- Exit quand RSI(2) revient vers 50 → approxime par target de 0.5%
- Stop : 1%
- Min volume : barre > 1.5x moyenne
- Max 3 trades/jour, prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi
import config


LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
RSI_PERIOD = 2
RSI_OVERSOLD = 5
RSI_OVERBOUGHT = 95
STOP_PCT = 0.01          # 1%
TARGET_PCT = 0.005        # 0.5% (quick mean reversion)
MIN_VOL_RATIO = 1.5


class MeanReversionRSI2Strategy(BaseStrategy):
    name = "Mean Reversion RSI(2)"

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        rsi_oversold: float = RSI_OVERSOLD,
        rsi_overbought: float = RSI_OVERBOUGHT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        min_vol_ratio: float = MIN_VOL_RATIO,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_vol_ratio = min_vol_ratio
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 15:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            df = df.copy()

            # Calculer RSI(2) et volume ratio
            df["rsi2"] = rsi(df["close"], period=self.rsi_period)
            df["vol_avg"] = df["volume"].rolling(20, min_periods=5).mean()
            df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

            # Scanner apres 10:00 (RSI(2) a besoin de quelques barres de warmup)
            tradeable = df.between_time("10:00", "15:30")
            if tradeable.empty:
                continue

            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                rsi_val = bar.get("rsi2", np.nan)
                vol_r = bar.get("vol_ratio", np.nan)

                if pd.isna(rsi_val) or pd.isna(vol_r):
                    continue

                # Filtre volume minimum
                if vol_r < self.min_vol_ratio:
                    continue

                entry_price = bar["close"]

                # RSI(2) oversold extreme → LONG
                if rsi_val < self.rsi_oversold:
                    stop_loss = entry_price * (1 - self.stop_pct)
                    take_profit = entry_price * (1 + self.target_pct)
                    action = "LONG"
                    score = self.rsi_oversold - rsi_val  # Plus extreme = meilleur

                # RSI(2) overbought extreme → SHORT
                elif rsi_val > self.rsi_overbought:
                    stop_loss = entry_price * (1 + self.stop_pct)
                    take_profit = entry_price * (1 - self.target_pct)
                    action = "SHORT"
                    score = rsi_val - self.rsi_overbought

                else:
                    continue

                candidates.append({
                    "score": score * vol_r,
                    "signal": Signal(
                        action=action,
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi2": round(rsi_val, 2),
                            "vol_ratio": round(vol_r, 2),
                        },
                    ),
                })
                signal_found = True

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
