"""
Strategie : Volume Profile Point of Control (POC)

Edge structurel :
Le Point of Control est le niveau de prix ou le plus de volume a ete echange.
Le prix tend a revenir vers le POC — c'est l'attracteur principal du volume profile.
Apres 11:00, quand le prix s'eloigne de >0.5% du POC, on trade le mean reversion.

Iteration barre par barre. Stop 0.7%, target = POC.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class VolumeProfilePOCStrategy(BaseStrategy):
    name = "Volume Profile POC"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    POC_DISTANCE_PCT = 0.005    # Prix doit etre >0.5% du POC
    STOP_PCT = 0.007            # 0.7% stop
    VOL_RATIO_MIN = 1.2         # Volume minimum pour confirmer
    NUM_PRICE_BINS = 50         # Resolution du volume profile

    def _calculate_poc(self, df: pd.DataFrame) -> float:
        """Calcule le Point of Control : prix avec le plus de volume."""
        if df.empty or df["volume"].sum() == 0:
            return np.nan

        price_low = df["low"].min()
        price_high = df["high"].max()
        if price_high == price_low:
            return price_low

        # Creer des bins de prix et distribuer le volume
        bins = np.linspace(price_low, price_high, self.NUM_PRICE_BINS + 1)
        bin_volume = np.zeros(self.NUM_PRICE_BINS)

        for _, bar in df.iterrows():
            # Le volume de chaque barre est distribue uniformement entre low et high
            bar_low = bar["low"]
            bar_high = bar["high"]
            bar_vol = bar["volume"]
            if bar_vol <= 0 or bar_high == bar_low:
                # Volume concentre sur le close
                bin_idx = np.searchsorted(bins, bar["close"]) - 1
                bin_idx = max(0, min(bin_idx, self.NUM_PRICE_BINS - 1))
                bin_volume[bin_idx] += bar_vol
                continue

            for i in range(self.NUM_PRICE_BINS):
                bin_lo = bins[i]
                bin_hi = bins[i + 1]
                # Overlap entre [bar_low, bar_high] et [bin_lo, bin_hi]
                overlap_lo = max(bar_low, bin_lo)
                overlap_hi = min(bar_high, bin_hi)
                if overlap_hi > overlap_lo:
                    fraction = (overlap_hi - overlap_lo) / (bar_high - bar_low)
                    bin_volume[i] += bar_vol * fraction

        # POC = milieu du bin avec le plus de volume
        poc_bin = np.argmax(bin_volume)
        poc_price = (bins[poc_bin] + bins[poc_bin + 1]) / 2
        return poc_price

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < 20:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            df = df.copy()
            df["vol_ratio"] = volume_ratio(df["volume"], 20)

            # Iterer barre par barre apres 11:00 (assez de volume profile)
            tradeable = df.between_time("11:00", "15:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                # Calculer le POC avec toutes les barres jusqu'a maintenant
                idx = df.index.get_loc(ts)
                bars_so_far = df.iloc[:idx + 1]
                poc = self._calculate_poc(bars_so_far)

                if pd.isna(poc) or poc <= 0:
                    continue

                entry_price = bar["close"]
                distance_pct = (entry_price - poc) / poc

                # Filtre volume
                if pd.isna(bar["vol_ratio"]) or bar["vol_ratio"] < self.VOL_RATIO_MIN:
                    continue

                # LONG : prix sous le POC de >0.5%
                if distance_pct < -self.POC_DISTANCE_PCT:
                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = poc  # Target = retour au POC
                    if take_profit <= entry_price:
                        continue
                    score = abs(distance_pct)
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
                                "poc": round(poc, 2),
                                "distance_pct": round(distance_pct * 100, 2),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True

                # SHORT : prix au-dessus du POC de >0.5%
                elif distance_pct > self.POC_DISTANCE_PCT:
                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = poc  # Target = retour au POC
                    if take_profit >= entry_price:
                        continue
                    score = abs(distance_pct)
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
                                "poc": round(poc, 2),
                                "distance_pct": round(distance_pct * 100, 2),
                                "vol_ratio": round(bar["vol_ratio"], 1),
                            },
                        ),
                    })
                    signal_found = True

        # Trier par distance au POC (plus loin = meilleur setup)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
