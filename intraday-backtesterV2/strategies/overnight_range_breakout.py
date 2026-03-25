"""
Strategie : Overnight Range Breakout

Edge structurel :
Quand le range de la premiere heure est <60% du range moyen (20 jours),
le marche est "comprime" (coiled spring). La sortie du range de la premiere
heure genere un mouvement fort dans la direction du breakout.

Iteration barre par barre. Stop = opposite end du first hour range.
Target = 1.5x le range.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class OvernightRangeBreakoutStrategy(BaseStrategy):
    name = "Overnight Range Breakout"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    RANGE_COMPRESSION_THRESHOLD = 0.60  # Range < 60% de la moyenne
    TARGET_MULTIPLIER = 1.5             # Target = 1.5x le range
    LOOKBACK_DAYS = 20                  # Moyenne du range sur 20 jours
    VOL_RATIO_MIN = 1.2                 # Volume confirmation au breakout
    MAX_RISK_PCT = 0.03                 # Max 3% de risque

    def _get_first_hour_ranges(self, df: pd.DataFrame, current_date) -> list[float]:
        """Calcule les ranges de la premiere heure pour les N derniers jours."""
        all_dates = sorted(set(df.index.date))
        prev_dates = [d for d in all_dates if d < current_date]
        if not prev_dates:
            return []

        ranges = []
        for d in prev_dates[-self.LOOKBACK_DAYS:]:
            day_df = df[df.index.date == d]
            first_hour = day_df.between_time("09:30", "10:29")
            if len(first_hour) >= 3:
                fh_range = first_hour["high"].max() - first_hour["low"].min()
                if fh_range > 0:
                    ranges.append(fh_range)
        return ranges

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if len(df) < 30:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            day_bars = df[df.index.date == date]
            if len(day_bars) < 15:
                continue

            # Calculer le range de la premiere heure aujourd'hui
            first_hour = day_bars.between_time("09:30", "10:29")
            if len(first_hour) < 3:
                continue

            fh_high = first_hour["high"].max()
            fh_low = first_hour["low"].min()
            fh_range = fh_high - fh_low

            if fh_range <= 0:
                continue

            # Comparer au range moyen historique
            hist_ranges = self._get_first_hour_ranges(df, date)
            if len(hist_ranges) < 5:
                continue

            avg_range = np.mean(hist_ranges)
            if avg_range <= 0:
                continue

            compression_ratio = fh_range / avg_range
            if compression_ratio >= self.RANGE_COMPRESSION_THRESHOLD:
                continue  # Range pas assez comprime

            # Calculer les indicateurs
            df_copy = df.copy()
            df_copy["vol_ratio"] = volume_ratio(df_copy["volume"], 20)

            # Iterer barre par barre apres la premiere heure (10:30+)
            tradeable = day_bars.between_time("10:30", "15:00")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df_copy.index.get_loc(ts)
                vol_r = df_copy["vol_ratio"].iloc[idx]

                # Filtre volume au breakout
                if pd.isna(vol_r) or vol_r < self.VOL_RATIO_MIN:
                    continue

                entry_price = bar["close"]

                # LONG breakout : close au-dessus du high de la premiere heure
                if entry_price > fh_high:
                    stop_loss = fh_low  # Stop = bas du range
                    risk = entry_price - stop_loss
                    if risk <= 0 or risk / entry_price > self.MAX_RISK_PCT:
                        continue
                    take_profit = entry_price + fh_range * self.TARGET_MULTIPLIER

                    candidates.append({
                        "score": (1 - compression_ratio) * vol_r,  # Plus comprime + plus de volume = meilleur
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "compression_ratio": round(compression_ratio, 2),
                                "fh_range": round(fh_range, 2),
                                "avg_range": round(avg_range, 2),
                                "vol_ratio": round(vol_r, 1),
                            },
                        ),
                    })
                    signal_found = True
                    continue

                # SHORT breakout : close en-dessous du low de la premiere heure
                if entry_price < fh_low:
                    stop_loss = fh_high  # Stop = haut du range
                    risk = stop_loss - entry_price
                    if risk <= 0 or risk / entry_price > self.MAX_RISK_PCT:
                        continue
                    take_profit = entry_price - fh_range * self.TARGET_MULTIPLIER

                    candidates.append({
                        "score": (1 - compression_ratio) * vol_r,
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "compression_ratio": round(compression_ratio, 2),
                                "fh_range": round(fh_range, 2),
                                "avg_range": round(avg_range, 2),
                                "vol_ratio": round(vol_r, 1),
                            },
                        ),
                    })
                    signal_found = True

        # Trier par score et prendre les meilleurs
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
