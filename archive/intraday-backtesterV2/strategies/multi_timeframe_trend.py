"""
Strategie 12 : Multi-Timeframe Trend Alignment

Edge structurel :
Quand le trend est aligne sur 3 timeframes (5M, 15M simule, 30M simule),
la probabilite de continuation est significativement plus elevee. On simule
les timeframes superieurs en agregeant les barres 5M.

Regles :
- 5M : EMA(9) vs EMA(21) pour le trend court terme
- 15M (agrege 3x5M) : EMA(9) vs EMA(21) pour trend moyen terme
- 30M (agrege 6x5M) : EMA(9) vs EMA(21) pour trend long terme
- LONG : 3 TF bullish + pullback vers EMA(9) 5M + volume > 1x moyenne
- SHORT : 3 TF bearish + pullback vers EMA(9) 5M + volume > 1x moyenne
- Stop : EMA(21) en 5M
- Target : 2x ATR(14) en 5M depuis l'entree
- Filtres : ADX(14) < 20 sur 15M, EMAs trop proches (< 0.1%), volume < 0.8x
- Timing : 10:00-14:30 ET
- Frequence : 1-5 trades/jour, un seul signal par ticker par jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, volume_ratio
import config


# ── Parametres ──
EMA_FAST = 9
EMA_SLOW = 21
ATR_PERIOD = 14
ATR_TARGET_MULT = 2.0     # Target = 2x ATR
ADX_MIN_15M = 20          # ADX minimum sur le 15M pour confirmer un trend
EMA_MIN_SPREAD_PCT = 0.001  # EMAs doivent etre espacees d'au moins 0.1%
VOL_MIN_RATIO = 1.0       # Volume >= 1x moyenne
VOL_LOW_RATIO = 0.8       # Skip si volume < 0.8x moyenne
MIN_ATR_PCT = 0.008       # ATR daily minimum 0.8%
MIN_VOLUME = 500_000      # Volume minimum 500K


class MultiTimeframeTrendStrategy(BaseStrategy):
    name = "Multi-TF Trend Alignment"

    def __init__(
        self,
        ema_fast: int = EMA_FAST,
        ema_slow: int = EMA_SLOW,
        atr_period: int = ATR_PERIOD,
        atr_target_mult: float = ATR_TARGET_MULT,
        adx_min_15m: float = ADX_MIN_15M,
        ema_min_spread: float = EMA_MIN_SPREAD_PCT,
        vol_min_ratio: float = VOL_MIN_RATIO,
        max_trades_per_day: int = 5,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_target_mult = atr_target_mult
        self.adx_min_15m = adx_min_15m
        self.ema_min_spread = ema_min_spread
        self.vol_min_ratio = vol_min_ratio
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue

            if len(df) < 40:
                continue

            # ── Filtres pre-calcul : volume et ATR ──
            total_volume = df["volume"].sum()
            if total_volume < MIN_VOLUME:
                continue

            atr_pct = self._compute_atr_pct(df)
            if atr_pct is None or atr_pct < MIN_ATR_PCT:
                continue

            df_5m = df.copy()

            # ── Construire les timeframes superieurs par aggregation ──
            df_15m = self._resample(df, "15min")
            df_30m = self._resample(df, "30min")

            if df_15m is None or df_30m is None:
                continue
            if len(df_15m) < self.ema_slow + 2 or len(df_30m) < self.ema_slow + 2:
                continue

            # ── Calculer les EMAs sur chaque timeframe ──
            df_5m["ema_fast"] = df_5m["close"].ewm(span=self.ema_fast, adjust=False).mean()
            df_5m["ema_slow"] = df_5m["close"].ewm(span=self.ema_slow, adjust=False).mean()

            df_15m["ema_fast"] = df_15m["close"].ewm(span=self.ema_fast, adjust=False).mean()
            df_15m["ema_slow"] = df_15m["close"].ewm(span=self.ema_slow, adjust=False).mean()

            df_30m["ema_fast"] = df_30m["close"].ewm(span=self.ema_fast, adjust=False).mean()
            df_30m["ema_slow"] = df_30m["close"].ewm(span=self.ema_slow, adjust=False).mean()

            # ── ADX sur le 15M ──
            adx_15m = adx(df_15m, period=14)

            # ── ATR en 5M pour le target ──
            df_5m["atr"] = self._compute_atr_series(df_5m, self.atr_period)

            # ── Volume moyen glissant (20 barres, 5M) ──
            df_5m["vol_avg_20"] = df_5m["volume"].rolling(20).mean()

            # ── Scanner les barres 5M dans la fenetre 10:00-14:30 ET ──
            tradeable = df_5m.between_time("10:00", "14:30")
            signal_found = False

            for i in range(2, len(tradeable)):
                if signal_found:
                    break
                if len(signals) >= self.max_trades_per_day:
                    break

                ts = tradeable.index[i]
                bar = tradeable.iloc[i]
                prev = tradeable.iloc[i - 1]
                prev2 = tradeable.iloc[i - 2]

                # ── Valeurs 5M (anti-lookahead : on lit prev) ──
                ema_fast_5m = prev["ema_fast"]
                ema_slow_5m = prev["ema_slow"]
                if pd.isna(ema_fast_5m) or pd.isna(ema_slow_5m):
                    continue

                # ── Valeurs 15M (derniere barre completee avant ts) ──
                tf_15m_before = df_15m[df_15m.index < ts]
                if len(tf_15m_before) < 2:
                    continue
                last_15m = tf_15m_before.iloc[-1]
                ema_fast_15m = last_15m["ema_fast"]
                ema_slow_15m = last_15m["ema_slow"]
                if pd.isna(ema_fast_15m) or pd.isna(ema_slow_15m):
                    continue

                # ── Valeurs 30M (derniere barre completee avant ts) ──
                tf_30m_before = df_30m[df_30m.index < ts]
                if len(tf_30m_before) < 2:
                    continue
                last_30m = tf_30m_before.iloc[-1]
                ema_fast_30m = last_30m["ema_fast"]
                ema_slow_30m = last_30m["ema_slow"]
                if pd.isna(ema_fast_30m) or pd.isna(ema_slow_30m):
                    continue

                # ── Determiner le biais de chaque timeframe ──
                bullish_5m = ema_fast_5m > ema_slow_5m
                bullish_15m = ema_fast_15m > ema_slow_15m
                bullish_30m = ema_fast_30m > ema_slow_30m

                bearish_5m = ema_fast_5m < ema_slow_5m
                bearish_15m = ema_fast_15m < ema_slow_15m
                bearish_30m = ema_fast_30m < ema_slow_30m

                # ── Filtre : les 3 TF doivent etre alignes ──
                all_bullish = bullish_5m and bullish_15m and bullish_30m
                all_bearish = bearish_5m and bearish_15m and bearish_30m

                if not all_bullish and not all_bearish:
                    continue

                # ── Filtre ADX 15M : trend assez fort ──
                adx_15m_before = adx_15m[adx_15m.index < ts]
                if adx_15m_before.empty:
                    continue
                current_adx = adx_15m_before.iloc[-1]
                if pd.isna(current_adx) or current_adx < self.adx_min_15m:
                    continue

                # ── Filtre : EMAs pas trop proches (marche range) ──
                spread_5m = abs(ema_fast_5m - ema_slow_5m) / ema_slow_5m
                spread_15m = abs(ema_fast_15m - ema_slow_15m) / ema_slow_15m
                if spread_5m < self.ema_min_spread or spread_15m < self.ema_min_spread:
                    continue

                # ── Filtre volume ──
                vol_avg = prev["vol_avg_20"]
                if pd.isna(vol_avg) or vol_avg <= 0:
                    continue
                vol_ratio_val = bar["volume"] / vol_avg
                if vol_ratio_val < VOL_LOW_RATIO:
                    continue
                if vol_ratio_val < self.vol_min_ratio:
                    continue

                # ── ATR pour le target ──
                atr_val = prev["atr"]
                if pd.isna(atr_val) or atr_val <= 0:
                    continue

                entry_price = bar["close"]

                if all_bullish:
                    # ── Pullback vers EMA(9) 5M puis rebond ──
                    # prev2 ou prev : close a touche/passe sous EMA9
                    # bar : close revient au-dessus de EMA9
                    pullback_touch = (
                        prev["close"] <= ema_fast_5m
                        or prev2["close"] <= prev2.get("ema_fast", ema_fast_5m)
                    )
                    bounce_back = bar["close"] > ema_fast_5m

                    if not (pullback_touch and bounce_back):
                        continue

                    stop_loss = ema_slow_5m  # EMA(21) 5M
                    take_profit = entry_price + self.atr_target_mult * atr_val

                    # Verifier RR
                    risk = entry_price - stop_loss
                    reward = take_profit - entry_price
                    if risk <= 0 or reward <= 0:
                        continue

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "trend": "BULLISH",
                            "adx_15m": round(current_adx, 1),
                            "spread_5m_pct": round(spread_5m * 100, 3),
                            "spread_15m_pct": round(spread_15m * 100, 3),
                            "atr": round(atr_val, 4),
                            "vol_ratio": round(vol_ratio_val, 2),
                        },
                    ))
                    signal_found = True

                elif all_bearish:
                    # ── Pullback vers EMA(9) 5M puis rejet ──
                    # prev2 ou prev : close a touche/passe au-dessus de EMA9
                    # bar : close retombe sous EMA9
                    pullback_touch = (
                        prev["close"] >= ema_fast_5m
                        or prev2["close"] >= prev2.get("ema_fast", ema_fast_5m)
                    )
                    rejection = bar["close"] < ema_fast_5m

                    if not (pullback_touch and rejection):
                        continue

                    stop_loss = ema_slow_5m  # EMA(21) 5M
                    take_profit = entry_price - self.atr_target_mult * atr_val

                    # Verifier RR
                    risk = stop_loss - entry_price
                    reward = entry_price - take_profit
                    if risk <= 0 or reward <= 0:
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "trend": "BEARISH",
                            "adx_15m": round(current_adx, 1),
                            "spread_5m_pct": round(spread_5m * 100, 3),
                            "spread_15m_pct": round(spread_15m * 100, 3),
                            "atr": round(atr_val, 4),
                            "vol_ratio": round(vol_ratio_val, 2),
                        },
                    ))
                    signal_found = True

        return signals

    @staticmethod
    def _resample(df: pd.DataFrame, freq: str) -> pd.DataFrame | None:
        """
        Agrege les barres 5M en timeframe superieur (15M, 30M).
        Retourne None si pas assez de donnees.
        """
        try:
            resampled = df.resample(freq).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna(subset=["open", "close"])

            if len(resampled) < 5:
                return None
            return resampled
        except Exception:
            return None

    @staticmethod
    def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        """
        ATR en pourcentage du prix moyen.
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

    @staticmethod
    def _compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR en serie pour calculer le target dynamiquement."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        return tr.rolling(period).mean()
