"""
Strategie : Initial Balance Extension

Edge structurel :
Les 30 premieres minutes (Initial Balance, 9:30-10:00 ET) refletent le
positionnement institutionnel overnight. Quand le prix casse l'IB avec
conviction (volume), il tend a continuer dans cette direction car les
institutionnels defendent leurs positions. L'IB extension (1.5x la taille
de l'IB) est un target bien documente dans la litterature market profile.

Regles :
- IB = high/low de 9:30-10:00 ET
- LONG : close > IB_high + volume > 1.5x moyenne 20 barres + ADX > 20
- SHORT : close < IB_low  + volume > 1.5x moyenne 20 barres + ADX > 20
- Stop : milieu de l'IB range
- Target : 1.5x IB extension
- Filtres : IB range entre 0.3% et 3% du prix, volume premiere heure
  >= 50% de la moyenne daily, ATR > 1.5%
- Frequence : 1-3 trades/jour max, un seul signal par ticker par jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
import config


class InitialBalanceExtensionStrategy(BaseStrategy):
    name = "Initial Balance Extension"

    def __init__(
        self,
        ib_extension: float = 1.5,
        vol_multiplier: float = 1.5,
        adx_threshold: float = 20.0,
        min_ib_pct: float = 0.003,
        max_ib_pct: float = 0.03,
        min_atr_pct: float = 0.015,
        min_vol_first_hour_ratio: float = 0.5,
        max_trades_per_day: int = 3,
    ):
        self.ib_extension = ib_extension
        self.vol_multiplier = vol_multiplier
        self.adx_threshold = adx_threshold
        self.min_ib_pct = min_ib_pct
        self.max_ib_pct = max_ib_pct
        self.min_atr_pct = min_atr_pct
        self.min_vol_first_hour_ratio = min_vol_first_hour_ratio
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            if len(df) < 40:
                continue

            # ── Filtre ATR : on veut des tickers volatils (ATR > 1.5%) ──
            atr_pct = self._compute_atr_pct(df)
            if atr_pct is None or atr_pct < self.min_atr_pct:
                continue

            # ── Calculer l'Initial Balance (9:30-10:00 ET) ──
            ib_bars = df.between_time("09:30", "09:59")
            if len(ib_bars) < 5:
                continue

            ib_high = ib_bars["high"].max()
            ib_low = ib_bars["low"].min()
            ib_range = ib_high - ib_low

            if ib_range <= 0:
                continue

            # Prix de reference pour les filtres en pourcentage
            mid_price = (ib_high + ib_low) / 2

            # ── Filtre : IB range entre 0.3% et 3% du prix ──
            ib_pct = ib_range / mid_price
            if ib_pct < self.min_ib_pct or ib_pct > self.max_ib_pct:
                continue

            # ── Filtre : volume premiere heure >= 50% de la moyenne daily ──
            first_hour_vol = ib_bars["volume"].sum()
            daily_avg_vol = df["volume"].sum()  # volume total du jour
            # On compare le volume de la 1ere demi-heure a 50% du total
            # (en intraday, la 1ere heure fait typiquement ~30-40% du volume)
            if daily_avg_vol > 0 and first_hour_vol < daily_avg_vol * self.min_vol_first_hour_ratio * 0.5:
                continue

            # ── Calculer ADX pour le filtre directionnel ──
            # Utiliser les barres dispo jusqu'a la fin de l'IB pour evaluer la tendance
            # On a besoin d'au moins 14+1 barres pour l'ADX
            df_copy = df.copy()
            adx_series = adx(df_copy, period=14)

            # ── Scanner les barres apres l'IB (10:00-14:00 ET) ──
            post_ib = df.between_time("10:00", "14:00")
            signal_found = False

            for ts, bar in post_ib.iterrows():
                if signal_found:
                    break

                # Limiter le nombre de signaux par jour
                if len(signals) >= self.max_trades_per_day:
                    break

                # ── ADX au moment de l'evaluation (shift 1 pour eviter lookahead) ──
                # On utilise l'ADX de la barre precedente
                adx_idx = adx_series.index.get_indexer([ts], method="pad")
                if adx_idx[0] < 1:
                    continue
                # Valeur ADX de la barre precedente (anti-lookahead)
                current_adx = adx_series.iloc[adx_idx[0] - 1]
                if pd.isna(current_adx) or current_adx < self.adx_threshold:
                    continue

                # ── Volume ratio (barre courante vs moyenne 20 barres precedentes) ──
                bars_before = df.loc[:ts]
                if len(bars_before) < 21:
                    continue
                avg_vol_20 = bars_before["volume"].iloc[-21:-1].mean()
                if avg_vol_20 <= 0:
                    continue
                current_vol_ratio = bar["volume"] / avg_vol_20

                if current_vol_ratio < self.vol_multiplier:
                    continue

                # ── LONG : close > IB_high ──
                if bar["close"] > ib_high:
                    stop_loss = ib_high - 0.5 * ib_range  # milieu de l'IB
                    take_profit = bar["close"] + self.ib_extension * ib_range

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "ib_high": round(ib_high, 4),
                            "ib_low": round(ib_low, 4),
                            "ib_range_pct": round(ib_pct * 100, 2),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(current_vol_ratio, 1),
                        },
                    ))
                    signal_found = True

                # ── SHORT : close < IB_low ──
                elif bar["close"] < ib_low:
                    stop_loss = ib_low + 0.5 * ib_range  # milieu de l'IB
                    take_profit = bar["close"] - self.ib_extension * ib_range

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=bar["close"],
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "ib_high": round(ib_high, 4),
                            "ib_low": round(ib_low, 4),
                            "ib_range_pct": round(ib_pct * 100, 2),
                            "adx": round(current_adx, 1),
                            "vol_ratio": round(current_vol_ratio, 1),
                        },
                    ))
                    signal_found = True

        return signals

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """
        Calcule l'ATR en pourcentage du prix moyen sur les barres disponibles.
        Retourne None si pas assez de donnees.
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
