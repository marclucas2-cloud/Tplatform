"""
Regime Detector HMM — detecteur de regime avance (3 etats : bull, neutral, bear).

Utilise VIX level + SPY vs SMA200 + breadth proxy pour detecter les changements
de regime en < 2 jours vs le detecteur ADX-based existant (core/regime/detector.py).

Architecture :
  - Pas de hmmlearn : modele a seuils multi-signaux avec score composite
  - 3 signaux independants votes pour un regime
  - Transition smoothing : evite les faux signaux (regime doit persister 2 jours)
  - Confiance = proportion de signaux concordants

Regimes :
  BULL    : VIX < 20, SPY > SMA200, breadth positive
  NEUTRAL : conditions mixtes, aucun consensus fort
  BEAR    : VIX > 25, SPY < SMA200, breadth negative

Differences avec RegimeDetector existant :
  - 3 etats (bull/neutral/bear) vs 5 etats (trending_up/down/ranging/volatile/unknown)
  - Focus macro (VIX, SMA200) vs micro (ADX, ATR intraday)
  - Objectif : allocation-level decisions, pas routing de strategies intraday
"""

import logging
from enum import Enum
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


class MacroRegime(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"


class RegimeDetectorHMM:
    """Detecteur de regime avance (3 etats : bull, neutral, bear).

    Utilise VIX level + SPY vs SMA200 + breadth proxy.
    Objectif : detecter les changements de regime en < 2 jours vs l'actuel.

    Usage :
        detector = RegimeDetectorHMM()
        result = detector.detect_regime(spy_data, vix_data)
        print(result)  # {regime, confidence, days_in_regime, transition_probability}
    """

    # --- Thresholds ---
    VIX_BULL_THRESHOLD = 18.0     # VIX < 18 = signal bull
    VIX_BEAR_THRESHOLD = 25.0     # VIX > 25 = signal bear
    SMA_PERIOD = 200              # SMA200 pour SPY trend
    BREADTH_LOOKBACK = 10         # Jours pour le breadth proxy (% closes > SMA50)
    BREADTH_BULL_THRESHOLD = 0.6  # > 60% des jours positifs = bull
    BREADTH_BEAR_THRESHOLD = 0.4  # < 40% = bear
    SMOOTHING_DAYS = 2            # Jours de persistence requis avant transition

    # --- Transition matrix (prior probabilities) ---
    # Probabilite de rester dans le meme regime si aucun signal contraire
    TRANSITION_PRIORS = {
        MacroRegime.BULL: {
            MacroRegime.BULL: 0.85,
            MacroRegime.NEUTRAL: 0.12,
            MacroRegime.BEAR: 0.03,
        },
        MacroRegime.NEUTRAL: {
            MacroRegime.BULL: 0.20,
            MacroRegime.NEUTRAL: 0.60,
            MacroRegime.BEAR: 0.20,
        },
        MacroRegime.BEAR: {
            MacroRegime.BULL: 0.05,
            MacroRegime.NEUTRAL: 0.15,
            MacroRegime.BEAR: 0.80,
        },
    }

    def __init__(
        self,
        vix_bull: float = None,
        vix_bear: float = None,
        sma_period: int = None,
        smoothing_days: int = None,
    ):
        self.vix_bull = vix_bull or self.VIX_BULL_THRESHOLD
        self.vix_bear = vix_bear or self.VIX_BEAR_THRESHOLD
        self.sma_period = sma_period or self.SMA_PERIOD
        self.smoothing_days = smoothing_days or self.SMOOTHING_DAYS

        # Internal state for tracking regime persistence
        self._current_regime = MacroRegime.NEUTRAL
        self._days_in_regime = 0
        self._pending_regime = None
        self._pending_days = 0

    def detect_regime(
        self,
        spy_data: pd.DataFrame,
        vix_data: pd.DataFrame,
    ) -> Dict:
        """Detecte le regime macro courant.

        Args:
            spy_data: DataFrame avec au minimum une colonne 'close', index DatetimeIndex.
                      Minimum 200 rows pour le SMA200.
            vix_data: DataFrame avec une colonne 'close' (VIX level), index DatetimeIndex.

        Returns:
            {
                regime: str ('BULL', 'NEUTRAL', 'BEAR'),
                confidence: float (0.0 - 1.0),
                days_in_regime: int,
                transition_probability: dict {regime: probability},
                signals: {
                    vix_signal: str,
                    trend_signal: str,
                    breadth_signal: str,
                },
                vix_level: float,
                spy_vs_sma200: float (% distance),
                breadth_ratio: float,
            }
        """
        if spy_data is None or len(spy_data) < self.sma_period:
            logger.warning(
                "Pas assez de donnees SPY (%d < %d). Regime = NEUTRAL.",
                len(spy_data) if spy_data is not None else 0,
                self.sma_period,
            )
            return self._build_result(
                MacroRegime.NEUTRAL, 0.0, 0,
                {"vix_signal": "unknown", "trend_signal": "unknown", "breadth_signal": "unknown"},
                0.0, 0.0, 0.5,
            )

        # --- Signal 1 : VIX level ---
        vix_level = self._get_latest_vix(vix_data)
        vix_signal = self._vix_signal(vix_level)

        # --- Signal 2 : SPY vs SMA200 ---
        spy_close = spy_data["close"]
        sma200 = spy_close.rolling(self.sma_period).mean()
        latest_close = float(spy_close.iloc[-1])
        latest_sma = float(sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else latest_close
        spy_vs_sma = (latest_close - latest_sma) / latest_sma if latest_sma > 0 else 0.0
        trend_signal = self._trend_signal(spy_vs_sma)

        # --- Signal 3 : Breadth proxy ---
        breadth_ratio = self._breadth_proxy(spy_data)
        breadth_signal = self._breadth_signal(breadth_ratio)

        # --- Combine signals via majority vote ---
        signals = {
            "vix_signal": vix_signal,
            "trend_signal": trend_signal,
            "breadth_signal": breadth_signal,
        }
        raw_regime, confidence = self._combine_signals(vix_signal, trend_signal, breadth_signal)

        # --- Transition smoothing ---
        final_regime, days = self._apply_smoothing(raw_regime)

        # --- Transition probabilities ---
        transition_prob = dict(self.TRANSITION_PRIORS.get(
            final_regime, self.TRANSITION_PRIORS[MacroRegime.NEUTRAL]
        ))

        return self._build_result(
            final_regime, confidence, days, signals,
            vix_level, spy_vs_sma, breadth_ratio, transition_prob,
        )

    def detect_regime_history(
        self,
        spy_data: pd.DataFrame,
        vix_data: pd.DataFrame,
    ) -> List[Dict]:
        """Detecte le regime sur toute la serie historique.

        Utile pour le backtesting de l'allocation regime-conditional.

        Args:
            spy_data: DataFrame SPY avec 'close', index DatetimeIndex.
            vix_data: DataFrame VIX avec 'close', index DatetimeIndex.

        Returns:
            Liste de dicts (un par jour), meme format que detect_regime().
        """
        if spy_data is None or len(spy_data) < self.sma_period:
            return []

        # Reset state
        self._current_regime = MacroRegime.NEUTRAL
        self._days_in_regime = 0
        self._pending_regime = None
        self._pending_days = 0

        spy_close = spy_data["close"]
        sma200 = spy_close.rolling(self.sma_period).mean()
        sma50 = spy_close.rolling(50).mean()

        # Align VIX data to SPY dates
        vix_aligned = self._align_vix(vix_data, spy_data.index)

        results = []
        for i in range(self.sma_period, len(spy_data)):
            # VIX
            vix_level = float(vix_aligned.iloc[i]) if not pd.isna(vix_aligned.iloc[i]) else 20.0
            vix_signal = self._vix_signal(vix_level)

            # Trend
            close_val = float(spy_close.iloc[i])
            sma_val = float(sma200.iloc[i]) if not pd.isna(sma200.iloc[i]) else close_val
            spy_vs_sma = (close_val - sma_val) / sma_val if sma_val > 0 else 0.0
            trend_signal = self._trend_signal(spy_vs_sma)

            # Breadth proxy (rolling window)
            start_idx = max(0, i - self.BREADTH_LOOKBACK)
            window = spy_data.iloc[start_idx:i + 1]
            breadth_ratio = self._breadth_proxy(window)
            breadth_signal = self._breadth_signal(breadth_ratio)

            # Combine
            raw_regime, confidence = self._combine_signals(vix_signal, trend_signal, breadth_signal)
            final_regime, days = self._apply_smoothing(raw_regime)

            signals = {
                "vix_signal": vix_signal,
                "trend_signal": trend_signal,
                "breadth_signal": breadth_signal,
            }
            transition_prob = dict(self.TRANSITION_PRIORS.get(
                final_regime, self.TRANSITION_PRIORS[MacroRegime.NEUTRAL]
            ))
            result = self._build_result(
                final_regime, confidence, days, signals,
                vix_level, spy_vs_sma, breadth_ratio, transition_prob,
            )
            result["date"] = spy_data.index[i]
            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def _get_latest_vix(self, vix_data: pd.DataFrame) -> float:
        """Extrait le dernier niveau du VIX."""
        if vix_data is None or len(vix_data) == 0:
            return 20.0  # Default neutral
        return float(vix_data["close"].iloc[-1])

    def _vix_signal(self, vix_level: float) -> str:
        """Signal VIX : bull/neutral/bear."""
        if vix_level < self.vix_bull:
            return "bull"
        elif vix_level > self.vix_bear:
            return "bear"
        return "neutral"

    def _trend_signal(self, spy_vs_sma: float) -> str:
        """Signal trend : bull si SPY > SMA200 + 2%, bear si < -2%."""
        if spy_vs_sma > 0.02:
            return "bull"
        elif spy_vs_sma < -0.02:
            return "bear"
        return "neutral"

    def _breadth_proxy(self, spy_data: pd.DataFrame) -> float:
        """Breadth proxy = % des jours ou le close > close precedent sur le lookback.

        En l'absence de donnees breadth reelles (advance/decline), on utilise
        la proportion de jours haussiers comme approximation.
        """
        if len(spy_data) < 2:
            return 0.5
        closes = spy_data["close"]
        daily_returns = closes.pct_change().dropna()
        if len(daily_returns) == 0:
            return 0.5
        lookback = min(self.BREADTH_LOOKBACK, len(daily_returns))
        recent = daily_returns.iloc[-lookback:]
        return float((recent > 0).sum() / len(recent))

    def _breadth_signal(self, breadth_ratio: float) -> str:
        """Signal breadth : bull/neutral/bear."""
        if breadth_ratio > self.BREADTH_BULL_THRESHOLD:
            return "bull"
        elif breadth_ratio < self.BREADTH_BEAR_THRESHOLD:
            return "bear"
        return "neutral"

    # ------------------------------------------------------------------
    # Combination & smoothing
    # ------------------------------------------------------------------

    def _combine_signals(
        self, vix_signal: str, trend_signal: str, breadth_signal: str,
    ) -> tuple:
        """Combine les 3 signaux via majority vote.

        Returns:
            (regime: MacroRegime, confidence: float 0-1)
        """
        votes = {"bull": 0, "neutral": 0, "bear": 0}
        for signal in [vix_signal, trend_signal, breadth_signal]:
            if signal in votes:
                votes[signal] += 1

        # Regime = majority vote
        max_votes = max(votes.values())
        if votes["bull"] == max_votes:
            regime = MacroRegime.BULL
        elif votes["bear"] == max_votes:
            regime = MacroRegime.BEAR
        else:
            regime = MacroRegime.NEUTRAL

        # Confidence = proportion de signaux concordants
        confidence = max_votes / 3.0

        return regime, round(confidence, 3)

    def _apply_smoothing(self, raw_regime: MacroRegime) -> tuple:
        """Transition smoothing : evite les faux signaux.

        Un regime doit etre detecte pendant `smoothing_days` consecutifs
        avant de devenir effectif.

        Returns:
            (effective_regime: MacroRegime, days_in_regime: int)
        """
        if raw_regime == self._current_regime:
            # Meme regime : incrementer le compteur, reset pending
            self._days_in_regime += 1
            self._pending_regime = None
            self._pending_days = 0
        elif raw_regime == self._pending_regime:
            # Continuation du nouveau regime en attente
            self._pending_days += 1
            if self._pending_days >= self.smoothing_days:
                # Transition confirmee
                self._current_regime = raw_regime
                self._days_in_regime = self._pending_days
                self._pending_regime = None
                self._pending_days = 0
        else:
            # Nouveau signal different du pending
            self._pending_regime = raw_regime
            self._pending_days = 1

        return self._current_regime, self._days_in_regime

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _align_vix(self, vix_data: pd.DataFrame, target_index: pd.DatetimeIndex) -> pd.Series:
        """Aligne les donnees VIX sur l'index SPY (forward fill)."""
        if vix_data is None or len(vix_data) == 0:
            return pd.Series(20.0, index=target_index)
        vix_series = vix_data["close"].reindex(target_index, method="ffill")
        return vix_series.fillna(20.0)

    @staticmethod
    def _build_result(
        regime: MacroRegime,
        confidence: float,
        days_in_regime: int,
        signals: dict,
        vix_level: float,
        spy_vs_sma200: float,
        breadth_ratio: float,
        transition_probability: dict = None,
    ) -> Dict:
        """Construit le dict de resultat standardise."""
        if transition_probability is None:
            transition_probability = {
                "BULL": 0.33,
                "NEUTRAL": 0.34,
                "BEAR": 0.33,
            }
        return {
            "regime": regime.value,
            "confidence": confidence,
            "days_in_regime": days_in_regime,
            "transition_probability": {
                k.value if isinstance(k, MacroRegime) else k: v
                for k, v in transition_probability.items()
            },
            "signals": signals,
            "vix_level": round(vix_level, 2),
            "spy_vs_sma200": round(spy_vs_sma200, 4),
            "breadth_ratio": round(breadth_ratio, 3),
        }

    def get_allocation_regime(self, regime_result: Dict) -> str:
        """Mappe le regime detecte vers les regimes d'allocation de l'allocator.

        Returns:
            Un de 'BULL_NORMAL', 'BULL_HIGH_VOL', 'BEAR_NORMAL', 'BEAR_HIGH_VOL'
        """
        regime = regime_result["regime"]
        vix = regime_result["vix_level"]
        high_vol = vix > 22.0

        if regime == "BULL":
            return "BULL_HIGH_VOL" if high_vol else "BULL_NORMAL"
        elif regime == "BEAR":
            return "BEAR_HIGH_VOL" if high_vol else "BEAR_NORMAL"
        else:
            # NEUTRAL -> lean toward normal bull if VIX low, bear_normal if high
            if vix > 22.0:
                return "BEAR_NORMAL"
            return "BULL_NORMAL"
