"""P2-01: Signal Quality Filter v2 — score-based signal filtering.

Filters weak signals BEFORE they generate orders. Each signal receives
a quality score (0.0 to 1.0) based on 5 dimensions:
  1. Signal Strength (0.0 - 0.3) — distance from trigger threshold
  2. Regime Alignment (0.0 - 0.2) — signal direction vs market regime
  3. Confluence (0.0 - 0.2) — confirmation from other strategies
  4. Timing (0.0 - 0.15) — liquidity/session quality
  5. Volatility Context (0.0 - 0.15) — vol in historically profitable range

Score thresholds:
  VALIDATED strats: >= 0.4
  BORDERLINE strats: >= 0.6
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SignalVerdict(str, Enum):
    TRADE = "TRADE"
    SKIP = "SKIP"
    REDUCE = "REDUCE"  # Trade but with reduced size


class StratTier(str, Enum):
    VALIDATED = "VALIDATED"
    BORDERLINE = "BORDERLINE"


TIER_THRESHOLDS = {
    StratTier.VALIDATED: 0.40,
    StratTier.BORDERLINE: 0.60,
}

# Regime alignment mapping: (signal_direction, regime) -> score
REGIME_ALIGNMENT = {
    # Trend-following signals
    ("BUY", "TREND_STRONG"): 0.20,
    ("BUY", "TRENDING_UP"): 0.20,
    ("SELL", "TREND_STRONG"): 0.15,  # short in trend = acceptable if short strategy
    ("SELL", "TRENDING_DOWN"): 0.20,
    # Mean-reversion signals
    ("BUY", "MEAN_REVERT"): 0.15,
    ("BUY", "RANGING"): 0.15,
    ("SELL", "MEAN_REVERT"): 0.15,
    ("SELL", "RANGING"): 0.15,
    # Neutral
    ("BUY", "HIGH_VOL"): 0.05,
    ("SELL", "HIGH_VOL"): 0.10,
    ("BUY", "VOLATILE"): 0.05,
    ("SELL", "VOLATILE"): 0.10,
    # Panic — only shorts aligned
    ("BUY", "PANIC"): 0.00,
    ("SELL", "PANIC"): 0.20,
    # Unknown/low liquidity
    ("BUY", "UNKNOWN"): 0.05,
    ("SELL", "UNKNOWN"): 0.05,
    ("BUY", "LOW_LIQUIDITY"): 0.02,
    ("SELL", "LOW_LIQUIDITY"): 0.02,
}

# Liquidity windows (CET hours) — score by asset class
LIQUIDITY_WINDOWS = {
    "fx": {
        "high": [(13, 17)],    # London/NY overlap
        "medium": [(8, 13), (17, 22)],
        "low": [(0, 8), (22, 24)],
    },
    "us_equity": {
        "high": [(15, 22)],    # US session (15:30-22:00 CET)
        "medium": [(14, 15)],  # Pre-market
        "low": [(0, 14), (22, 24)],
    },
    "eu_equity": {
        "high": [(9, 17)],     # EU session
        "medium": [(8, 9)],
        "low": [(0, 8), (17, 24)],
    },
    "crypto": {
        "high": [(14, 22)],    # Peak crypto volume
        "medium": [(8, 14), (22, 2)],
        "low": [(2, 8)],
    },
}

TIMING_SCORES = {"high": 0.15, "medium": 0.08, "low": 0.02}


@dataclass
class SignalScore:
    """Detailed signal quality score breakdown."""
    strategy: str
    symbol: str
    direction: str
    strength: float = 0.0       # 0.0 - 0.30
    regime_alignment: float = 0.0  # 0.0 - 0.20
    confluence: float = 0.0     # 0.0 - 0.20
    timing: float = 0.0        # 0.0 - 0.15
    vol_context: float = 0.0   # 0.0 - 0.15
    total: float = 0.0
    verdict: SignalVerdict = SignalVerdict.SKIP
    details: dict = field(default_factory=dict)

    def compute_total(self):
        self.total = round(
            self.strength + self.regime_alignment + self.confluence
            + self.timing + self.vol_context, 3
        )


class SignalQualityFilter:
    """Filters weak signals based on multi-factor quality score.

    Usage:
        sqf = SignalQualityFilter()
        score = sqf.score_signal(
            strategy="fx_carry_vs",
            symbol="EURUSD",
            direction="BUY",
            signal_value=0.75,       # Raw signal output
            trigger_threshold=0.5,    # Strategy's entry threshold
            regime="TREND_STRONG",
            asset_class="fx",
            concurrent_signals=["fx_vol_scaling"],  # Other active signals same direction
            current_vol=12.5,
            historical_vol_range=(8.0, 25.0),
            tier=StratTier.VALIDATED,
        )
        if score.verdict == SignalVerdict.TRADE:
            execute_trade(...)
    """

    def __init__(
        self,
        validated_threshold: float = 0.40,
        borderline_threshold: float = 0.60,
        reduce_threshold: float = 0.30,
    ):
        self._validated_threshold = validated_threshold
        self._borderline_threshold = borderline_threshold
        self._reduce_threshold = reduce_threshold
        self._active_signals: dict[str, list[dict]] = {}  # symbol -> [signals]

    def register_active_signal(
        self,
        symbol: str,
        strategy: str,
        direction: str,
    ):
        """Register an active signal for confluence detection."""
        self._active_signals.setdefault(symbol, []).append({
            "strategy": strategy,
            "direction": direction,
        })

    def clear_signals(self):
        """Clear all registered active signals."""
        self._active_signals.clear()

    def score_signal(
        self,
        strategy: str,
        symbol: str,
        direction: str,
        signal_value: float,
        trigger_threshold: float,
        regime: str = "UNKNOWN",
        asset_class: str = "us_equity",
        concurrent_signals: list[str] | None = None,
        current_vol: float | None = None,
        historical_vol_range: tuple[float, float] | None = None,
        tier: StratTier = StratTier.VALIDATED,
        timestamp: datetime | None = None,
    ) -> SignalScore:
        """Score a trading signal across 5 quality dimensions."""
        score = SignalScore(
            strategy=strategy,
            symbol=symbol,
            direction=direction,
        )

        # 1. Signal Strength (0.0 - 0.30)
        score.strength = self._score_strength(
            signal_value, trigger_threshold
        )

        # 2. Regime Alignment (0.0 - 0.20)
        score.regime_alignment = self._score_regime(direction, regime)

        # 3. Confluence (0.0 - 0.20)
        score.confluence = self._score_confluence(
            symbol, strategy, direction, concurrent_signals
        )

        # 4. Timing (0.0 - 0.15)
        score.timing = self._score_timing(asset_class, timestamp)

        # 5. Volatility Context (0.0 - 0.15)
        score.vol_context = self._score_vol_context(
            current_vol, historical_vol_range
        )

        score.compute_total()

        # Verdict
        threshold = (
            self._borderline_threshold
            if tier == StratTier.BORDERLINE
            else self._validated_threshold
        )

        if score.total >= threshold:
            score.verdict = SignalVerdict.TRADE
        elif score.total >= self._reduce_threshold:
            score.verdict = SignalVerdict.REDUCE
        else:
            score.verdict = SignalVerdict.SKIP

        score.details = {
            "threshold_used": threshold,
            "tier": tier.value,
            "regime": regime,
            "asset_class": asset_class,
        }

        logger.info(
            "Signal %s %s %s: score=%.2f [str=%.2f reg=%.2f conf=%.2f "
            "tim=%.2f vol=%.2f] -> %s",
            strategy, symbol, direction, score.total,
            score.strength, score.regime_alignment, score.confluence,
            score.timing, score.vol_context, score.verdict.value,
        )

        return score

    def _score_strength(
        self,
        signal_value: float,
        trigger_threshold: float,
    ) -> float:
        """Score based on distance from trigger threshold.

        A signal barely crossing the threshold = weak (0.05).
        A signal far past the threshold = strong (0.25+).
        """
        if trigger_threshold == 0:
            return 0.15  # Neutral if no threshold

        # Normalize distance: how far past the threshold (0 to inf)
        if signal_value == 0:
            return 0.0

        distance_ratio = abs(signal_value - trigger_threshold) / abs(trigger_threshold)

        # Map to 0.0-0.30 with diminishing returns
        # distance_ratio=0 -> 0.03, =0.5 -> 0.15, =1.0 -> 0.22, =2.0 -> 0.28
        strength = min(0.30, 0.03 + 0.25 * (1 - 1 / (1 + distance_ratio * 2)))
        return round(strength, 3)

    def _score_regime(self, direction: str, regime: str) -> float:
        """Score based on signal direction alignment with regime."""
        key = (direction.upper(), regime.upper())
        return REGIME_ALIGNMENT.get(key, 0.10)

    def _score_confluence(
        self,
        symbol: str,
        strategy: str,
        direction: str,
        concurrent_signals: list[str] | None = None,
    ) -> float:
        """Score based on confirmation from other strategies."""
        score = 0.0

        # Check registered active signals
        active = self._active_signals.get(symbol, [])
        for sig in active:
            if sig["strategy"] == strategy:
                continue
            if sig["direction"] == direction:
                score += 0.10  # Confirmation
            else:
                score -= 0.10  # Contradiction

        # Check explicit concurrent signals
        if concurrent_signals:
            score += len(concurrent_signals) * 0.10

        return round(max(0.0, min(0.20, score)), 3)

    def _score_timing(
        self,
        asset_class: str,
        timestamp: datetime | None = None,
    ) -> float:
        """Score based on time of day and asset class liquidity."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # Convert to CET (UTC+1, ignoring DST for simplicity)
        hour_cet = (timestamp.hour + 1) % 24

        windows = LIQUIDITY_WINDOWS.get(asset_class, LIQUIDITY_WINDOWS["us_equity"])

        for quality, ranges in windows.items():
            for start, end in ranges:
                if start <= end:
                    if start <= hour_cet < end:
                        return TIMING_SCORES[quality]
                else:  # Wraps around midnight
                    if hour_cet >= start or hour_cet < end:
                        return TIMING_SCORES[quality]

        return TIMING_SCORES["low"]

    def _score_vol_context(
        self,
        current_vol: float | None,
        historical_range: tuple[float, float] | None,
    ) -> float:
        """Score based on current vol being in historically profitable range.

        Typically: moderate vol is best (not too calm, not too crazy).
        """
        if current_vol is None or historical_range is None:
            return 0.08  # Neutral if no data

        low, high = historical_range
        if low >= high:
            return 0.08

        # "Sweet spot" is between 25th and 75th percentile of historical range
        range_width = high - low
        pct_25 = low + range_width * 0.25
        pct_75 = low + range_width * 0.75

        if pct_25 <= current_vol <= pct_75:
            return 0.15  # In sweet spot
        elif low <= current_vol <= high:
            return 0.08  # In range but not sweet spot
        else:
            return 0.02  # Outside historical range


def get_size_multiplier(verdict: SignalVerdict) -> float:
    """Get position size multiplier based on signal verdict."""
    return {
        SignalVerdict.TRADE: 1.0,
        SignalVerdict.REDUCE: 0.5,
        SignalVerdict.SKIP: 0.0,
    }[verdict]
