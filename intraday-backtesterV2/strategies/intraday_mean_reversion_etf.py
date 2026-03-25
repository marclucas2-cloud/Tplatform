"""
Strategie : Intraday Mean Reversion ETF

Edge structurel :
Les ETFs majeurs (SPY, QQQ, IWM, DIA) mean-revertent plus fiablement que
les actions individuelles car ils sont diversifies et soumis a l'arbitrage.
Quand un ETF s'eloigne de >1.5% du VWAP daily avec RSI < 30 ou > 70,
le retour vers le VWAP est tres probable.

Iteration barre par barre. Stop 0.5%, target = VWAP.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, vwap, volume_ratio


# ETFs cibles — grands, liquides, mean-revert bien
TARGET_ETFS = {"SPY", "QQQ", "IWM", "DIA"}


class IntradayMeanReversionETFStrategy(BaseStrategy):
    name = "Intraday Mean Reversion ETF"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    VWAP_DISTANCE_PCT = 0.015   # >1.5% du VWAP
    RSI_PERIOD = 14
    RSI_LONG_THRESHOLD = 30     # RSI < 30 pour LONG
    RSI_SHORT_THRESHOLD = 70    # RSI > 70 pour SHORT
    STOP_PCT = 0.005            # 0.5% stop (tight pour ETFs)
    VOL_RATIO_MIN = 1.0

    def get_required_tickers(self) -> list[str]:
        return list(TARGET_ETFS)

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            # Ne trader que les ETFs cibles
            if ticker not in TARGET_ETFS:
                continue
            if len(df) < 30:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()

            # Calculer VWAP et RSI
            df["vwap_val"] = vwap(df)
            df["rsi"] = rsi(df["close"], self.RSI_PERIOD)
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:30-15:30 — warmup VWAP + RSI)
            tradeable = df.between_time("10:30", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                vwap_val = bar["vwap_val"]
                rsi_val = bar["rsi"]

                if pd.isna(vwap_val) or pd.isna(rsi_val) or vwap_val <= 0:
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]

                # Distance au VWAP
                vwap_distance = (entry_price - vwap_val) / vwap_val

                # LONG : prix sous VWAP de >1.5% + RSI < 30
                if vwap_distance < -self.VWAP_DISTANCE_PCT and rsi_val < self.RSI_LONG_THRESHOLD:
                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = vwap_val  # Target = VWAP
                    if take_profit <= entry_price:
                        continue

                    score = abs(vwap_distance) + (self.RSI_LONG_THRESHOLD - rsi_val) / 100
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
                                "vwap": round(vwap_val, 2),
                                "vwap_dist_pct": round(vwap_distance * 100, 2),
                                "rsi": round(rsi_val, 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True

                # SHORT : prix au-dessus du VWAP de >1.5% + RSI > 70
                elif vwap_distance > self.VWAP_DISTANCE_PCT and rsi_val > self.RSI_SHORT_THRESHOLD:
                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = vwap_val  # Target = VWAP
                    if take_profit >= entry_price:
                        continue

                    score = abs(vwap_distance) + (rsi_val - self.RSI_SHORT_THRESHOLD) / 100
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
                                "vwap": round(vwap_val, 2),
                                "vwap_dist_pct": round(vwap_distance * 100, 2),
                                "rsi": round(rsi_val, 1),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True

        # Trier par score (distance VWAP + extremite RSI)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
