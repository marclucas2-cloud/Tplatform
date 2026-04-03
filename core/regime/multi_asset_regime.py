"""
D1-01 — Multi-Asset Regime Detector.

Detects market regime per asset class and globally. Deterministic rules
(no ML). Designed to protect FX Carry from carry crashes and cut exposure
in PANIC conditions.

Regimes:
  TREND_STRONG  — ADX > 25, vol_ratio < 1.5, corr stable
  MEAN_REVERT   — ADX < 20, vol_ratio < 1.2, spread tight
  HIGH_VOL      — vol_ratio > 2.0, corr rising
  PANIC         — vol_ratio > 3.0, corr > 0.8, spread blow-out
  LOW_LIQUIDITY — spread_zscore > 2.0, volume < 50% average
  UNKNOWN       — missing data → all strats go DEFENSIVE

Hysteresis: regime must persist 2 consecutive periods before switch.
Transitions logged to data/regime_transitions.jsonl.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


class Regime(str, Enum):
    TREND_STRONG = "TREND_STRONG"
    MEAN_REVERT = "MEAN_REVERT"
    HIGH_VOL = "HIGH_VOL"
    PANIC = "PANIC"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    UNKNOWN = "UNKNOWN"


class AssetClass(str, Enum):
    FX = "FX"
    CRYPTO = "CRYPTO"
    US_EQUITY = "US_EQUITY"
    EU_EQUITY = "EU_EQUITY"
    FUTURES = "FUTURES"


# Default thresholds — overridable via config/regime.yaml
DEFAULT_THRESHOLDS = {
    "trend_adx_min": 25.0,
    "trend_vol_ratio_max": 1.5,
    "mr_adx_max": 20.0,
    "mr_vol_ratio_max": 1.2,
    "highvol_vol_ratio_min": 2.0,
    "highvol_corr_rising_min": 0.1,
    "panic_vol_ratio_min": 3.0,
    "panic_corr_min": 0.8,
    "panic_spread_zscore_min": 2.5,
    "lowliq_spread_zscore_min": 2.0,
    "lowliq_volume_ratio_max": 0.5,
    "hysteresis_periods": 2,
}


@dataclass
class RegimeInput:
    """Input metrics for regime detection on one asset class."""
    asset_class: str
    realized_vol_20d: float = 0.0
    realized_vol_5d: float = 0.0
    cross_corr: float = 0.0            # avg cross-asset correlation
    cross_corr_delta_5d: float = 0.0   # change in corr over 5d
    spread_zscore: float = 0.0
    trend_strength: float = 0.0        # ADX or equivalent
    volume_ratio: float = 1.0          # current vol / 20d avg vol

    @property
    def vol_ratio(self) -> float:
        if self.realized_vol_20d > 1e-9:
            return self.realized_vol_5d / self.realized_vol_20d
        return 1.0


@dataclass
class RegimeResult:
    """Output of regime detection for one asset class."""
    asset_class: str
    regime: str
    confidence: float
    metrics: dict
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class MultiAssetRegimeDetector:
    """Deterministic multi-asset regime detector with hysteresis.

    Usage::

        detector = MultiAssetRegimeDetector()
        results = detector.detect_all({
            "FX": RegimeInput(asset_class="FX", ...),
            "CRYPTO": RegimeInput(asset_class="CRYPTO", ...),
        })
        global_regime = detector.get_global_regime()
    """

    TRANSITIONS_FILE = ROOT / "data" / "regime_transitions.jsonl"

    def __init__(self, thresholds: dict | None = None):
        self._thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._hysteresis_n = self._thresholds["hysteresis_periods"]

        # Current confirmed regime per asset class
        self._current: dict[str, Regime] = {}
        # Candidate regime (not yet confirmed due to hysteresis)
        self._candidate: dict[str, tuple[Regime, int]] = {}
        # History for dashboard/API
        self._history: list[dict] = []

    def detect(self, inp: RegimeInput) -> RegimeResult:
        """Detect regime for a single asset class."""
        raw_regime, confidence, metrics = self._classify(inp)

        ac = inp.asset_class
        confirmed = self._apply_hysteresis(ac, raw_regime)

        # Log transition if regime changed
        prev = self._current.get(ac, Regime.UNKNOWN)
        if confirmed != prev:
            self._log_transition(ac, prev, confirmed, metrics)
            self._current[ac] = confirmed

        result = RegimeResult(
            asset_class=ac,
            regime=confirmed.value,
            confidence=round(confidence, 3),
            metrics=metrics,
        )
        self._history.append(asdict(result))
        if len(self._history) > 5000:
            self._history = self._history[-2500:]

        return result

    def detect_all(
        self, inputs: dict[str, RegimeInput]
    ) -> dict[str, RegimeResult]:
        """Detect regime for all provided asset classes."""
        results = {}
        for ac, inp in inputs.items():
            results[ac] = self.detect(inp)
        return results

    def get_global_regime(self) -> Regime:
        """Global regime = worst across all asset classes.

        Priority: PANIC > HIGH_VOL > LOW_LIQUIDITY > UNKNOWN > MEAN_REVERT > TREND_STRONG
        """
        if not self._current:
            return Regime.UNKNOWN

        severity = {
            Regime.PANIC: 5,
            Regime.HIGH_VOL: 4,
            Regime.LOW_LIQUIDITY: 3,
            Regime.UNKNOWN: 2,
            Regime.MEAN_REVERT: 1,
            Regime.TREND_STRONG: 0,
        }
        worst = max(self._current.values(), key=lambda r: severity.get(r, 2))
        return worst

    def get_regime(self, asset_class: str) -> Regime:
        """Get current confirmed regime for an asset class."""
        return self._current.get(asset_class, Regime.UNKNOWN)

    def get_all_regimes(self) -> dict[str, str]:
        """All current regimes as {asset_class: regime_str}."""
        return {ac: r.value for ac, r in self._current.items()}

    def get_snapshot(self) -> dict:
        """Full snapshot for API/dashboard."""
        return {
            "regimes": self.get_all_regimes(),
            "global": self.get_global_regime().value,
            "transitions_24h": self._count_recent_transitions(24),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # Classification rules
    # ------------------------------------------------------------------

    def _classify(self, inp: RegimeInput) -> tuple[Regime, float, dict]:
        """Apply deterministic rules to classify regime."""
        t = self._thresholds
        vr = inp.vol_ratio
        adx = inp.trend_strength
        corr = inp.cross_corr
        corr_delta = inp.cross_corr_delta_5d
        spread_z = inp.spread_zscore
        vol_rat = inp.volume_ratio

        metrics = {
            "vol_ratio": round(vr, 3),
            "adx": round(adx, 2),
            "cross_corr": round(corr, 3),
            "corr_delta_5d": round(corr_delta, 3),
            "spread_zscore": round(spread_z, 2),
            "volume_ratio": round(vol_rat, 2),
        }

        # PANIC — highest priority
        if (
            vr > t["panic_vol_ratio_min"]
            and corr > t["panic_corr_min"]
        ):
            conf = min(1.0, (vr - t["panic_vol_ratio_min"]) / 2.0 + 0.6)
            return Regime.PANIC, conf, metrics

        if spread_z > t["panic_spread_zscore_min"] and vr > t["highvol_vol_ratio_min"]:
            conf = min(1.0, (spread_z - t["panic_spread_zscore_min"]) / 2.0 + 0.5)
            return Regime.PANIC, conf, metrics

        # LOW_LIQUIDITY
        if (
            spread_z > t["lowliq_spread_zscore_min"]
            and vol_rat < t["lowliq_volume_ratio_max"]
        ):
            conf = min(1.0, (spread_z - t["lowliq_spread_zscore_min"]) / 2.0 + 0.5)
            return Regime.LOW_LIQUIDITY, conf, metrics

        # HIGH_VOL
        if vr > t["highvol_vol_ratio_min"]:
            conf = min(1.0, (vr - t["highvol_vol_ratio_min"]) / 2.0 + 0.5)
            return Regime.HIGH_VOL, conf, metrics

        # TREND_STRONG
        if adx > t["trend_adx_min"] and vr < t["trend_vol_ratio_max"]:
            conf = min(1.0, (adx - t["trend_adx_min"]) / 20.0 + 0.5)
            return Regime.TREND_STRONG, conf, metrics

        # MEAN_REVERT
        if adx < t["mr_adx_max"] and vr < t["mr_vol_ratio_max"]:
            conf = min(1.0, (t["mr_adx_max"] - adx) / t["mr_adx_max"] + 0.3)
            return Regime.MEAN_REVERT, conf, metrics

        return Regime.UNKNOWN, 0.3, metrics

    # ------------------------------------------------------------------
    # Hysteresis
    # ------------------------------------------------------------------

    def _apply_hysteresis(self, ac: str, raw: Regime) -> Regime:
        """Require N consecutive same-regime readings before switching."""
        current = self._current.get(ac, Regime.UNKNOWN)

        if raw == current:
            # Already confirmed — reset candidate
            self._candidate.pop(ac, None)
            return current

        # Different from current — track as candidate
        candidate, count = self._candidate.get(ac, (raw, 0))

        if candidate == raw:
            count += 1
        else:
            # New candidate, reset
            candidate = raw
            count = 1

        self._candidate[ac] = (candidate, count)

        # Exception: PANIC bypasses hysteresis (safety first)
        if raw == Regime.PANIC:
            self._candidate.pop(ac, None)
            return Regime.PANIC

        if count >= self._hysteresis_n:
            self._candidate.pop(ac, None)
            return raw

        return current

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_transition(
        self, ac: str, old: Regime, new: Regime, metrics: dict
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "asset_class": ac,
            "old_regime": old.value,
            "new_regime": new.value,
            "trigger_metrics": metrics,
        }
        logger.warning(
            "REGIME TRANSITION %s: %s -> %s (metrics=%s)",
            ac, old.value, new.value, metrics,
        )
        try:
            log_path = self.TRANSITIONS_FILE
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to log regime transition: %s", e)

    def _count_recent_transitions(self, hours: int = 24) -> int:
        """Count transitions in last N hours from history."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        cutoff_str = cutoff.isoformat()
        count = 0
        for entry in reversed(self._history):
            ts = entry.get("timestamp", "")
            if ts < cutoff_str:
                break
            count += 1
        return count
