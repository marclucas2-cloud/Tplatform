"""
Strategie : MACD Divergence

Edge structurel :
Divergence entre le prix et l'histogramme MACD signale un epuisement du mouvement.
Quand le prix fait un nouveau high mais le MACD histogramme decline (bearish divergence),
ou quand le prix fait un nouveau low mais le MACD histogramme remonte (bullish divergence),
un retournement est probable.

Iteration barre par barre. Stop 0.7%, target 1.0%.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class MACDDivergenceStrategy(BaseStrategy):
    name = "MACD Divergence"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    LOOKBACK = 20               # Fenetre pour chercher les pivots
    STOP_PCT = 0.007            # 0.7%
    TARGET_PCT = 0.010          # 1.0%
    VOL_RATIO_MIN = 1.2

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        """Calcule MACD line, signal line, et histogramme."""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < self.MACD_SLOW + self.LOOKBACK + 10:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()
            close = df["close"]

            macd_line, signal_line, histogram = self._macd(
                close, self.MACD_FAST, self.MACD_SLOW, self.MACD_SIGNAL
            )
            df["macd_hist"] = histogram
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre (10:30 pour warmup MACD)
            tradeable = df.between_time("10:30", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.MACD_SLOW + self.LOOKBACK:
                    continue

                current_hist = df["macd_hist"].iloc[idx]
                if pd.isna(current_hist):
                    continue

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]

                # Fenetre de lookback
                price_window = close.iloc[max(0, idx - self.LOOKBACK):idx + 1]
                hist_window = df["macd_hist"].iloc[max(0, idx - self.LOOKBACK):idx + 1]

                if len(price_window) < 5:
                    continue

                # Bearish divergence : prix near high, histogramme en baisse
                price_near_high = entry_price >= price_window.max() * 0.998
                if price_near_high:
                    # Histogramme doit avoir ete plus haut avant
                    prev_hist_max = hist_window.iloc[:-3].max()
                    if not pd.isna(prev_hist_max) and prev_hist_max > 0:
                        if current_hist < prev_hist_max * 0.7:  # Hist a baisse de >30%
                            stop_loss = entry_price * (1 + self.STOP_PCT)
                            take_profit = entry_price * (1 - self.TARGET_PCT)
                            score = (prev_hist_max - current_hist) / abs(prev_hist_max) if prev_hist_max != 0 else 0
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
                                        "type": "bearish_div",
                                        "macd_hist": round(current_hist, 4),
                                        "prev_hist_max": round(prev_hist_max, 4),
                                        "vol_ratio": round(bar["vol_ratio"], 1),
                                    },
                                ),
                            })
                            signal_found = True
                            continue

                # Bullish divergence : prix near low, histogramme en hausse
                price_near_low = entry_price <= price_window.min() * 1.002
                if price_near_low:
                    prev_hist_min = hist_window.iloc[:-3].min()
                    if not pd.isna(prev_hist_min) and prev_hist_min < 0:
                        if current_hist > prev_hist_min * 0.7:  # Hist a remonte de >30%
                            stop_loss = entry_price * (1 - self.STOP_PCT)
                            take_profit = entry_price * (1 + self.TARGET_PCT)
                            score = (current_hist - prev_hist_min) / abs(prev_hist_min) if prev_hist_min != 0 else 0
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
                                        "type": "bullish_div",
                                        "macd_hist": round(current_hist, 4),
                                        "prev_hist_min": round(prev_hist_min, 4),
                                        "vol_ratio": round(bar["vol_ratio"], 1),
                                    },
                                ),
                            })
                            signal_found = True

        # Trier par score de divergence et prendre les meilleurs
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
