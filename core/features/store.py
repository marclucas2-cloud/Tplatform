"""
Feature Store — calcul centralisé et mis en cache de tous les indicateurs techniques.

RÈGLE : toutes les stratégies passent par le FeatureStore.
Avantages :
  - Indicateurs calculés une seule fois même si plusieurs stratégies en ont besoin
  - Garantie no-lookahead : shift(1) appliqué ici, jamais dans les stratégies
  - Reproductibilité : fingerprint de cache = hash(data_fingerprint + features)

Indicateurs disponibles :
  rsi_{period}          RSI Wilder
  ema_{period}          Exponential Moving Average
  sma_{period}          Simple Moving Average
  atr_{period}          Average True Range
  adx_{period}          Average Directional Index (+ DI+, DI-)
  vwap                  Volume Weighted Average Price (intraday, reset à chaque jour)
  bb_upper_{p}_{std}    Bollinger Band haute
  bb_lower_{p}_{std}    Bollinger Band basse
  bb_width_{p}_{std}    Bollinger Band width (volatilité normalisée)
  vol_regime            Régime de volatilité : 0=low, 1=normal, 2=high
  or_high               Opening Range High (première bougie du jour)
  or_low                Opening Range Low (première bougie du jour)
  or_established        Booléen : l'opening range est établi
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FeatureStore:
    """
    Calcule et met en cache les indicateurs techniques.

    Usage :
        fs = FeatureStore()
        df = fs.compute(ohlcv_df, ["rsi_14", "adx_14", "vwap", "atr_14"])
        # df contient les colonnes originales + les indicateurs demandés
    """

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}

    def compute(self, df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
        """
        Calcule les features demandées et les ajoute au DataFrame.
        Utilise le cache si les mêmes features ont déjà été calculées sur ce DataFrame.

        IMPORTANT : toutes les features retournées sont décalées de 1 bougie (.shift(1))
        pour garantir le no-lookahead. Le signal sur bougie[t] n'utilise que close[t-1].
        """
        cache_key = self._cache_key(df, features)
        if cache_key in self._cache:
            logger.debug(f"FeatureStore cache hit : {features}")
            return self._cache[cache_key]

        result = df.copy()
        for feature in features:
            cols = self._compute_feature(df, feature)
            for col_name, series in cols.items():
                # Shift(1) : signal sur bougie[t] utilise indicateur de bougie[t-1]
                result[col_name] = series.shift(1)

        self._cache[cache_key] = result
        logger.debug(f"FeatureStore calculé : {features} ({len(df)} bougies)")
        return result

    # ─── Dispatch ────────────────────────────────────────────────────────────

    def _compute_feature(self, df: pd.DataFrame, feature: str) -> dict[str, pd.Series]:
        """Parse le nom de la feature et dispatch vers la bonne fonction."""
        parts = feature.split("_")

        if parts[0] == "rsi":
            period = int(parts[1])
            return {feature: self._rsi(df["close"], period)}

        elif parts[0] == "ema":
            period = int(parts[1])
            return {feature: df["close"].ewm(span=period, adjust=False).mean()}

        elif parts[0] == "sma":
            period = int(parts[1])
            return {feature: df["close"].rolling(period).mean()}

        elif parts[0] == "atr":
            period = int(parts[1])
            return {feature: self._atr(df, period)}

        elif parts[0] == "adx":
            period = int(parts[1])
            return self._adx(df, period)  # retourne adx_{p}, di_plus_{p}, di_minus_{p}

        elif parts[0] == "vwap":
            return {"vwap": self._vwap(df)}

        elif parts[0] == "bb":
            # bb_upper_20_2, bb_lower_20_2, bb_width_20_2
            period = int(parts[2])
            n_std = float(parts[3])
            return self._bollinger(df, period, n_std)

        elif parts[0] == "vol" and parts[1] == "regime":
            return {"vol_regime": self._vol_regime(df)}

        elif parts[0] == "or":
            return self._opening_range(df)

        else:
            logger.warning(f"Feature inconnue : {feature}")
            return {}

    # ─── Indicateurs ─────────────────────────────────────────────────────────

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        """RSI Wilder (EMA lissée)."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        """Average True Range."""
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _adx(df: pd.DataFrame, period: int) -> dict[str, pd.Series]:
        """
        Average Directional Index (ADX) + DI+ / DI-.
        ADX < 20 : marché ranging → favorable aux stratégies mean-reversion
        ADX > 25 : marché trending → favorable aux stratégies momentum
        """
        high = df["high"]
        low  = df["low"]
        prev_high = high.shift(1)
        prev_low  = low.shift(1)
        prev_close = df["close"].shift(1)

        # True Range
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Directional Movement
        dm_plus  = (high - prev_high).clip(lower=0)
        dm_minus = (prev_low - low).clip(lower=0)
        # Supprimer les cas où les deux DM sont positifs
        dm_plus  = dm_plus.where(dm_plus > dm_minus, 0)
        dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

        # Smooth avec EWM (méthode Wilder)
        atr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
        di_plus  = 100 * dm_plus.ewm(alpha=1/period, adjust=False).mean()  / atr_smooth.replace(0, np.nan)
        di_minus = 100 * dm_minus.ewm(alpha=1/period, adjust=False).mean() / atr_smooth.replace(0, np.nan)

        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()

        return {
            f"adx_{period}":      adx,
            f"di_plus_{period}":  di_plus,
            f"di_minus_{period}": di_minus,
        }

    @staticmethod
    def _vwap(df: pd.DataFrame) -> pd.Series:
        """
        VWAP intraday — reset chaque jour.
        VWAP = cumsum(typical_price * volume) / cumsum(volume)
        typical_price = (high + low + close) / 3
        """
        typical = (df["high"] + df["low"] + df["close"]) / 3
        pv = typical * df["volume"]

        # Grouper par jour pour reset quotidien
        dates = df.index.date
        cumsum_pv  = pv.groupby(dates).cumsum()
        cumsum_vol = df["volume"].groupby(dates).cumsum()

        vwap = cumsum_pv / cumsum_vol.replace(0, np.nan)
        return vwap

    @staticmethod
    def _bollinger(df: pd.DataFrame, period: int, n_std: float) -> dict[str, pd.Series]:
        """Bollinger Bands + width normalisée."""
        sma = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        upper = sma + n_std * std
        lower = sma - n_std * std
        width = (upper - lower) / sma.replace(0, np.nan)
        key = f"{period}_{int(n_std)}"
        return {
            f"bb_upper_{key}": upper,
            f"bb_lower_{key}": lower,
            f"bb_width_{key}": width,
            f"bb_mid_{key}":   sma,
        }

    @staticmethod
    def _vol_regime(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
        """
        Régime de volatilité basé sur ATR relatif.
        0 = faible volatilité, 1 = normale, 2 = haute
        """
        atr = (df["high"] - df["low"])
        atr_pct = atr / df["close"]
        atr_mean = atr_pct.rolling(lookback).mean()
        atr_std  = atr_pct.rolling(lookback).std()

        regime = pd.Series(1, index=df.index)  # Normal par défaut
        regime[atr_pct < atr_mean - atr_std] = 0  # Low vol
        regime[atr_pct > atr_mean + atr_std] = 2  # High vol
        return regime

    @staticmethod
    def _opening_range(df: pd.DataFrame, or_minutes: int = 30) -> dict[str, pd.Series]:
        """
        Opening Range : high/low des N premières minutes de chaque session.
        Utilisé par la stratégie ORB (Opening Range Breakout).

        Pour timeframe 1H : or_minutes=30 → première demi-heure (approximée sur 1H)
        Pour timeframe 5M : or_minutes=30 → 6 premières bougies
        """
        # Détecter le timeframe depuis le delta médian entre barres consécutives
        if len(df) >= 2:
            delta_sec = (df.index[1] - df.index[0]).total_seconds()
            bar_minutes = max(1, delta_sec / 60)
        else:
            bar_minutes = 60
        n_or_bars = max(1, round(or_minutes / bar_minutes))

        # Grouper par jour
        dates = df.index.date
        or_high_series = pd.Series(np.nan, index=df.index)
        or_low_series  = pd.Series(np.nan, index=df.index)
        established    = pd.Series(False, index=df.index)

        for date, group in df.groupby(dates):
            if len(group) < 2:
                continue
            or_bars = group.iloc[:n_or_bars]
            or_high = or_bars["high"].max()
            or_low  = or_bars["low"].min()
            or_end  = or_bars.index[-1]

            # Appliquer à toutes les bougies du jour après l'OR
            for idx in group.index:
                if idx >= or_end:
                    or_high_series[idx] = or_high
                    or_low_series[idx]  = or_low
                    established[idx]    = True

        return {
            "or_high":        or_high_series,
            "or_low":         or_low_series,
            "or_established": established.astype(float),
        }

    @staticmethod
    def _cache_key(df: pd.DataFrame, features: list[str]) -> str:
        """Clé de cache = hash(shape + index bornes + features)."""
        raw = f"{len(df)}|{df.index[0]}|{df.index[-1]}|{sorted(features)}"
        return hashlib.md5(raw.encode()).hexdigest()
