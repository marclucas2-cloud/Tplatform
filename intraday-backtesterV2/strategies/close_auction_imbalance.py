"""
Strategie : Close Auction Imbalance

Edge structurel :
Dans les 30 dernieres minutes (15:25-15:55 ET), les ordres MOC (Market-On-Close)
des institutionnels creent des imbalances temporaires. Quand le volume surge >2x
et que le prix se deplace de >0.3%, la dislocation est souvent exageree.
On fade le mouvement (mean reversion) car le prix revient vers le VWAP
avant le closing auction officiel.

Regles :
- Fenetre : 15:25-15:55 ET uniquement
- Volume de la barre > 2x la moyenne des 20 dernieres barres
- Move depuis le debut de la fenetre > 0.3%
- FADE le move (SHORT si prix a monte, LONG si prix a baisse)
- Stop : 0.5% — Target : 0.3%
- Max 3 trades/jour
- Prix > $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio
import config


# ETFs leverages a exclure
LEVERAGED_ETFS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "SPXU", "SPXS", "UPRO", "TNA", "TZA", "LABU", "LABD",
    "UCO", "SCO", "NVDL", "NVDX", "TSLL", "TSLQ",
}

# Parametres
MAX_TRADES_PER_DAY = 3
MIN_PRICE = 10.0
VOL_SURGE_MULT = 2.0
MOVE_THRESHOLD_PCT = 0.003   # 0.3%
STOP_PCT = 0.005             # 0.5%
TARGET_PCT = 0.003           # 0.3%


class CloseAuctionImbalanceStrategy(BaseStrategy):
    name = "Close Auction Imbalance"

    def __init__(
        self,
        vol_surge: float = VOL_SURGE_MULT,
        move_threshold: float = MOVE_THRESHOLD_PCT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.vol_surge = vol_surge
        self.move_threshold = move_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        candidates = []

        for ticker, df in data.items():
            if ticker in LEVERAGED_ETFS:
                continue
            if len(df) < 40:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # Volume ratio rolling 20 barres
            df = df.copy()
            df["vol_avg"] = df["volume"].rolling(20, min_periods=5).mean()
            df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)

            # Fenetre : 15:25-15:50 (on laisse 5 min avant la coupure 15:55)
            late_window = df.between_time("15:25", "15:50")
            if len(late_window) < 2:
                continue

            # Prix d'ancrage : premiere barre de la fenetre
            anchor_price = late_window.iloc[0]["close"]
            if anchor_price <= 0:
                continue

            signal_found = False

            for ts, bar in late_window.iterrows():
                if signal_found:
                    break

                # Filtre volume surge
                if pd.isna(bar.get("vol_ratio")) or bar["vol_ratio"] < self.vol_surge:
                    continue

                # Calculer le move depuis le debut de la fenetre
                move_pct = (bar["close"] - anchor_price) / anchor_price

                if abs(move_pct) < self.move_threshold:
                    continue

                entry_price = bar["close"]

                if move_pct > self.move_threshold:
                    # Prix a monte → FADE = SHORT
                    stop_loss = entry_price * (1 + self.stop_pct)
                    take_profit = entry_price * (1 - self.target_pct)
                    action = "SHORT"
                else:
                    # Prix a baisse → FADE = LONG
                    stop_loss = entry_price * (1 - self.stop_pct)
                    take_profit = entry_price * (1 + self.target_pct)
                    action = "LONG"

                score = abs(move_pct) * bar["vol_ratio"]
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
                            "move_pct": round(move_pct * 100, 3),
                            "vol_ratio": round(bar["vol_ratio"], 2),
                        },
                    ),
                })
                signal_found = True

        # Trier par score et garder les meilleurs
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return [c["signal"] for c in candidates[:self.max_trades_per_day]]
