"""
Détecteur de régime de marché — meta-model qui conditionne le routing des stratégies.

Régimes détectés :
  TRENDING_UP    : tendance haussière forte (ADX > 25, prix > EMA)
  TRENDING_DOWN  : tendance baissière forte (ADX > 25, prix < EMA)
  RANGING        : marché latéral (ADX < 20) → mean reversion favorable
  VOLATILE       : forte volatilité sans direction (ATR élevé, ADX moyen)
  UNKNOWN        : données insuffisantes

Routing recommandé par régime :
  TRENDING_*  → momentum, breakout (ORB)
  RANGING     → mean reversion (RSI filtré, VWAP)
  VOLATILE    → réduire la taille, augmenter les stops
  UNKNOWN     → pas de trade

Ce meta-model est le vrai edge d'un système multi-stratégies :
  ne pas utiliser une stratégie mean-reversion en tendance forte,
  ni une stratégie momentum dans un range.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from core.features.store import FeatureStore

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    TRENDING_UP   = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING       = "RANGING"
    VOLATILE      = "VOLATILE"
    UNKNOWN       = "UNKNOWN"


# Stratégies compatibles par régime — clé du meta-routing
REGIME_STRATEGY_MAP: dict[MarketRegime, list[str]] = {
    MarketRegime.TRENDING_UP:   ["orb_", "breakout_", "momentum_"],
    MarketRegime.TRENDING_DOWN: ["orb_", "breakout_", "momentum_"],
    MarketRegime.RANGING:       ["rsi_", "vwap_", "rsi_filtered_"],
    MarketRegime.VOLATILE:      [],      # Aucune stratégie en vol extrême
    MarketRegime.UNKNOWN:       [],
}


@dataclass
class RegimeSnapshot:
    """Snapshot du régime à un instant T."""
    timestamp: pd.Timestamp
    regime: MarketRegime
    adx: float
    atr_pct: float          # ATR / price — volatilité normalisée
    trend_direction: int    # +1 haussier, -1 baissier, 0 neutre
    confidence: float       # 0.0 → 1.0
    allowed_strategies: list[str]

    def allows(self, strategy_id: str) -> bool:
        """Vérifie si une stratégie est autorisée dans ce régime."""
        return any(strategy_id.startswith(pfx) for pfx in self.allowed_strategies)


class RegimeDetector:
    """
    Détecte le régime de marché courant sur une série OHLCV.

    Usage :
        detector = RegimeDetector()
        regime = detector.detect(ohlcv_df)  # Régime sur la dernière bougie
        history = detector.detect_all(ohlcv_df)  # Régime sur toutes les bougies
    """

    def __init__(self,
                 adx_period: int = 14,
                 adx_trending: float = 25.0,
                 adx_ranging: float = 20.0,
                 ema_period: int = 50,
                 vol_lookback: int = 20,
                 vol_high_threshold: float = 1.5):
        self.adx_period = adx_period
        self.adx_trending = adx_trending
        self.adx_ranging = adx_ranging
        self.ema_period = ema_period
        self.vol_lookback = vol_lookback
        self.vol_high_threshold = vol_high_threshold
        self._fs = FeatureStore()

    def detect(self, df: pd.DataFrame) -> RegimeSnapshot:
        """Détecte le régime sur la DERNIÈRE bougie disponible."""
        history = self.detect_all(df)
        return history.iloc[-1]

    def detect_all(self, df: pd.DataFrame) -> pd.Series:
        """
        Calcule le régime pour chaque bougie.
        Retourne une pd.Series de RegimeSnapshot indexée comme df.
        """
        # Calcul des indicateurs (sans shift — on veut les valeurs courantes)
        fs = FeatureStore()

        adx_data = fs._adx(df, self.adx_period)
        adx    = adx_data[f"adx_{self.adx_period}"]
        di_plus  = adx_data[f"di_plus_{self.adx_period}"]
        di_minus = adx_data[f"di_minus_{self.adx_period}"]
        ema    = df["close"].ewm(span=self.ema_period, adjust=False).mean()
        atr    = fs._atr(df, self.adx_period)
        atr_pct = atr / df["close"]
        vol_mean = atr_pct.rolling(self.vol_lookback).mean()
        vol_std  = atr_pct.rolling(self.vol_lookback).std()

        regimes = []
        for i in range(len(df)):
            ts = df.index[i]
            adx_val = adx.iloc[i] if not pd.isna(adx.iloc[i]) else 0.0
            di_p    = di_plus.iloc[i]  if not pd.isna(di_plus.iloc[i])  else 0.0
            di_m    = di_minus.iloc[i] if not pd.isna(di_minus.iloc[i]) else 0.0
            close   = df["close"].iloc[i]
            ema_val = ema.iloc[i]
            atr_pct_val = atr_pct.iloc[i] if not pd.isna(atr_pct.iloc[i]) else 0.0
            vm = vol_mean.iloc[i] if not pd.isna(vol_mean.iloc[i]) else atr_pct_val
            vs = vol_std.iloc[i]  if not pd.isna(vol_std.iloc[i])  else 0.0

            if pd.isna(adx_val) or adx_val == 0:
                regime = MarketRegime.UNKNOWN
                confidence = 0.0
                direction = 0
            elif atr_pct_val > vm + self.vol_high_threshold * vs:
                regime = MarketRegime.VOLATILE
                confidence = min((atr_pct_val - vm) / (vs + 1e-9) / 3, 1.0)
                direction = 0
            elif adx_val >= self.adx_trending:
                direction = 1 if di_p > di_m else -1
                regime = MarketRegime.TRENDING_UP if direction == 1 else MarketRegime.TRENDING_DOWN
                confidence = min((adx_val - self.adx_trending) / 20.0, 1.0)
            elif adx_val <= self.adx_ranging:
                regime = MarketRegime.RANGING
                confidence = min((self.adx_ranging - adx_val) / self.adx_ranging, 1.0)
                direction = 0
            else:
                # Zone grise entre ranging et trending
                regime = MarketRegime.RANGING if close > ema_val else MarketRegime.RANGING
                confidence = 0.3
                direction = 0

            allowed = REGIME_STRATEGY_MAP.get(regime, [])
            regimes.append(RegimeSnapshot(
                timestamp=ts,
                regime=regime,
                adx=round(adx_val, 2),
                atr_pct=round(atr_pct_val * 100, 4),
                trend_direction=direction,
                confidence=round(confidence, 3),
                allowed_strategies=allowed,
            ))

        return pd.Series(regimes, index=df.index)

    def get_regime_stats(self, df: pd.DataFrame) -> dict:
        """
        Statistiques sur la distribution des régimes sur toute la période.
        Utile pour choisir les bonnes stratégies à backtester.
        """
        history = self.detect_all(df)
        counts = {}
        for snap in history:
            r = snap.regime.value
            counts[r] = counts.get(r, 0) + 1

        total = len(history)
        stats = {r: {"count": c, "pct": round(c / total * 100, 1)} for r, c in counts.items()}

        # Régime dominant
        dominant = max(counts, key=counts.get)
        stats["dominant"] = dominant
        stats["total_bars"] = total

        logger.info(f"Régimes : {stats}")
        return stats
