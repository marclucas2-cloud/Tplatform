"""
ROC-C02 — Crypto conviction sizing for Binance France.

Calculates a conviction score (0-1) from 5 weighted signals, then adjusts
Kelly-based position size accordingly.  Works with margin + spot strategies
(NO futures/perp).

5 signals:
  1. trend_strength  (0.25) — ADX-based trend quality
  2. volume_confirm  (0.20) — 24h volume vs baseline
  3. regime_align    (0.25) — signal direction vs crypto regime
  4. borrow_cost     (0.15) — penalises expensive shorts
  5. correlation     (0.15) — BTC decorrelation bonus

4 conviction tiers:
  STRONG  >= 0.8  → 1.5× kelly  (max 3/16 kelly)
  NORMAL  >= 0.5  → 1.0× kelly  (max 1/8 kelly)
  WEAK    >= 0.3  → 0.7× kelly  (max 0.09 kelly)
  SKIP    <  0.3  → 0×          (no trade)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

SIGNAL_WEIGHTS = {
    "trend_strength": 0.25,
    "volume_confirm": 0.20,
    "regime_align": 0.25,
    "borrow_cost": 0.15,
    "correlation": 0.15,
}

CONVICTION_TIERS = [
    # (min_score, label, kelly_multiplier, max_kelly_fraction)
    (0.8, "STRONG", 1.5, 3 / 16),     # 0.1875
    (0.5, "NORMAL", 1.0, 1 / 8),      # 0.125
    (0.3, "WEAK",   0.7, 0.09),
    (0.0, "SKIP",   0.0, 0.0),
]


# ──────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────

def _score_trend_strength(adx: float) -> float:
    """Score from ADX value. Higher ADX = stronger trend.

    Args:
        adx: Average Directional Index value (0-100 typical range).

    Returns:
        Score 0.2 – 1.0.
    """
    if adx > 40:
        return 1.0
    if adx > 30:
        return 0.8
    if adx > 25:
        return 0.6
    if adx > 20:
        return 0.4
    return 0.2


def _score_volume_confirm(volume_ratio_24h: float) -> float:
    """Score from 24h volume relative to baseline.

    Args:
        volume_ratio_24h: current_24h_vol / avg_24h_vol.

    Returns:
        Score 0.2 – 1.0.
    """
    if volume_ratio_24h > 2.0:
        return 1.0
    if volume_ratio_24h > 1.5:
        return 0.8
    if volume_ratio_24h > 1.0:
        return 0.6
    if volume_ratio_24h > 0.8:
        return 0.4
    return 0.2


def _score_regime_align(side: str, regime: str) -> float:
    """Score alignment between trade direction and crypto regime.

    Args:
        side: "BUY" or "SELL".
        regime: "BULL", "BEAR", or "CHOP".

    Returns:
        Score 0.3 – 1.0.
    """
    side_upper = side.upper()
    regime_upper = regime.upper()

    if regime_upper == "CHOP":
        return 0.5

    # Aligned with regime
    if side_upper == "BUY" and regime_upper == "BULL":
        return 1.0
    if side_upper == "SELL" and regime_upper == "BEAR":
        return 1.0

    # Counter-trend
    return 0.3


def _score_borrow_cost(side: str, borrow_rate_daily: float) -> float:
    """Score borrow cost — only penalises shorts.

    Args:
        side: "BUY" or "SELL".
        borrow_rate_daily: daily borrow rate as decimal (0.001 = 0.1%).

    Returns:
        Score 0.2 – 1.0.
    """
    if side.upper() == "BUY":
        # Longs don't borrow — neutral score
        return 0.8

    # For shorts: penalise expensive borrows
    if borrow_rate_daily < 0.0003:     # < 0.03%/day (~11%/an) — cheap
        return 1.0
    if borrow_rate_daily < 0.0005:     # < 0.05%/day (~18%/an) — moderate
        return 0.7
    if borrow_rate_daily < 0.001:      # < 0.1%/day  (~36%/an) — expensive
        return 0.4
    # > 0.1%/day — very expensive
    return 0.2


def _score_correlation(btc_correlation_7d: float) -> float:
    """Score BTC decorrelation — lower correlation = more diversification value.

    Args:
        btc_correlation_7d: 7-day correlation with BTC (-1 to +1).

    Returns:
        Score 0.3 – 1.0.
    """
    abs_corr = abs(btc_correlation_7d)
    if abs_corr < 0.3:
        return 1.0
    if abs_corr < 0.5:
        return 0.8
    if abs_corr < 0.7:
        return 0.6
    return 0.3


# ──────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ConvictionResult:
    """Result of a conviction calculation."""

    score: float
    level: str               # STRONG / NORMAL / WEAK / SKIP
    kelly_multiplier: float
    max_kelly_fraction: float
    breakdown: dict[str, float]
    timestamp: str


class CryptoConvictionSizer:
    """Crypto-specific conviction sizer — adjusts Kelly fractions by signal quality.

    Usage::

        sizer = CryptoConvictionSizer()
        score, breakdown = sizer.calculate_conviction(signal, market_state)
        size, score, level = sizer.get_adjusted_size(signal, market_state, 0.12, 15_000)
    """

    def __init__(self, weights: Optional[dict[str, float]] = None):
        """Initialise with optional custom weights.

        Args:
            weights: Dict mapping signal name to weight (must sum to 1.0).
                     Defaults to SIGNAL_WEIGHTS.
        """
        self._weights = weights or dict(SIGNAL_WEIGHTS)

        # Sanity-check weights sum to ~1.0
        total = sum(self._weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(
                "Conviction weights sum to %.3f (expected 1.0) — normalising", total
            )
            for k in self._weights:
                self._weights[k] /= total

    # ── Public API ────────────────────────────────────────────────────

    def calculate_conviction(
        self,
        signal: dict,
        market_state: dict,
    ) -> tuple[float, dict[str, float]]:
        """Calculate conviction score from signal + market state.

        Args:
            signal: Signal dict with at least ``side`` (BUY/SELL).
            market_state: Dict with keys:
                - adx (float)
                - volume_ratio_24h (float)
                - regime (str: BULL/BEAR/CHOP)
                - borrow_rate_daily (float)
                - btc_correlation_7d (float)

        Returns:
            Tuple of (score 0-1, breakdown dict per signal).
        """
        side = signal.get("side", "BUY")

        scores = {
            "trend_strength": _score_trend_strength(
                market_state.get("adx", 15)
            ),
            "volume_confirm": _score_volume_confirm(
                market_state.get("volume_ratio_24h", 1.0)
            ),
            "regime_align": _score_regime_align(
                side, market_state.get("regime", "CHOP")
            ),
            "borrow_cost": _score_borrow_cost(
                side, market_state.get("borrow_rate_daily", 0.0)
            ),
            "correlation": _score_correlation(
                market_state.get("btc_correlation_7d", 0.5)
            ),
        }

        weighted_score = sum(
            scores[k] * self._weights.get(k, 0) for k in scores
        )

        # Clamp to [0, 1]
        weighted_score = max(0.0, min(1.0, weighted_score))

        logger.debug(
            "Conviction score=%.3f side=%s regime=%s breakdown=%s",
            weighted_score,
            side,
            market_state.get("regime", "?"),
            scores,
        )

        return weighted_score, scores

    def get_adjusted_size(
        self,
        signal: dict,
        market_state: dict,
        base_kelly: float,
        capital: float,
    ) -> tuple[float, float, str]:
        """Compute position size adjusted by conviction.

        Args:
            signal: Signal dict.
            market_state: Market state dict (see calculate_conviction).
            base_kelly: Base Kelly fraction (e.g. 0.125 for 1/8).
            capital: Total capital in USD.

        Returns:
            Tuple of (size_usd, score, level_name).
            size_usd = 0.0 if conviction is SKIP.
        """
        score, breakdown = self.calculate_conviction(signal, market_state)

        # Find matching tier
        level_name = "SKIP"
        kelly_mult = 0.0
        max_frac = 0.0

        for min_score, label, mult, frac in CONVICTION_TIERS:
            if score >= min_score:
                level_name = label
                kelly_mult = mult
                max_frac = frac
                break

        # Adjusted kelly capped by tier max
        adjusted_kelly = min(base_kelly * kelly_mult, max_frac)
        size_usd = capital * adjusted_kelly

        logger.info(
            "ConvictionSizer: score=%.3f level=%s base_kelly=%.4f "
            "adjusted_kelly=%.4f size=$%.2f",
            score,
            level_name,
            base_kelly,
            adjusted_kelly,
            size_usd,
        )

        return size_usd, score, level_name

    def get_conviction_result(
        self,
        signal: dict,
        market_state: dict,
        base_kelly: float,
    ) -> ConvictionResult:
        """Full conviction result as a dataclass.

        Args:
            signal: Signal dict.
            market_state: Market state dict.
            base_kelly: Base Kelly fraction.

        Returns:
            ConvictionResult with all fields populated.
        """
        score, breakdown = self.calculate_conviction(signal, market_state)

        level_name = "SKIP"
        kelly_mult = 0.0
        max_frac = 0.0

        for min_score, label, mult, frac in CONVICTION_TIERS:
            if score >= min_score:
                level_name = label
                kelly_mult = mult
                max_frac = frac
                break

        return ConvictionResult(
            score=round(score, 4),
            level=level_name,
            kelly_multiplier=kelly_mult,
            max_kelly_fraction=max_frac,
            breakdown={k: round(v, 4) for k, v in breakdown.items()},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
