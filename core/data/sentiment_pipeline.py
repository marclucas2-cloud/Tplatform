"""P5-03: Sentiment Pipeline — sentiment as a signal filter (not primary signal).

Sources:
  1. Fear & Greed Index (alternative.me — free, daily)
  2. Social volume proxy (via CoinGecko trending)
  3. News sentiment (optional, via API)

Usage: sentiment is a FILTER, not a primary signal.
  signal_quality_score += 0.05 if sentiment confirms direction
  signal_quality_score -= 0.05 if sentiment contradicts

Validation: if adding sentiment filter doesn't change Sharpe by > 0.05 -> SKIP.
"""

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "sentiment"


@dataclass
class SentimentSnapshot:
    """A single sentiment observation."""
    timestamp: str
    source: str  # "fear_greed", "social", "news"
    asset: str   # "BTC", "ETH", "SPY", "market"
    score: float  # -1.0 (extreme bearish) to +1.0 (extreme bullish)
    raw_value: Any = None
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "asset": self.asset,
            "score": round(self.score, 3),
            "raw_value": self.raw_value,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class SentimentFilter:
    """Sentiment filter result for a signal."""
    composite_score: float  # -1.0 to 1.0
    confirms_direction: bool
    adjustment: float  # +/- 0.05 for signal quality
    sources_used: int
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "composite_score": round(self.composite_score, 3),
            "confirms_direction": self.confirms_direction,
            "adjustment": round(self.adjustment, 3),
            "sources_used": self.sources_used,
            "details": self.details,
        }


class SentimentStore:
    """Persists sentiment data in JSONL files."""

    def __init__(self, data_dir: Path | None = None):
        self._dir = data_dir or DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, snapshot: SentimentSnapshot):
        path = self._dir / f"sentiment_{snapshot.source}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(snapshot.to_dict()) + "\n")

    def load_recent(
        self,
        source: str,
        asset: str = "market",
        days: int = 7,
    ) -> list[SentimentSnapshot]:
        path = self._dir / f"sentiment_{source}.jsonl"
        if not path.exists():
            return []

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        results = []
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("timestamp", "") >= cutoff and data.get("asset") == asset:
                    results.append(SentimentSnapshot(**data))
        return results


class SentimentPipeline:
    """Processes sentiment data into signal quality adjustments.

    Usage:
        pipeline = SentimentPipeline()

        # Ingest data
        pipeline.ingest_fear_greed(35)  # Fear level 35
        pipeline.ingest_social_volume("BTC", 2.5)  # 2.5x normal volume

        # Get filter for a signal
        result = pipeline.filter_signal("BTC", "BUY")
        print(result.adjustment)  # +0.05 if sentiment confirms, -0.05 if contradicts
    """

    # Impact per source (modeste by design)
    SENTIMENT_IMPACT = 0.05

    def __init__(self, data_dir: Path | None = None):
        self._store = SentimentStore(data_dir)
        self._current: dict[str, dict[str, SentimentSnapshot]] = {}  # source -> asset -> snapshot

    def ingest_fear_greed(self, index: int):
        """Ingest Fear & Greed Index (0-100).

        Maps to score: 0=-1.0 (extreme fear → contrarian bullish),
        100=+1.0 (extreme greed → contrarian bearish)
        """
        # Contrarian interpretation
        if index <= 25:
            score = 0.5 + (25 - index) / 50  # 0.5 to 1.0 (bullish)
        elif index >= 75:
            score = -0.5 - (index - 75) / 50  # -0.5 to -1.0 (bearish)
        else:
            score = (50 - index) / 50  # -0.5 to 0.5

        snapshot = SentimentSnapshot(
            timestamp=datetime.now(UTC).isoformat(),
            source="fear_greed",
            asset="market",
            score=score,
            raw_value=index,
            confidence=0.6,
        )
        self._store.store(snapshot)
        self._current.setdefault("fear_greed", {})["market"] = snapshot

    def ingest_social_volume(self, asset: str, volume_ratio: float):
        """Ingest social volume as ratio to normal (1.0 = normal).

        High volume without price move = potential contrarian signal.
        """
        # Extreme social volume can mean either euphoria or panic
        if volume_ratio > 3.0:
            score = -0.3  # Too much hype = cautious
        elif volume_ratio > 2.0:
            score = -0.1
        elif volume_ratio < 0.3:
            score = 0.1  # Low interest = potential bottom
        else:
            score = 0.0

        snapshot = SentimentSnapshot(
            timestamp=datetime.now(UTC).isoformat(),
            source="social",
            asset=asset,
            score=score,
            raw_value=volume_ratio,
            confidence=0.3,  # Low confidence — social data is noisy
        )
        self._store.store(snapshot)
        self._current.setdefault("social", {})[asset] = snapshot

    def ingest_news_sentiment(self, asset: str, score: float, confidence: float = 0.5):
        """Ingest news sentiment score (-1.0 to 1.0)."""
        snapshot = SentimentSnapshot(
            timestamp=datetime.now(UTC).isoformat(),
            source="news",
            asset=asset,
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
        )
        self._store.store(snapshot)
        self._current.setdefault("news", {})[asset] = snapshot

    def filter_signal(
        self,
        asset: str,
        direction: str,
    ) -> SentimentFilter:
        """Apply sentiment filter to a trading signal.

        Returns adjustment to signal_quality_score (+/- 0.05).
        """
        # Collect all current sentiment for this asset
        sentiments = []

        for source in ["fear_greed", "social", "news"]:
            # Try asset-specific first, then "market"
            snap = self._current.get(source, {}).get(asset)
            if snap is None:
                snap = self._current.get(source, {}).get("market")
            if snap is not None:
                sentiments.append(snap)

        if not sentiments:
            return SentimentFilter(
                composite_score=0.0,
                confirms_direction=True,  # Neutral = don't penalize
                adjustment=0.0,
                sources_used=0,
            )

        # Weighted composite
        total_weight = sum(s.confidence for s in sentiments)
        if total_weight == 0:
            composite = 0.0
        else:
            composite = sum(s.score * s.confidence for s in sentiments) / total_weight

        # Does sentiment confirm the direction?
        direction_sign = 1.0 if direction.upper() == "BUY" else -1.0
        confirms = (composite * direction_sign) >= 0

        # Adjustment
        if confirms:
            adjustment = self.SENTIMENT_IMPACT
        else:
            adjustment = -self.SENTIMENT_IMPACT

        return SentimentFilter(
            composite_score=composite,
            confirms_direction=confirms,
            adjustment=adjustment,
            sources_used=len(sentiments),
            details=[s.to_dict() for s in sentiments],
        )

    def get_current_sentiment(self) -> dict[str, Any]:
        """Get all current sentiment snapshots."""
        result = {}
        for source, assets in self._current.items():
            for asset, snap in assets.items():
                result[f"{source}:{asset}"] = snap.to_dict()
        return result
