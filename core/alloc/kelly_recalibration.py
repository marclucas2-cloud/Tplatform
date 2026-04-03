"""P4-02: Kelly Recalibration Framework — adapt Kelly fraction to live data.

Framework:
  1. Kelly initial: calibrated on OOS data (current)
  2. Kelly adaptatif: recalibrated on live trades (NEED_LIVE after 50+ trades)
  3. Kelly floor/ceiling: 1/32 to 1/4 (never 0 or full Kelly)
  4. Kelly par regime: multipliers per market regime
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Kelly fraction bounds
KELLY_FLOOR = 1 / 32        # 0.03125 — never zero
KELLY_CEILING = 1 / 4       # 0.25 — never full Kelly
KELLY_STOPPED = 0.0         # Hard stop

# Regime multipliers on top of Kelly fraction
REGIME_KELLY_MULTIPLIERS = {
    "TREND_STRONG": 1.2,
    "MEAN_REVERT": 1.0,
    "HIGH_VOL": 0.6,
    "PANIC": 0.3,
    "LOW_LIQUIDITY": 0.5,
    "UNKNOWN": 0.5,
    "TRENDING_UP": 1.1,
    "TRENDING_DOWN": 0.8,
    "RANGING": 1.0,
    "VOLATILE": 0.6,
    # Crypto
    "BULL": 1.2,
    "BEAR": 0.6,
    "CHOP": 0.8,
}

# Smoothing: new Kelly = alpha * calculated + (1-alpha) * old
SMOOTHING_ALPHA = 0.30  # 30% new, 70% old

# Drift detection thresholds
WIN_RATE_DRIFT_THRESHOLD = 0.80   # Live < 80% of OOS -> reduce
RATIO_DRIFT_THRESHOLD = 0.60     # Live < 60% of OOS -> reduce
MIN_TRADES_FOR_RECALIB = 50


@dataclass
class KellyMetrics:
    """Metrics used to compute Kelly fraction."""
    win_rate: float
    avg_win: float
    avg_loss: float
    n_trades: int
    source: str = "oos"  # "oos" or "live"


@dataclass
class KellyResult:
    """Computed Kelly fraction with all adjustments."""
    raw_kelly: float          # f* = (p*b - q) / b
    fractional_kelly: float   # After fraction (e.g., 1/4 Kelly)
    regime_adjusted: float    # After regime multiplier
    final_kelly: float        # After floor/ceiling + smoothing
    regime: str = "UNKNOWN"
    regime_multiplier: float = 1.0
    oos_win_rate: float = 0.0
    live_win_rate: float | None = None
    drift_detected: bool = False
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "raw_kelly": round(self.raw_kelly, 4),
            "fractional_kelly": round(self.fractional_kelly, 4),
            "regime_adjusted": round(self.regime_adjusted, 4),
            "final_kelly": round(self.final_kelly, 4),
            "regime": self.regime,
            "regime_multiplier": self.regime_multiplier,
            "oos_win_rate": round(self.oos_win_rate, 3),
            "live_win_rate": round(self.live_win_rate, 3) if self.live_win_rate is not None else None,
            "drift_detected": self.drift_detected,
            "details": self.details,
        }


class KellyRecalibrator:
    """Manages Kelly fraction recalibration with live data.

    Usage:
        recal = KellyRecalibrator()

        # Initial setup from OOS
        recal.set_oos_metrics("fx_carry_vs",
            win_rate=0.58, avg_win=45.0, avg_loss=25.0, n_trades=120)

        # Get current Kelly (no live data yet)
        result = recal.get_kelly("fx_carry_vs", regime="TREND_STRONG")
        print(result.final_kelly)  # e.g., 0.15

        # After accumulating live trades:
        recal.update_live_metrics("fx_carry_vs",
            win_rate=0.52, avg_win=38.0, avg_loss=28.0, n_trades=60)

        # Recalibrated Kelly
        result = recal.get_kelly("fx_carry_vs", regime="TREND_STRONG")
        print(result.final_kelly)  # Lower due to drift
    """

    def __init__(
        self,
        base_fraction: float = 0.25,  # 1/4 Kelly default
        floor: float = KELLY_FLOOR,
        ceiling: float = KELLY_CEILING,
        smoothing: float = SMOOTHING_ALPHA,
    ):
        self._base_fraction = base_fraction
        self._floor = floor
        self._ceiling = ceiling
        self._smoothing = smoothing
        self._oos: dict[str, KellyMetrics] = {}
        self._live: dict[str, KellyMetrics] = {}
        self._prev_kelly: dict[str, float] = {}

    def set_oos_metrics(
        self,
        strategy: str,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        n_trades: int,
    ):
        """Set OOS-calibrated metrics for a strategy."""
        self._oos[strategy] = KellyMetrics(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            n_trades=n_trades,
            source="oos",
        )

    def update_live_metrics(
        self,
        strategy: str,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        n_trades: int,
    ):
        """Update live-calibrated metrics (call after each trade batch)."""
        self._live[strategy] = KellyMetrics(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            n_trades=n_trades,
            source="live",
        )

    def get_kelly(
        self,
        strategy: str,
        regime: str = "UNKNOWN",
        kelly_mode: str = "NOMINAL",  # From DynamicKellyManager
    ) -> KellyResult:
        """Compute the Kelly fraction for a strategy.

        Uses live data if available (>= 50 trades), otherwise OOS.
        """
        oos = self._oos.get(strategy)
        live = self._live.get(strategy)

        if not oos:
            return KellyResult(
                raw_kelly=0, fractional_kelly=0,
                regime_adjusted=0, final_kelly=self._floor,
                regime=regime,
                details={"error": f"No OOS metrics for {strategy}"},
            )

        # Choose data source
        use_live = live is not None and live.n_trades >= MIN_TRADES_FOR_RECALIB
        metrics = live if use_live else oos

        # 1. Raw Kelly: f* = (p*b - q) / b
        raw_kelly = self._compute_raw_kelly(metrics.win_rate, metrics.avg_win, metrics.avg_loss)

        # 2. Fractional Kelly
        fraction = self._get_fraction(kelly_mode)
        fractional = raw_kelly * fraction

        # 3. Drift detection
        drift = False
        if use_live and oos:
            wr_drift = live.win_rate < oos.win_rate * WIN_RATE_DRIFT_THRESHOLD
            ratio_live = live.avg_win / live.avg_loss if live.avg_loss > 0 else 0
            ratio_oos = oos.avg_win / oos.avg_loss if oos.avg_loss > 0 else 0
            ratio_drift = ratio_live < ratio_oos * RATIO_DRIFT_THRESHOLD

            if wr_drift or ratio_drift:
                drift = True
                fractional *= 0.5  # Halve Kelly on drift
                logger.warning(
                    "KELLY DRIFT: %s — live WR=%.2f vs OOS=%.2f, "
                    "live ratio=%.2f vs OOS=%.2f",
                    strategy, live.win_rate, oos.win_rate,
                    ratio_live, ratio_oos,
                )

        # 4. Regime adjustment
        regime_mult = REGIME_KELLY_MULTIPLIERS.get(regime, 1.0)
        regime_adjusted = fractional * regime_mult

        # 5. Smoothing
        prev = self._prev_kelly.get(strategy)
        if prev is not None:
            smoothed = self._smoothing * regime_adjusted + (1 - self._smoothing) * prev
        else:
            smoothed = regime_adjusted

        # 6. Floor/ceiling
        final = max(self._floor, min(self._ceiling, smoothed))
        if kelly_mode == "STOPPED":
            final = KELLY_STOPPED

        self._prev_kelly[strategy] = final

        return KellyResult(
            raw_kelly=raw_kelly,
            fractional_kelly=fractional,
            regime_adjusted=regime_adjusted,
            final_kelly=final,
            regime=regime,
            regime_multiplier=regime_mult,
            oos_win_rate=oos.win_rate,
            live_win_rate=live.win_rate if live else None,
            drift_detected=drift,
            details={
                "source": "live" if use_live else "oos",
                "kelly_mode": kelly_mode,
                "fraction": fraction,
                "smoothed": round(smoothed, 4),
                "n_trades": metrics.n_trades,
            },
        )

    def _compute_raw_kelly(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """Compute raw Kelly criterion: f* = (p*b - q) / b."""
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0

        b = avg_win / avg_loss  # Win/loss ratio
        p = win_rate
        q = 1.0 - p

        kelly = (p * b - q) / b
        return max(0.0, kelly)

    def _get_fraction(self, kelly_mode: str) -> float:
        """Map Kelly mode to fraction."""
        fractions = {
            "AGGRESSIVE": 0.50,
            "NOMINAL": 0.25,
            "DEFENSIVE": 0.0625,
            "STOPPED": 0.0,
        }
        return fractions.get(kelly_mode, self._base_fraction)

    def get_all_kellys(self, regime: str = "UNKNOWN") -> dict[str, KellyResult]:
        """Get Kelly fractions for all registered strategies."""
        results = {}
        for strategy in self._oos:
            results[strategy] = self.get_kelly(strategy, regime)
        return results
