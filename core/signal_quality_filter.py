"""
Filtre de qualite des signaux — reduit la frequence des strategies haute rotation.

Les strategies VWAP Micro, ORB V2 et Triple EMA ont ete rejetees en walk-forward
(OOS Sharpe negatif / plat) a cause de sur-trading et overfitting. Plutot que
de les supprimer, ce filtre conserve uniquement les signaux haute conviction
pour reduire les trades de 40-50% et ameliorer le Sharpe net apres couts.

Usage :
    from core.signal_quality_filter import SignalQualityFilter

    sqf = SignalQualityFilter()
    ok, reason = sqf.should_trade(signal, market_context)
    if not ok:
        logger.info(f"Signal filtre: {reason}")
        return

Filtres appliques (tous doivent passer) :
    1. Volume relatif > mediane 20j
    2. ATR relatif > mediane 20j
    3. Pas de conflit inter-strategie sur le meme ticker
    4. Pas dans les 30 premieres/dernieres min du marche
    5. Reduction taille si VIX > 30
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Optional

import zoneinfo

logger = logging.getLogger(__name__)

ET = zoneinfo.ZoneInfo("America/New_York")

# Horaires d'exclusion (ouverture/fermeture bruyantes)
_MARKET_OPEN = dtime(9, 30)
_NOISY_OPEN_END = dtime(10, 0)       # 30 premieres min
_NOISY_CLOSE_START = dtime(15, 30)    # 30 dernieres min
_MARKET_CLOSE = dtime(16, 0)

# Seuil VIX pour reduction de taille
_VIX_HIGH_THRESHOLD = 30.0
_VIX_SIZE_REDUCTION = 0.50  # reduire la taille de 50%


class SignalQualityFilter:
    """Filtre les trades a faible conviction pour les strategies haute frequence.

    Objectif : reduire les trades de 40-50% en ne gardant que les signaux
    haute conviction, pour ameliorer le Sharpe net apres couts.
    """

    def __init__(
        self,
        volume_lookback: int = 20,
        atr_lookback: int = 20,
        vix_threshold: float = _VIX_HIGH_THRESHOLD,
        vix_size_reduction: float = _VIX_SIZE_REDUCTION,
    ):
        """
        Args:
            volume_lookback: nb de jours pour la mediane de volume (default 20)
            atr_lookback: nb de jours pour la mediane d'ATR (default 20)
            vix_threshold: seuil VIX au-dela duquel reduire la taille (default 30)
            vix_size_reduction: facteur de reduction si VIX > seuil (default 0.50)
        """
        self.volume_lookback = volume_lookback
        self.atr_lookback = atr_lookback
        self.vix_threshold = vix_threshold
        self.vix_size_reduction = vix_size_reduction

        # Suivi des positions actives par strategie pour detecter les conflits
        self._active_tickers: dict[str, str] = {}  # ticker -> strategy_name

    def register_active_position(self, ticker: str, strategy: str) -> None:
        """Enregistre une position active pour la detection de conflit."""
        self._active_tickers[ticker] = strategy

    def unregister_position(self, ticker: str) -> None:
        """Retire une position fermee du registre."""
        self._active_tickers.pop(ticker, None)

    def should_trade(
        self, signal: dict, market_context: dict
    ) -> tuple[bool, str]:
        """Decide si un signal merite d'etre execute.

        Filtres universels (appliques a toute strategie haute freq) :
        1. Volume relatif > mediane 20j (pas de trade en basse liquidite)
        2. ATR > mediane 20j (assez de mouvement pour couvrir les couts)
        3. Pas de conflit avec une autre strategie sur le meme ticker
        4. Pas dans les 30 premieres min ou 30 dernieres min (bruit)
        5. VIX level : si VIX > 30, reduire la taille de 50%

        Args:
            signal: {
                "ticker": str,
                "strategy": str,
                "direction": "LONG" | "SHORT",
                "current_volume": float,     # volume courant de la barre
                "volume_median_20d": float,  # mediane volume 20j
                "current_atr": float,        # ATR courant
                "atr_median_20d": float,     # mediane ATR 20j
                "size": float,               # taille proposee en $
            }
            market_context: {
                "timestamp": datetime,     # heure courante (TZ-aware)
                "vix": float,              # niveau VIX courant
                "active_positions": dict,  # {ticker: strategy} deja actives
            }

        Returns:
            (should_trade: bool, reason: str)
            Si should_trade est True, reason contient des info sur d'eventuels
            ajustements (ex: "OK — taille reduite 50% (VIX > 30)").
        """
        ticker = signal.get("ticker", "")
        strategy = signal.get("strategy", "")

        # -----------------------------------------------------------
        # FILTRE 1 : Volume relatif
        # -----------------------------------------------------------
        current_volume = signal.get("current_volume", 0)
        volume_median = signal.get("volume_median_20d", 0)

        if volume_median > 0 and current_volume < volume_median:
            return False, (
                f"FILTRE VOLUME: {ticker} volume={current_volume:,.0f} "
                f"< mediane 20j={volume_median:,.0f} — liquidite insuffisante"
            )

        # -----------------------------------------------------------
        # FILTRE 2 : ATR relatif (assez de mouvement pour les couts)
        # -----------------------------------------------------------
        current_atr = signal.get("current_atr", 0)
        atr_median = signal.get("atr_median_20d", 0)

        if atr_median > 0 and current_atr < atr_median:
            return False, (
                f"FILTRE ATR: {ticker} ATR={current_atr:.4f} "
                f"< mediane 20j={atr_median:.4f} — mouvement insuffisant"
            )

        # -----------------------------------------------------------
        # FILTRE 3 : Conflit inter-strategie
        # -----------------------------------------------------------
        active_positions = market_context.get("active_positions", {})
        # Merge avec le registre interne
        all_active = {**self._active_tickers, **active_positions}

        if ticker in all_active:
            conflicting_strategy = all_active[ticker]
            if conflicting_strategy != strategy:
                return False, (
                    f"FILTRE CONFLIT: {ticker} deja en position par "
                    f"'{conflicting_strategy}', signal de '{strategy}' ignore"
                )

        # -----------------------------------------------------------
        # FILTRE 4 : Horaires bruyants (30 premieres/dernieres min)
        # -----------------------------------------------------------
        timestamp = market_context.get("timestamp")
        if timestamp is not None:
            # Convertir en heure ET si timezone-aware
            if hasattr(timestamp, "astimezone"):
                et_time = timestamp.astimezone(ET).time()
            else:
                et_time = timestamp.time()

            if et_time < _NOISY_OPEN_END:
                return False, (
                    f"FILTRE HORAIRE: {et_time.strftime('%H:%M')} ET "
                    f"< 10:00 — 30 premieres min trop bruyantes"
                )
            if et_time >= _NOISY_CLOSE_START:
                return False, (
                    f"FILTRE HORAIRE: {et_time.strftime('%H:%M')} ET "
                    f">= 15:30 — 30 dernieres min trop bruyantes"
                )

        # -----------------------------------------------------------
        # FILTRE 5 : VIX — ajustement de taille (pas de rejet)
        # -----------------------------------------------------------
        vix = market_context.get("vix", 0)
        size_adjustment = 1.0

        if vix > self.vix_threshold:
            size_adjustment = 1.0 - self.vix_size_reduction
            logger.info(
                f"VIX ADJUSTMENT: {ticker} VIX={vix:.1f} > {self.vix_threshold} "
                f"— taille reduite de {self.vix_size_reduction:.0%}"
            )

        # -----------------------------------------------------------
        # RESULTAT
        # -----------------------------------------------------------
        if size_adjustment < 1.0:
            return True, (
                f"OK — taille reduite {self.vix_size_reduction:.0%} "
                f"(VIX={vix:.1f} > {self.vix_threshold})"
            )

        return True, "OK — signal haute conviction"

    def get_adjusted_size(
        self, original_size: float, market_context: dict
    ) -> float:
        """Retourne la taille ajustee en fonction du VIX.

        Args:
            original_size: taille originale en $
            market_context: dict avec au moins "vix"

        Returns:
            Taille ajustee (reduite si VIX > seuil)
        """
        vix = market_context.get("vix", 0)
        if vix > self.vix_threshold:
            return original_size * (1.0 - self.vix_size_reduction)
        return original_size

    def compute_conviction_score(self, signal: dict) -> float:
        """Calcule un score de conviction entre 0 et 1.

        Plus le score est eleve, plus le signal est fiable.
        Combine volume relatif + ATR relatif + heure de la journee.

        Args:
            signal: dict avec les champs volume, ATR, et timestamp

        Returns:
            Score entre 0.0 et 1.0
        """
        score = 0.0
        n_factors = 0

        # Volume relatif
        current_volume = signal.get("current_volume", 0)
        volume_median = signal.get("volume_median_20d", 0)
        if volume_median > 0:
            vol_ratio = min(current_volume / volume_median, 3.0) / 3.0
            score += vol_ratio
            n_factors += 1

        # ATR relatif
        current_atr = signal.get("current_atr", 0)
        atr_median = signal.get("atr_median_20d", 0)
        if atr_median > 0:
            atr_ratio = min(current_atr / atr_median, 3.0) / 3.0
            score += atr_ratio
            n_factors += 1

        if n_factors == 0:
            return 0.5  # Pas de donnees = neutre

        return round(score / n_factors, 4)
