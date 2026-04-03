"""P5-01: Data Quality Scoring — quality score per instrument/timeframe.

Metrics:
  1. Completeness: % of bars present vs expected
  2. Freshness: age of the last bar
  3. Consistency: number of gaps > 2x expected frequency
  4. Outliers: number of bad ticks (z-score > threshold)
  5. Volume: % of bars with volume = 0

Score: 0-100 (EXCELLENT >90, BON 70-90, ACCEPTABLE 50-70, MAUVAIS <50)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


class QualityLevel:
    EXCELLENT = "EXCELLENT"  # > 90
    BON = "BON"              # 70-90
    ACCEPTABLE = "ACCEPTABLE"  # 50-70
    MAUVAIS = "MAUVAIS"     # < 50


@dataclass
class InstrumentQuality:
    """Quality metrics for a single instrument/timeframe."""
    symbol: str
    timeframe: str
    completeness: float = 0.0     # 0-100
    freshness_hours: float = 0.0  # Hours since last bar
    consistency: float = 0.0      # 0-100 (100 = no gaps)
    outlier_pct: float = 0.0      # % of bars that are outliers
    zero_volume_pct: float = 0.0  # % of bars with volume = 0
    total_bars: int = 0
    expected_bars: int = 0
    n_gaps: int = 0
    n_outliers: int = 0
    score: float = 0.0
    level: str = QualityLevel.MAUVAIS
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "score": round(self.score, 1),
            "level": self.level,
            "completeness": round(self.completeness, 1),
            "freshness_hours": round(self.freshness_hours, 1),
            "consistency": round(self.consistency, 1),
            "outlier_pct": round(self.outlier_pct, 2),
            "zero_volume_pct": round(self.zero_volume_pct, 1),
            "total_bars": self.total_bars,
            "expected_bars": self.expected_bars,
            "n_gaps": self.n_gaps,
            "n_outliers": self.n_outliers,
            "details": self.details,
        }


@dataclass
class DataQualityReport:
    """Full data quality report."""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    instruments: dict[str, InstrumentQuality] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, quality: InstrumentQuality):
        key = f"{quality.symbol}:{quality.timeframe}"
        self.instruments[key] = quality

    def compute_summary(self):
        by_level = {
            QualityLevel.EXCELLENT: [],
            QualityLevel.BON: [],
            QualityLevel.ACCEPTABLE: [],
            QualityLevel.MAUVAIS: [],
        }
        for key, q in self.instruments.items():
            by_level[q.level].append(key)

        scores = [q.score for q in self.instruments.values()]
        self.summary = {
            "total_instruments": len(self.instruments),
            "avg_score": round(np.mean(scores), 1) if scores else 0,
            "min_score": round(min(scores), 1) if scores else 0,
            "by_level": {level: strats for level, strats in by_level.items()},
            "alerts": [
                key for key, q in self.instruments.items()
                if q.score < 70
            ],
        }

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "summary": self.summary,
            "instruments": {k: v.to_dict() for k, v in self.instruments.items()},
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / "data_quality_report.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Data quality report saved to %s", path)


# Expected bars per year by timeframe
EXPECTED_BARS_PER_YEAR = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
    "1D": 365,
}

# Trading hours factor (not all markets trade 24h)
TRADING_HOURS_FACTOR = {
    "fx": 5 / 7,     # 5 days, ~24h
    "us_equity": 6.5 / 24 * 5 / 7,  # 6.5h/day, 5 days
    "eu_equity": 8.5 / 24 * 5 / 7,  # 8.5h/day, 5 days
    "crypto": 1.0,    # 24/7
    "futures": 23 / 24 * 5 / 7,  # ~23h/day, 5 days
}

TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1D": 1440,
}


class DataQualityScorer:
    """Scores data quality per instrument/timeframe.

    Usage:
        scorer = DataQualityScorer()
        quality = scorer.score(df, "EURUSD", "1h", asset_class="fx")
        print(quality.score, quality.level)  # 92.3 EXCELLENT
    """

    def __init__(
        self,
        outlier_zscore: float = 4.0,
        freshness_warning_hours: float = 24.0,
    ):
        self._outlier_zscore = outlier_zscore
        self._freshness_warning = freshness_warning_hours

    def score(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        asset_class: str = "us_equity",
    ) -> InstrumentQuality:
        """Score a single instrument's data quality.

        Args:
            df: DataFrame with columns: open, high, low, close, volume (opt)
                Index should be DatetimeIndex or 'timestamp' column
            symbol: Instrument symbol
            timeframe: "1m", "5m", "15m", "1h", "4h", "1d"
            asset_class: "fx", "us_equity", "eu_equity", "crypto", "futures"
        """
        quality = InstrumentQuality(symbol=symbol, timeframe=timeframe)

        if df.empty:
            return quality

        # Ensure datetime index
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            elif "date" in df.columns:
                df = df.set_index("date")

        df = df.sort_index()
        quality.total_bars = len(df)

        # 1. Completeness
        quality.completeness, quality.expected_bars = self._score_completeness(
            df, timeframe, asset_class
        )

        # 2. Freshness
        quality.freshness_hours = self._score_freshness(df)

        # 3. Consistency (gap detection)
        quality.consistency, quality.n_gaps = self._score_consistency(
            df, timeframe
        )

        # 4. Outliers
        quality.outlier_pct, quality.n_outliers = self._score_outliers(df)

        # 5. Zero volume
        quality.zero_volume_pct = self._score_volume(df)

        # Composite score (weighted average)
        freshness_score = max(0, 100 - quality.freshness_hours * 2)  # -2 per hour
        outlier_score = max(0, 100 - quality.outlier_pct * 20)  # -20 per 1% outliers
        volume_score = max(0, 100 - quality.zero_volume_pct)

        quality.score = (
            quality.completeness * 0.30
            + freshness_score * 0.15
            + quality.consistency * 0.25
            + outlier_score * 0.15
            + volume_score * 0.15
        )

        # Level
        if quality.score >= 90:
            quality.level = QualityLevel.EXCELLENT
        elif quality.score >= 70:
            quality.level = QualityLevel.BON
        elif quality.score >= 50:
            quality.level = QualityLevel.ACCEPTABLE
        else:
            quality.level = QualityLevel.MAUVAIS

        quality.details = {
            "freshness_score": round(freshness_score, 1),
            "outlier_score": round(outlier_score, 1),
            "volume_score": round(volume_score, 1),
            "date_range": f"{df.index[0]} to {df.index[-1]}",
        }

        return quality

    def score_all(
        self,
        datasets: dict[str, dict],
    ) -> DataQualityReport:
        """Score multiple instruments.

        Args:
            datasets: {symbol: {df, timeframe, asset_class}}
        """
        report = DataQualityReport()

        for symbol, info in datasets.items():
            quality = self.score(
                info["df"],
                symbol,
                info.get("timeframe", "1h"),
                info.get("asset_class", "us_equity"),
            )
            report.add(quality)

        report.compute_summary()
        return report

    def _score_completeness(
        self,
        df: pd.DataFrame,
        timeframe: str,
        asset_class: str,
    ) -> tuple[float, int]:
        """Completeness = actual bars / expected bars × 100."""
        if len(df) < 2:
            return 0.0, 0

        date_range_days = (df.index[-1] - df.index[0]).total_seconds() / 86400
        years = date_range_days / 365.25

        base_per_year = EXPECTED_BARS_PER_YEAR.get(timeframe, 8760)
        factor = TRADING_HOURS_FACTOR.get(asset_class, 0.5)
        expected = int(base_per_year * factor * years)

        if expected <= 0:
            return 100.0, len(df)

        completeness = min(100.0, len(df) / expected * 100)
        return round(completeness, 1), expected

    def _score_freshness(self, df: pd.DataFrame) -> float:
        """Hours since last bar."""
        if df.empty:
            return 9999.0

        last_ts = df.index[-1]
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")

        age = datetime.now(UTC) - last_ts.to_pydatetime().replace(tzinfo=UTC)
        return max(0, age.total_seconds() / 3600)

    def _score_consistency(
        self,
        df: pd.DataFrame,
        timeframe: str,
    ) -> tuple[float, int]:
        """Count gaps > 2x expected frequency."""
        if len(df) < 2:
            return 100.0, 0

        expected_minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
        gap_threshold = pd.Timedelta(minutes=expected_minutes * 2.5)

        diffs = df.index.to_series().diff().dropna()
        n_gaps = int((diffs > gap_threshold).sum())

        # Score: 100 if no gaps, decreasing
        total_intervals = len(diffs)
        gap_ratio = n_gaps / total_intervals if total_intervals > 0 else 0
        consistency = max(0, 100 * (1 - gap_ratio * 10))  # 10% gaps = 0 score

        return round(consistency, 1), n_gaps

    def _score_outliers(self, df: pd.DataFrame) -> tuple[float, int]:
        """Detect outliers using z-score on returns."""
        if len(df) < 10 or "close" not in df.columns:
            return 0.0, 0

        returns = df["close"].pct_change().dropna()
        if returns.std() == 0:
            return 0.0, 0

        z_scores = np.abs((returns - returns.mean()) / returns.std())
        n_outliers = int((z_scores > self._outlier_zscore).sum())
        pct = n_outliers / len(returns) * 100

        return round(pct, 2), n_outliers

    def _score_volume(self, df: pd.DataFrame) -> float:
        """% of bars with zero volume."""
        if "volume" not in df.columns:
            return 0.0  # No volume data = not applicable

        n_zero = int((df["volume"] == 0).sum())
        return round(n_zero / len(df) * 100, 1) if len(df) > 0 else 0.0
