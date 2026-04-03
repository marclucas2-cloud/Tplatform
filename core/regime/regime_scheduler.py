"""
D1-03 — Regime Scheduler: integrates regime detection into the worker cycle.

Every 15 minutes:
  1. Compute regime per asset class (FX, Crypto, US_EQUITY, EU_EQUITY, FUTURES)
  2. Global regime = worst across classes
  3. Publish regime change to Telegram
  4. Expose current regimes via get_current_regimes()

Strategy runners call get_activation_multiplier(strategy_id) before each signal.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import yaml

from .activation_matrix import ActivationMatrix
from .multi_asset_regime import (
    MultiAssetRegimeDetector,
    Regime,
    RegimeInput,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

# Minimum exposure floor: never go below this multiplier on ALL strategies
# Prevents full deleveraging that causes missed rebounds
MIN_EXPOSURE_FLOOR = 0.20

# After PANIC ends, ramp back up gradually (not instant full exposure)
# This prevents whipsaw: PANIC -> full exposure -> PANIC again
REENTRY_RAMP_PERIODS = 4  # 4 cycles (= 1h at 15min) to go from floor to full


class RegimeScheduler:
    """Manages regime detection cycle and strategy activation lookups.

    Usage in worker.py::

        from core.regime.regime_scheduler import RegimeScheduler
        regime_scheduler = RegimeScheduler(alert_callback=_send_alert)
        # In main loop every 15 min:
        regime_scheduler.run_cycle()
        # Before each strategy signal:
        mult = regime_scheduler.get_activation_multiplier("fx_carry_vol_scaled")
    """

    def __init__(
        self,
        alert_callback: Callable | None = None,
        config_path: Path | None = None,
    ):
        cfg_path = config_path or ROOT / "config" / "regime.yaml"
        thresholds = {}
        try:
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                thresholds = cfg.get("thresholds", {})
        except Exception as e:
            logger.error("Error loading regime config: %s", e)

        self._detector = MultiAssetRegimeDetector(thresholds=thresholds)
        self._matrix = ActivationMatrix(config_path=cfg_path)
        self._alert = alert_callback
        self._last_global: Regime | None = None
        self._cycle_count = 0
        # Re-entry ramp: tracks how many cycles since leaving PANIC
        self._cycles_since_panic: int = 999  # start high = no ramp needed
        self._was_panic: bool = False

    def run_cycle(
        self,
        fx_metrics: dict | None = None,
        crypto_metrics: dict | None = None,
        us_metrics: dict | None = None,
        eu_metrics: dict | None = None,
        futures_metrics: dict | None = None,
    ) -> dict:
        """Run regime detection for all asset classes.

        Each *_metrics dict should contain keys matching RegimeInput fields:
            realized_vol_20d, realized_vol_5d, cross_corr, cross_corr_delta_5d,
            spread_zscore, trend_strength, volume_ratio

        Missing asset classes use defaults (UNKNOWN regime).

        Returns:
            Snapshot dict with all regimes and global.
        """
        inputs = {}

        for ac_name, raw in [
            ("FX", fx_metrics),
            ("CRYPTO", crypto_metrics),
            ("US_EQUITY", us_metrics),
            ("EU_EQUITY", eu_metrics),
            ("FUTURES", futures_metrics),
        ]:
            if raw:
                inputs[ac_name] = RegimeInput(
                    asset_class=ac_name,
                    realized_vol_20d=raw.get("realized_vol_20d", 0),
                    realized_vol_5d=raw.get("realized_vol_5d", 0),
                    cross_corr=raw.get("cross_corr", 0),
                    cross_corr_delta_5d=raw.get("cross_corr_delta_5d", 0),
                    spread_zscore=raw.get("spread_zscore", 0),
                    trend_strength=raw.get("trend_strength", 0),
                    volume_ratio=raw.get("volume_ratio", 1.0),
                )

        results = self._detector.detect_all(inputs)
        global_regime = self._detector.get_global_regime()

        # Alert on global regime change
        if global_regime != self._last_global and self._last_global is not None:
            msg = (
                f"REGIME GLOBAL: {self._last_global.value} -> {global_regime.value}\n"
                f"Detail: {self._detector.get_all_regimes()}"
            )
            logger.warning(msg)
            if self._alert:
                level = "critical" if global_regime == Regime.PANIC else "warning"
                self._alert(msg, level=level)

        # Track PANIC → non-PANIC transitions for re-entry ramp
        if global_regime == Regime.PANIC:
            self._was_panic = True
            self._cycles_since_panic = 0
        elif self._was_panic:
            self._cycles_since_panic += 1
            if self._cycles_since_panic >= REENTRY_RAMP_PERIODS:
                self._was_panic = False  # Ramp complete

        self._last_global = global_regime
        self._cycle_count += 1

        snapshot = self._detector.get_snapshot()
        snapshot["cycle_count"] = self._cycle_count
        snapshot["cycles_since_panic"] = self._cycles_since_panic
        snapshot["reentry_ramp_active"] = self._was_panic and self._cycles_since_panic > 0
        return snapshot

    def get_activation_multiplier(self, strategy_id: str) -> float:
        """Get sizing multiplier for a strategy based on current regime.

        Applies 3 layers:
          1. Base multiplier from activation matrix
          2. Minimum exposure floor (never 0 on everything)
          3. Re-entry ramp after PANIC (gradual, not instant)

        Returns:
            Float 0.0-1.0. Strategies should multiply their base sizing by this.
        """
        ac = self._matrix.get_asset_class(strategy_id)
        regime = self._detector.get_regime(ac)
        base_mult = self._matrix.get_multiplier(strategy_id, regime, ac)

        # Layer 2: minimum exposure floor
        # Even in PANIC, at least MIN_EXPOSURE_FLOOR stays active
        # Exception: strategies explicitly designed for PANIC (mult=1.0 in PANIC)
        # keep their full allocation
        if base_mult < MIN_EXPOSURE_FLOOR and regime != Regime.PANIC:
            base_mult = MIN_EXPOSURE_FLOOR
        elif base_mult == 0.0 and regime == Regime.PANIC:
            # In PANIC, strategies with 0.0 get the floor (minimum participation)
            base_mult = MIN_EXPOSURE_FLOOR

        # Layer 3: re-entry ramp after PANIC
        # Don't jump from floor to full exposure instantly
        if self._was_panic and self._cycles_since_panic > 0 and regime != Regime.PANIC:
            ramp_progress = min(1.0, self._cycles_since_panic / REENTRY_RAMP_PERIODS)
            # Interpolate: floor → base_mult over REENTRY_RAMP_PERIODS
            base_mult = MIN_EXPOSURE_FLOOR + (base_mult - MIN_EXPOSURE_FLOOR) * ramp_progress

        return round(base_mult, 3)

    def get_regime(self, asset_class: str) -> str:
        """Get current regime for an asset class."""
        return self._detector.get_regime(asset_class).value

    def get_global_regime(self) -> str:
        """Get global regime (worst across all)."""
        return self._detector.get_global_regime().value

    def get_snapshot(self) -> dict:
        """Full snapshot for API/dashboard/Telegram."""
        return self._detector.get_snapshot()

    def set_manual_override(self, regime_str: str | None) -> None:
        """Set manual regime override from Telegram."""
        self._matrix.set_manual_override(regime_str)

    @property
    def detector(self) -> MultiAssetRegimeDetector:
        return self._detector

    @property
    def matrix(self) -> ActivationMatrix:
        return self._matrix
