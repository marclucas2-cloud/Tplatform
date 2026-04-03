"""
Stratégie 11 : ML Volume Profile Clustering
Utilise K-means pour identifier les "types de journées" et adapter la stratégie.

Concept (inspiré de Marcos López de Prado) :
- Chaque journée a un "profil" caractérisé par :
  - Distribution du volume intraday (front-loaded vs U-shape vs flat)
  - Volatilité première heure vs reste
  - Ratio high-to-open vs close-to-open
- K-means identifie 4-5 clusters de journées types
- Chaque cluster a une stratégie optimale différente

Clusters typiques attendus :
1. "Trend Day" : volume front-loaded, mouvement unidirectionnel
2. "Range Day" : volume U-shape, mean reversion fonctionne
3. "Reversal Day" : gap + fade
4. "Breakout Day" : faible volume matinal puis explosion
5. "Chop Day" : volume faible partout → ne pas trader
"""
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from backtest_engine import BaseStrategy, Signal
import config


class VolumeProfileClusterStrategy(BaseStrategy):
    name = "ML Volume Cluster"

    def __init__(self, n_clusters: int = 5, lookback_days: int = 60,
                 stop_pct: float = 0.004, target_pct: float = 0.008):
        self.n_clusters = n_clusters
        self.lookback_days = lookback_days
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.model = None
        self.scaler = StandardScaler()
        self.cluster_strategies = {}
        self._day_profiles = []

    def _extract_day_features(self, df: pd.DataFrame) -> dict:
        """Extrait les features caractéristiques d'une journée."""
        if len(df) < 10:
            return None

        total_vol = df["volume"].sum()
        if total_vol == 0:
            return None

        # Volume distribution
        morning = df.between_time("09:30", "11:30")
        midday = df.between_time("11:30", "14:00")
        afternoon = df.between_time("14:00", "16:00")

        vol_morning_pct = morning["volume"].sum() / total_vol if not morning.empty else 0
        vol_midday_pct = midday["volume"].sum() / total_vol if not midday.empty else 0
        vol_afternoon_pct = afternoon["volume"].sum() / total_vol if not afternoon.empty else 0

        # Price action
        day_open = df.iloc[0]["open"]
        day_close = df.iloc[-1]["close"]
        day_high = df["high"].max()
        day_low = df["low"].min()
        day_range = (day_high - day_low) / day_open if day_open > 0 else 0

        # Directionalité : close-to-open vs range
        close_to_open = (day_close - day_open) / day_open if day_open > 0 else 0
        directionality = abs(close_to_open) / day_range if day_range > 0 else 0

        # Volatilité première heure vs reste
        first_hour = df.between_time("09:30", "10:30")
        rest = df.between_time("10:30", "16:00")
        vol_first_hour = first_hour["close"].pct_change().std() if len(first_hour) > 2 else 0
        vol_rest = rest["close"].pct_change().std() if len(rest) > 2 else 0
        vol_ratio = vol_first_hour / vol_rest if vol_rest > 0 else 1

        # Gap
        gap = 0  # Sera calculé si on a les données daily

        return {
            "vol_morning_pct": vol_morning_pct,
            "vol_midday_pct": vol_midday_pct,
            "vol_afternoon_pct": vol_afternoon_pct,
            "day_range": day_range,
            "directionality": directionality,
            "close_to_open": close_to_open,
            "vol_ratio_first_hour": vol_ratio,
        }

    def _train_clusters(self, all_day_data: list[tuple]):
        """Entraîne K-means sur l'historique des profils journaliers."""
        features_list = []
        dates = []

        for date, df in all_day_data:
            feats = self._extract_day_features(df)
            if feats:
                features_list.append(feats)
                dates.append(date)

        if len(features_list) < self.n_clusters * 3:
            return False

        X = pd.DataFrame(features_list)
        X_scaled = self.scaler.fit_transform(X)

        self.model = KMeans(n_clusters=self.n_clusters, random_state=42, n_init=10)
        labels = self.model.fit_predict(X_scaled)

        # Analyser chaque cluster pour déterminer la stratégie optimale
        for cluster_id in range(self.n_clusters):
            cluster_mask = labels == cluster_id
            cluster_features = X[cluster_mask]

            avg_directionality = cluster_features["directionality"].mean()
            avg_range = cluster_features["day_range"].mean()
            avg_vol_morning = cluster_features["vol_morning_pct"].mean()

            if avg_directionality > 0.6 and avg_range > 0.015:
                strategy = "TREND_FOLLOW"  # Journée directionnelle
            elif avg_directionality < 0.3 and avg_range > 0.01:
                strategy = "MEAN_REVERSION"  # Range day
            elif avg_vol_morning > 0.5:
                strategy = "MORNING_MOMENTUM"  # Volume front-loaded
            elif avg_range < 0.008:
                strategy = "NO_TRADE"  # Chop day
            else:
                strategy = "BREAKOUT_WAIT"  # Attendre le breakout

            self.cluster_strategies[cluster_id] = strategy

        self._trained_dates = dates
        self._trained_labels = labels
        return True

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # On a besoin d'un historique pour entraîner — skip les premiers jours
        # En pratique, le modèle est entraîné une fois au début du backtest

        for ticker in ["SPY", "QQQ", "NVDA"]:
            if ticker not in data:
                continue

            df = data[ticker]
            feats = self._extract_day_features(df)
            if feats is None:
                continue

            # Si le modèle n'est pas entraîné, accumuler les données
            if self.model is None:
                self._day_profiles.append((date, df.copy()))
                if len(self._day_profiles) >= self.lookback_days:
                    if not self._train_clusters(self._day_profiles):
                        continue
                continue

            # Prédire le type de journée
            X = pd.DataFrame([feats])
            X_scaled = self.scaler.transform(X)
            cluster = self.model.predict(X_scaled)[0]
            day_type = self.cluster_strategies.get(cluster, "NO_TRADE")

            if day_type == "NO_TRADE":
                continue

            # Appliquer la stratégie appropriée
            tradeable = df.between_time("10:00", "15:30")
            if tradeable.empty:
                continue

            if day_type == "TREND_FOLLOW":
                # Entrer dans la direction du mouvement matinal
                morning_close = df.between_time("10:00", "10:05")
                if morning_close.empty:
                    continue
                morning_move = (morning_close.iloc[0]["close"] - df.iloc[0]["open"]) / df.iloc[0]["open"]

                if abs(morning_move) > 0.003:
                    entry = morning_close.iloc[0]["close"]
                    ts = morning_close.index[0]
                    action = "LONG" if morning_move > 0 else "SHORT"
                    signals.append(Signal(
                        action=action, ticker=ticker,
                        entry_price=entry,
                        stop_loss=entry * (1 - self.stop_pct) if action == "LONG" else entry * (1 + self.stop_pct),
                        take_profit=entry * (1 + self.target_pct) if action == "LONG" else entry * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={"strategy": self.name, "day_type": day_type,
                                  "cluster": int(cluster)},
                    ))

            elif day_type == "MEAN_REVERSION":
                # Chercher les extrêmes RSI intraday
                from utils.indicators import rsi
                df_copy = df.copy()
                df_copy["rsi"] = rsi(df_copy["close"], 7)

                for ts, bar in tradeable.iterrows():
                    if pd.isna(bar.get("rsi", np.nan)):
                        # Recalculer depuis df_copy
                        if ts in df_copy.index and not pd.isna(df_copy.loc[ts, "rsi"]):
                            rsi_val = df_copy.loc[ts, "rsi"]
                        else:
                            continue
                    else:
                        rsi_val = bar.get("rsi", 50)

                    if rsi_val < 25:
                        signals.append(Signal(
                            action="LONG", ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 - self.stop_pct),
                            take_profit=bar["close"] * (1 + self.target_pct * 0.5),
                            timestamp=ts,
                            metadata={"strategy": self.name, "day_type": day_type,
                                      "cluster": int(cluster)},
                        ))
                        break
                    elif rsi_val > 75:
                        signals.append(Signal(
                            action="SHORT", ticker=ticker,
                            entry_price=bar["close"],
                            stop_loss=bar["close"] * (1 + self.stop_pct),
                            take_profit=bar["close"] * (1 - self.target_pct * 0.5),
                            timestamp=ts,
                            metadata={"strategy": self.name, "day_type": day_type,
                                      "cluster": int(cluster)},
                        ))
                        break

        return signals
