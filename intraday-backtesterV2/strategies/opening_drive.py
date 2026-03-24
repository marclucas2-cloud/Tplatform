"""
Stratégie 8 : Opening Drive Extended (9:40-12:00 ET)

Edge structurel :
Certains jours, le flux d'ouverture est massivement unidirectionnel — market
orders overnight, portfolio rebalancing, news réactions. Quand les 10 premières
minutes montrent un move > 0.5% dans une direction avec volume > 2x la moyenne
ET que le prix ne fait PAS de pullback > 30% du move initial, c'est un
"opening drive" qui tend à continuer pendant 1-2h.

Règles :
- Signal détecté à 9:40-9:45 ET. Entrée 9:45. Exit avant 12:00.
- LONG : close à 9:40 > open à 9:30 + 0.5%, pullback max < 30% du move,
  volume 9:30-9:40 > 2x la moyenne des premières 10 min.
- SHORT : close à 9:40 < open à 9:30 - 0.5%, pullback < 30%, volume > 2x.
- Stop : retour à l'open du jour (le drive a échoué).
- Target : 2x le move initial (9:30-9:40).
- Filtres : move >= 0.5%, pullback < 30%, ADX >= 15.
- Fréquence : 0-3 trades/jour, un seul signal par ticker par jour.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx
import config


class OpeningDriveStrategy(BaseStrategy):
    name = "Opening Drive Extended"

    def __init__(
        self,
        min_move_pct: float = 0.5,
        max_pullback_pct: float = 0.30,
        vol_multiplier: float = 2.0,
        target_multiplier: float = 2.0,
        adx_threshold: float = 15.0,
        min_volume: int = 1_000_000,
        min_atr_pct: float = 0.01,
        max_trades_per_day: int = 3,
    ):
        self.min_move_pct = min_move_pct / 100  # Convertir en décimal
        self.max_pullback_pct = max_pullback_pct
        self.vol_multiplier = vol_multiplier
        self.target_multiplier = target_multiplier
        self.adx_threshold = adx_threshold
        self.min_volume = min_volume
        self.min_atr_pct = min_atr_pct
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            if len(df) < 30:
                continue

            # ── Filtre volume : volume total du jour > 1M ──
            daily_volume = df["volume"].sum()
            if daily_volume < self.min_volume:
                continue

            # ── Filtre ATR : volatilité suffisante (> 1%) ──
            atr_pct = self._compute_atr_pct(df)
            if atr_pct is not None and atr_pct < self.min_atr_pct:
                continue

            # ── Récupérer l'open du jour (9:30) ──
            opening_bars = df.between_time("09:30", "09:31")
            if opening_bars.empty:
                continue
            day_open = opening_bars.iloc[0]["open"]

            # ── Barres des 10 premières minutes (9:30-9:40) ──
            first_10_bars = df.between_time("09:30", "09:39")
            if len(first_10_bars) < 2:
                continue

            close_at_940 = first_10_bars.iloc[-1]["close"]
            high_10 = first_10_bars["high"].max()
            low_10 = first_10_bars["low"].min()
            vol_10 = first_10_bars["volume"].sum()

            # ── Calcul du move initial ──
            move_pct = (close_at_940 - day_open) / day_open
            abs_move_pct = abs(move_pct)

            # ── Filtre : move >= 0.5% ──
            if abs_move_pct < self.min_move_pct:
                continue

            # ── Filtre volume : volume 9:30-9:40 > 2x la moyenne ──
            # Approximation : comparer le volume des 10 premières minutes
            # au volume moyen par barre sur le reste de la journée.
            # On utilise le nombre de barres dans la fenêtre comme base.
            num_first_bars = len(first_10_bars)
            # Volume moyen de la première barre (proxy de la moyenne historique)
            avg_bar_vol = first_10_bars["volume"].mean()
            # Le volume total doit être élevé comparé au nombre de barres
            # On compare au volume moyen global du jour
            all_day_avg = df["volume"].mean()
            if all_day_avg > 0 and avg_bar_vol < all_day_avg * self.vol_multiplier:
                continue

            # ── Filtre pullback : le prix n'a pas retraité > 30% du move ──
            if move_pct > 0:
                # Move haussier : pullback = (high - close) / (high - open)
                pullback = (high_10 - close_at_940) / (high_10 - day_open) if (high_10 - day_open) > 0 else 0
            else:
                # Move baissier : pullback = (close - low) / (open - low)
                pullback = (close_at_940 - low_10) / (day_open - low_10) if (day_open - low_10) > 0 else 0

            if pullback > self.max_pullback_pct:
                continue

            # ── Filtre ADX (anti-lookahead : on utilise les barres avant 9:40) ──
            # Si pas assez de barres pour l'ADX, on skip le filtre
            adx_series = adx(df.copy(), period=14)
            bars_before = adx_series[adx_series.index <= first_10_bars.index[-1]]
            if len(bars_before) >= 2:
                # ADX de la barre précédant la décision (shift 1)
                current_adx = bars_before.iloc[-2]
                if pd.isna(current_adx) or current_adx < self.adx_threshold:
                    continue

            # ── Trouver le timestamp d'entrée (première barre après 9:44) ──
            entry_window = df.between_time("09:44", "09:50")
            if entry_window.empty:
                continue
            entry_bar = entry_window.iloc[0]
            entry_ts = entry_window.index[0]
            entry_price = entry_bar["close"]

            # ── Direction et niveaux ──
            initial_move = abs(close_at_940 - day_open)

            if move_pct > 0:
                direction = "LONG"
                stop_loss = day_open  # Retour à l'open = drive échoué
                take_profit = entry_price + initial_move * self.target_multiplier
            else:
                direction = "SHORT"
                stop_loss = day_open  # Retour à l'open = drive échoué
                take_profit = entry_price - initial_move * self.target_multiplier

            candidates.append({
                "ticker": ticker,
                "direction": direction,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_ts": entry_ts,
                "abs_move_pct": abs_move_pct,
                "pullback": round(pullback, 3),
                "initial_move": round(initial_move, 4),
                "vol_ratio": round(avg_bar_vol / all_day_avg if all_day_avg > 0 else 0, 2),
            })

        # ── Trier par force du move, prendre les meilleurs ──
        candidates.sort(key=lambda x: x["abs_move_pct"], reverse=True)
        candidates = candidates[:self.max_trades_per_day]

        for c in candidates:
            signals.append(Signal(
                action=c["direction"],
                ticker=c["ticker"],
                entry_price=c["entry_price"],
                stop_loss=c["stop_loss"],
                take_profit=c["take_profit"],
                timestamp=c["entry_ts"],
                metadata={
                    "strategy": self.name,
                    "initial_move_pct": round(c["abs_move_pct"] * 100, 2),
                    "pullback_pct": round(c["pullback"] * 100, 1),
                    "initial_move_size": c["initial_move"],
                    "vol_ratio": c["vol_ratio"],
                },
            ))

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """
        Calcule l'ATR en pourcentage du prix moyen.
        Retourne None si pas assez de données.
        """
        if len(df) < period + 1:
            return None

        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(period).mean().iloc[-1]
        avg_price = close.mean()

        if avg_price <= 0 or pd.isna(atr):
            return None

        return atr / avg_price
