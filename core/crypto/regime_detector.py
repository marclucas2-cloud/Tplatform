"""
ROC-C04 — Enhanced crypto regime detector (4 signals).

Replaces the simple 2-signal detector in allocator_crypto.py with a
weighted multi-signal approach:

  1. trend     (0.35) — BTC close vs EMA50/EMA200 (golden/death cross)
  2. momentum  (0.25) — BTC 30-day return
  3. volatility(0.20) — vol_7d / vol_30d ratio + trend direction
  4. breadth   (0.20) — % of altcoins above their EMA50

Final regime = weighted majority vote across all 4 signals.
Each signal votes BULL, BEAR, or CHOP independently.
Confidence = weighted agreement ratio.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

SIGNAL_WEIGHTS = {
    "trend": 0.35,
    "momentum": 0.25,
    "volatility": 0.20,
    "breadth": 0.20,
}

REGIMES = ("BULL", "BEAR", "CHOP")


# ──────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────

@dataclass
class CryptoRegimeResult:
    """Output of the enhanced regime detector."""

    regime: str                      # BULL / BEAR / CHOP
    confidence: float                # 0.0 – 1.0
    votes: dict[str, str]            # signal_name -> regime vote
    scores: dict[str, float]         # signal_name -> raw score
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __str__(self) -> str:
        return (
            f"Regime={self.regime} confidence={self.confidence:.2f} "
            f"votes={self.votes}"
        )


# ──────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────

class CryptoRegimeDetector:
    """Enhanced 4-signal crypto regime detector.

    Usage::

        detector = CryptoRegimeDetector()
        result = detector.detect({
            "btc_close": 67_500.0,
            "btc_ema50": 65_000.0,
            "btc_ema200": 60_000.0,
            "btc_return_30d": 0.12,
            "vol_7d": 0.45,
            "vol_30d": 0.60,
            "btc_trend_direction": "up",
            "altcoin_above_ema50_pct": 0.72,
        })
        print(result.regime, result.confidence)
    """

    def __init__(self, weights: Optional[dict[str, float]] = None):
        """Initialise with optional custom weights.

        Args:
            weights: Dict mapping signal name to weight. Must sum to ~1.0.
                     Defaults to SIGNAL_WEIGHTS.
        """
        self._weights = weights or dict(SIGNAL_WEIGHTS)

        total = sum(self._weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(
                "Regime weights sum to %.3f — normalising", total
            )
            for k in self._weights:
                self._weights[k] /= total

        self._history: list[CryptoRegimeResult] = []

    # ── Public API ────────────────────────────────────────────────────

    def detect(self, market_data: dict) -> CryptoRegimeResult:
        """Detect the current crypto regime.

        Args:
            market_data: Dict with keys:
                - btc_close (float): Current BTC price.
                - btc_ema50 (float): BTC 50-day EMA.
                - btc_ema200 (float): BTC 200-day EMA.
                - btc_return_30d (float): BTC 30-day return as decimal
                  (0.10 = +10%).
                - vol_7d (float): BTC 7-day annualised volatility.
                - vol_30d (float): BTC 30-day annualised volatility.
                - btc_trend_direction (str): "up" or "down" (used by
                  volatility signal).
                - altcoin_above_ema50_pct (float): Fraction 0-1 of altcoins
                  above their 50-day EMA.

        Returns:
            CryptoRegimeResult with regime, confidence, votes, scores.
        """
        votes: dict[str, str] = {}
        scores: dict[str, float] = {}

        # Signal 1: Trend
        trend_vote, trend_score = self._vote_trend(market_data)
        votes["trend"] = trend_vote
        scores["trend"] = trend_score

        # Signal 2: Momentum
        momentum_vote, momentum_score = self._vote_momentum(market_data)
        votes["momentum"] = momentum_vote
        scores["momentum"] = momentum_score

        # Signal 3: Volatility
        vol_vote, vol_score = self._vote_volatility(market_data)
        votes["volatility"] = vol_vote
        scores["volatility"] = vol_score

        # Signal 4: Breadth
        breadth_vote, breadth_score = self._vote_breadth(market_data)
        votes["breadth"] = breadth_vote
        scores["breadth"] = breadth_score

        # Weighted majority vote
        regime, confidence = self._weighted_majority(votes)

        result = CryptoRegimeResult(
            regime=regime,
            confidence=round(confidence, 4),
            votes=votes,
            scores={k: round(v, 4) for k, v in scores.items()},
        )

        self._history.append(result)
        # Keep history bounded
        if len(self._history) > 1000:
            self._history = self._history[-500:]

        logger.info("RegimeDetector: %s", result)

        return result

    def get_history(self, limit: int = 50) -> list[CryptoRegimeResult]:
        """Return recent regime detection history.

        Args:
            limit: Max entries to return.

        Returns:
            List of CryptoRegimeResult, most recent last.
        """
        return self._history[-limit:]

    # ── Signal voters ─────────────────────────────────────────────────

    def _vote_trend(self, data: dict) -> tuple[str, float]:
        """Trend signal: BTC price vs EMA50 and EMA200.

        Golden cross (EMA50 > EMA200 and price > EMA50) = BULL.
        Death cross (EMA50 < EMA200 and price < EMA50) = BEAR.
        Otherwise = CHOP.

        Returns:
            Tuple of (regime_vote, raw_score).
        """
        btc_close = data.get("btc_close", 0)
        ema50 = data.get("btc_ema50", 0)
        ema200 = data.get("btc_ema200", 0)

        if ema200 <= 0 or ema50 <= 0:
            return "CHOP", 0.5

        # How far above/below EMA200 is the price (normalised)
        price_vs_ema200 = (btc_close - ema200) / ema200 if ema200 > 0 else 0
        ema50_vs_ema200 = (ema50 - ema200) / ema200 if ema200 > 0 else 0

        # Golden cross: EMA50 > EMA200 AND price > EMA50
        if ema50 > ema200 and btc_close > ema50:
            # Strength = distance above EMA200, capped at 0.30
            score = min(1.0, 0.5 + price_vs_ema200 / 0.30)
            return "BULL", score

        # Death cross: EMA50 < EMA200 AND price < EMA50
        if ema50 < ema200 and btc_close < ema50:
            score = min(1.0, 0.5 + abs(price_vs_ema200) / 0.30)
            return "BEAR", score

        # Mixed signals — choppy
        # Slight bullish or bearish lean
        if btc_close > ema200:
            return "CHOP", 0.55
        return "CHOP", 0.45

    def _vote_momentum(self, data: dict) -> tuple[str, float]:
        """Momentum signal: BTC 30-day return.

        > +10% = BULL, < -10% = BEAR, else CHOP.

        Returns:
            Tuple of (regime_vote, raw_score).
        """
        ret_30d = data.get("btc_return_30d", 0.0)

        if ret_30d > 0.10:
            # Scale: 10% -> 0.6, 20%+ -> 1.0
            score = min(1.0, 0.6 + (ret_30d - 0.10) / 0.25)
            return "BULL", score

        if ret_30d < -0.10:
            score = min(1.0, 0.6 + (abs(ret_30d) - 0.10) / 0.25)
            return "BEAR", score

        # Between -10% and +10% — choppy
        # Slightly lean towards direction
        score = 0.5 + ret_30d * 2  # +5% -> 0.6, -5% -> 0.4
        score = max(0.2, min(0.8, score))
        return "CHOP", score

    def _vote_volatility(self, data: dict) -> tuple[str, float]:
        """Volatility signal: vol_7d / vol_30d ratio.

        Low ratio (< 0.5) = compression = CHOP.
        High ratio (> 1.5) = expansion: direction determines BULL/BEAR.
        In between = neutral contribution.

        Returns:
            Tuple of (regime_vote, raw_score).
        """
        vol_7d = data.get("vol_7d", 0.0)
        vol_30d = data.get("vol_30d", 0.0)

        if vol_30d <= 0:
            return "CHOP", 0.5

        ratio = vol_7d / vol_30d

        if ratio < 0.5:
            # Volatility compression — market undecided
            return "CHOP", 0.4

        if ratio > 1.5:
            # Volatility expansion — check direction
            direction = data.get("btc_trend_direction", "").lower()
            score = min(1.0, 0.5 + (ratio - 1.5) / 2.0)

            if direction == "up":
                return "BULL", score
            elif direction == "down":
                return "BEAR", score
            else:
                return "CHOP", 0.5

        # Normal volatility — slight signal from ratio
        score = 0.5
        return "CHOP", score

    def _vote_breadth(self, data: dict) -> tuple[str, float]:
        """Breadth signal: % of altcoins above their EMA50.

        > 70% = BULL, < 30% = BEAR, else CHOP.

        Returns:
            Tuple of (regime_vote, raw_score).
        """
        pct = data.get("altcoin_above_ema50_pct", 0.5)

        if pct > 0.70:
            # 70% -> 0.7, 90%+ -> 1.0
            score = min(1.0, 0.7 + (pct - 0.70) / 1.0)
            return "BULL", score

        if pct < 0.30:
            # 30% -> 0.7, 10%- -> 0.9
            score = min(1.0, 0.7 + (0.30 - pct) / 1.0)
            return "BEAR", score

        # 30-70% — choppy
        score = 0.5
        return "CHOP", score

    # ── Aggregation ───────────────────────────────────────────────────

    def _weighted_majority(self, votes: dict[str, str]) -> tuple[str, float]:
        """Compute weighted majority regime from individual votes.

        Args:
            votes: Dict of signal_name -> regime vote.

        Returns:
            Tuple of (winning_regime, confidence 0-1).
        """
        tallies: dict[str, float] = {"BULL": 0.0, "BEAR": 0.0, "CHOP": 0.0}

        for signal_name, vote in votes.items():
            weight = self._weights.get(signal_name, 0.0)
            if vote in tallies:
                tallies[vote] += weight

        # Winner is the regime with highest weighted vote
        winner = max(tallies, key=lambda r: tallies[r])
        total_weight = sum(tallies.values())

        if total_weight <= 0:
            return "CHOP", 0.0

        confidence = tallies[winner] / total_weight

        # If confidence is too close between top 2, call it CHOP
        sorted_tallies = sorted(tallies.values(), reverse=True)
        if len(sorted_tallies) >= 2:
            margin = sorted_tallies[0] - sorted_tallies[1]
            if margin < 0.10 and winner != "CHOP":
                # Very close race — treat as CHOP with low confidence
                return "CHOP", confidence * 0.7

        return winner, confidence
