"""
Strategie : Pre-Market Volume Leader

Edge structurel :
Les actions avec un volume anormalement eleve sur la premiere barre (proxy pre-market)
ont une forte probabilite de continuer dans la direction d'ouverture.
On identifie les top 3 par volume ratio (premiere barre vs moyenne 20 barres),
puis on attend la confirmation apres 9:40 (2 barres 5M).

Iteration barre par barre. Stop 0.8%, target 1.2%.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


class PreMarketVolumeLeaderStrategy(BaseStrategy):
    name = "Pre-Market Volume Leader"

    MAX_TRADES_PER_DAY = 3
    MIN_PRICE = 10.0
    VOL_RATIO_THRESHOLD = 3.0   # Volume premiere barre > 3x moyenne
    STOP_PCT = 0.008            # 0.8%
    TARGET_PCT = 0.012          # 1.2%
    CONFIRMATION_BARS = 2       # Attendre 2 barres de confirmation

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        # Phase 1 : Scanner le volume ratio de la premiere barre pour chaque ticker
        volume_leaders = []

        for ticker, df in data.items():
            if len(df) < 25:
                continue
            if df.iloc[0]["open"] < self.MIN_PRICE:
                continue

            # Volume de la premiere barre (9:30)
            first_bars = df.between_time("09:30", "09:34")
            if first_bars.empty:
                continue

            first_bar_vol = first_bars.iloc[0]["volume"]
            if first_bar_vol <= 0:
                continue

            # Moyenne du volume sur les 20 premieres barres connues
            vol_avg = df["volume"].iloc[:20].mean()
            if vol_avg <= 0:
                continue

            vol_ratio_val = first_bar_vol / vol_avg
            if vol_ratio_val < self.VOL_RATIO_THRESHOLD:
                continue

            # Direction de la premiere barre
            first_bar = first_bars.iloc[0]
            opening_direction = "UP" if first_bar["close"] > first_bar["open"] else "DOWN"

            volume_leaders.append({
                "ticker": ticker,
                "vol_ratio": vol_ratio_val,
                "direction": opening_direction,
                "df": df,
            })

        # Trier par volume ratio et garder les top 3
        volume_leaders.sort(key=lambda x: x["vol_ratio"], reverse=True)
        top_leaders = volume_leaders[:self.MAX_TRADES_PER_DAY * 2]  # Pool plus large

        # Phase 2 : Chercher la confirmation barre par barre
        candidates = []

        for leader in top_leaders:
            ticker = leader["ticker"]
            df = leader["df"]
            direction = leader["direction"]

            # Iterer barre par barre apres 9:40 (confirmation)
            tradeable = df.between_time("09:40", "10:30")
            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.CONFIRMATION_BARS + 1:
                    continue

                entry_price = bar["close"]

                # Confirmation : prix continue dans la direction du gap
                if direction == "UP":
                    # Les N dernieres barres doivent confirmer (close > open)
                    recent = df.iloc[max(0, idx - self.CONFIRMATION_BARS):idx + 1]
                    confirmed = all(recent["close"] > recent["open"])
                    if not confirmed:
                        continue

                    stop_loss = entry_price * (1 - self.STOP_PCT)
                    take_profit = entry_price * (1 + self.TARGET_PCT)

                    candidates.append({
                        "score": leader["vol_ratio"],
                        "signal": Signal(
                            action="LONG",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "vol_ratio_first_bar": round(leader["vol_ratio"], 1),
                                "direction": direction,
                            },
                        ),
                    })
                    signal_found = True

                else:  # DOWN
                    recent = df.iloc[max(0, idx - self.CONFIRMATION_BARS):idx + 1]
                    confirmed = all(recent["close"] < recent["open"])
                    if not confirmed:
                        continue

                    stop_loss = entry_price * (1 + self.STOP_PCT)
                    take_profit = entry_price * (1 - self.TARGET_PCT)

                    candidates.append({
                        "score": leader["vol_ratio"],
                        "signal": Signal(
                            action="SHORT",
                            ticker=ticker,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "vol_ratio_first_bar": round(leader["vol_ratio"], 1),
                                "direction": direction,
                            },
                        ),
                    })
                    signal_found = True

        # Trier par vol_ratio et prendre les top trades
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.MAX_TRADES_PER_DAY]]
