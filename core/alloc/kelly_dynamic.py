"""
Dynamic Kelly Manager -- equity curve momentum-based Kelly switching.

Adjusts the Kelly fraction based on the relationship between current equity
and its moving average, implementing a gear system:

  AGGRESSIVE (1/4 Kelly):  equity > SMA20 + 0.5 * StdDev
  NOMINAL    (1/8 Kelly):  equity between SMA20 +/- 0.5 * StdDev
  DEFENSIVE  (1/32 Kelly): equity < SMA20 - 0.5 * StdDev
  STOPPED    (0 Kelly):    equity < peak - 10% (hard floor)

The idea: accelerate when the equity curve is trending up (the system is
"in sync" with the market), brake before it crashes (equity curve breaking
down below its average signals that the strategies are struggling).

Hysteresis prevents whipsaw: the equity must cross the boundary plus an
extra band (hysteresis_pct) to trigger a mode change.

Usage:
    from core.alloc.kelly_dynamic import DynamicKellyManager
    kelly_mgr = DynamicKellyManager(sma_lookback=20)
    kelly_mgr.update_equity(datetime.now(), 25_300.0)
    mode = kelly_mgr.get_kelly_mode()
    # mode = {"mode": "NOMINAL", "fraction": 0.125, "equity_vs_sma": 0.002}
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# Kelly fractions for each mode — ROC optim Phase 1 (doubled from original)
KELLY_FRACTIONS = {
    "AGGRESSIVE": 0.50,    # 1/2 Kelly
    "NOMINAL": 0.25,       # 1/4 Kelly
    "DEFENSIVE": 0.0625,   # 1/16 Kelly
    "STOPPED": 0.0,        # No trading
}

# Position multipliers for each mode
POSITION_MULTIPLIERS = {
    "AGGRESSIVE": 2.0,
    "NOMINAL": 1.0,
    "DEFENSIVE": 0.25,
    "STOPPED": 0.0,
}


def vol_kelly_multiplier(vix: float = None, btc_vol_30d: float = None) -> float:
    """Adjust Kelly fraction by volatility regime.

    Low vol -> more aggressive (calmer markets = better Sharpe realization).
    High vol -> more defensive (but never 0).

    Args:
        vix: Current VIX level (for equities/FX).
        btc_vol_30d: BTC 30d annualized vol (for crypto).

    Returns:
        Multiplier (0.4 to 1.5) to apply on Kelly fraction.
    """
    # Crypto: use BTC vol
    if btc_vol_30d is not None:
        if btc_vol_30d < 0.40:
            return 1.5   # Calm crypto market — rare, aggressive
        elif btc_vol_30d < 0.60:
            return 1.0   # Normal
        elif btc_vol_30d < 0.80:
            return 0.7   # High vol
        else:
            return 0.4   # Extreme vol

    # Equities/FX: use VIX
    if vix is not None:
        if vix < 15:
            return 1.5   # Low vol regime
        elif vix < 20:
            return 1.0   # Normal
        elif vix < 30:
            return 0.7   # Elevated
        else:
            return 0.4   # Stress

    return 1.0  # Default: no adjustment

# Hard floor: if equity drops more than this from peak, stop trading
HARD_FLOOR_DRAWDOWN = 0.10  # 10%

# StdDev multiplier for band boundaries
BAND_MULTIPLIER = 0.5


class DynamicKellyManager:
    """
    Adjusts Kelly fraction based on equity curve momentum.
    Accelerate when winning, brake before crash.
    """

    def __init__(
        self,
        sma_lookback: int = 20,
        hysteresis_pct: float = 0.02,
    ):
        """
        Args:
            sma_lookback: Number of equity snapshots for the SMA (default 20).
            hysteresis_pct: Extra band width to prevent whipsaw (default 2%).
        """
        if sma_lookback < 2:
            raise ValueError(f"sma_lookback must be >= 2, got {sma_lookback}")
        if hysteresis_pct < 0:
            raise ValueError(f"hysteresis_pct must be >= 0, got {hysteresis_pct}")

        self.sma_lookback = sma_lookback
        self.hysteresis_pct = hysteresis_pct

        # Equity history: (timestamp, equity) pairs
        self._equity_history: Deque[Tuple[datetime, float]] = deque(
            maxlen=max(sma_lookback * 3, 100)
        )

        self._peak_equity: float = 0.0
        self._current_mode: str = "NOMINAL"
        self._stopped_manually: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_equity(self, timestamp: datetime, equity: float) -> None:
        """Record equity snapshot (called every hour or at session close).

        Args:
            timestamp: When the snapshot was taken.
            equity: Current total portfolio equity in dollars.
        """
        if equity < 0:
            logger.warning("Negative equity received: %.2f, ignoring", equity)
            return

        self._equity_history.append((timestamp, equity))

        # Track peak
        if equity > self._peak_equity:
            self._peak_equity = equity

        logger.debug(
            "Equity update: %.2f (peak=%.2f, history=%d)",
            equity, self._peak_equity, len(self._equity_history),
        )

    def get_kelly_mode(self) -> dict:
        """Determine the current Kelly mode based on equity momentum.

        Returns:
            {
                "mode": "AGGRESSIVE"|"NOMINAL"|"DEFENSIVE"|"STOPPED",
                "fraction": float,
                "equity_vs_sma": float,  # (equity - sma) / sma
            }
        """
        # Not enough data: stay NOMINAL
        if len(self._equity_history) < 2:
            return {
                "mode": self._current_mode,
                "fraction": KELLY_FRACTIONS[self._current_mode],
                "equity_vs_sma": 0.0,
            }

        # If manually stopped, stay stopped
        if self._stopped_manually:
            self._current_mode = "STOPPED"
            return {
                "mode": "STOPPED",
                "fraction": 0.0,
                "equity_vs_sma": 0.0,
            }

        equities = np.array([eq for _, eq in self._equity_history])
        current_equity = equities[-1]

        # Hard floor check: drawdown from peak
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - current_equity) / self._peak_equity
            if drawdown >= HARD_FLOOR_DRAWDOWN:
                new_mode = "STOPPED"
                self._current_mode = new_mode
                logger.warning(
                    "HARD FLOOR breached: equity=%.2f, peak=%.2f, drawdown=%.2f%%",
                    current_equity, self._peak_equity, drawdown * 100,
                )
                return {
                    "mode": "STOPPED",
                    "fraction": 0.0,
                    "equity_vs_sma": -drawdown,
                }

        # Compute SMA and StdDev
        lookback_equities = equities[-self.sma_lookback:]
        sma = float(np.mean(lookback_equities))
        std = float(np.std(lookback_equities, ddof=1)) if len(lookback_equities) > 1 else 0.0

        # Equity vs SMA ratio
        equity_vs_sma = (current_equity - sma) / sma if sma > 0 else 0.0

        # Determine raw mode from bands
        upper_band = sma + BAND_MULTIPLIER * std
        lower_band = sma - BAND_MULTIPLIER * std

        if current_equity > upper_band:
            raw_mode = "AGGRESSIVE"
        elif current_equity < lower_band:
            raw_mode = "DEFENSIVE"
        else:
            raw_mode = "NOMINAL"

        # Apply hysteresis
        new_mode = self._check_hysteresis(raw_mode, self._current_mode)
        self._current_mode = new_mode

        base_fraction = KELLY_FRACTIONS[new_mode]

        return {
            "mode": new_mode,
            "fraction": base_fraction,
            "equity_vs_sma": round(equity_vs_sma, 6),
        }

    def get_position_multiplier(self) -> float:
        """Returns multiplier (0.0 to 1.0) for position sizing.

        AGGRESSIVE: 1.0, NOMINAL: 0.5, DEFENSIVE: 0.125, STOPPED: 0.0
        """
        mode_info = self.get_kelly_mode()
        return POSITION_MULTIPLIERS[mode_info["mode"]]

    def get_equity_stats(self) -> dict:
        """Full equity statistics for dashboard/monitoring.

        Returns:
            {
                "current": float,
                "sma20": float,
                "std20": float,
                "peak": float,
                "drawdown_pct": float,
                "mode": str,
                "fraction": float,
                "n_snapshots": int,
            }
        """
        if not self._equity_history:
            return {
                "current": 0.0,
                "sma20": 0.0,
                "std20": 0.0,
                "peak": 0.0,
                "drawdown_pct": 0.0,
                "mode": self._current_mode,
                "fraction": KELLY_FRACTIONS[self._current_mode],
                "n_snapshots": 0,
            }

        equities = np.array([eq for _, eq in self._equity_history])
        current = float(equities[-1])

        lookback = equities[-self.sma_lookback:]
        sma = float(np.mean(lookback))
        std = float(np.std(lookback, ddof=1)) if len(lookback) > 1 else 0.0

        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - current) / self._peak_equity

        return {
            "current": round(current, 2),
            "sma20": round(sma, 2),
            "std20": round(std, 2),
            "peak": round(self._peak_equity, 2),
            "drawdown_pct": round(drawdown_pct, 6),
            "mode": self._current_mode,
            "fraction": KELLY_FRACTIONS[self._current_mode],
            "n_snapshots": len(self._equity_history),
        }

    def reset_stopped(self, new_peak: Optional[float] = None) -> None:
        """Manual reset from STOPPED mode.

        Must be called explicitly by the operator after reviewing the situation.
        Optionally resets the peak equity to a new value.

        Args:
            new_peak: If provided, resets peak equity to this value.
                      If None, keeps current peak.
        """
        self._stopped_manually = False
        self._current_mode = "NOMINAL"

        if new_peak is not None and new_peak > 0:
            self._peak_equity = new_peak

        logger.info(
            "Manual reset from STOPPED -> NOMINAL (peak=%.2f)",
            self._peak_equity,
        )

    def force_stop(self) -> None:
        """Manually force STOPPED mode (emergency kill switch)."""
        self._stopped_manually = True
        self._current_mode = "STOPPED"
        logger.warning("Manual STOP activated")

    # ------------------------------------------------------------------
    # Hysteresis
    # ------------------------------------------------------------------

    def _check_hysteresis(self, new_mode: str, current_mode: str) -> str:
        """Prevent whipsaw: require crossing hysteresis band to change mode.

        If equity is oscillating near a boundary, stay in current mode.
        The new mode only takes effect if the equity has moved past the
        boundary by at least hysteresis_pct relative to the SMA.

        Args:
            new_mode: Mode suggested by raw band check.
            current_mode: Currently active mode.

        Returns:
            Final mode after hysteresis filter.
        """
        if new_mode == current_mode:
            return current_mode

        # If we're STOPPED, only manual reset can change the mode
        if current_mode == "STOPPED":
            return "STOPPED"

        # Allow transitions that move toward more caution without hysteresis
        # (AGGRESSIVE -> NOMINAL, NOMINAL -> DEFENSIVE, etc.)
        caution_order = {"AGGRESSIVE": 0, "NOMINAL": 1, "DEFENSIVE": 2, "STOPPED": 3}
        if caution_order.get(new_mode, 0) > caution_order.get(current_mode, 0):
            # Moving to more cautious mode: allow immediately
            logger.info(
                "Kelly mode transition: %s -> %s (more cautious, no hysteresis)",
                current_mode, new_mode,
            )
            return new_mode

        # Moving to more aggressive mode: require hysteresis
        if len(self._equity_history) < 2:
            return current_mode

        equities = np.array([eq for _, eq in self._equity_history])
        lookback = equities[-self.sma_lookback:]
        sma = float(np.mean(lookback))

        if sma <= 0:
            return current_mode

        current_equity = float(equities[-1])
        deviation = (current_equity - sma) / sma

        # For upgrade (less cautious), need to exceed band + hysteresis
        if new_mode == "AGGRESSIVE" and current_mode == "NOMINAL":
            std = float(np.std(lookback, ddof=1)) if len(lookback) > 1 else 0.0
            upper_threshold = (BAND_MULTIPLIER * std / sma) + self.hysteresis_pct if sma > 0 else self.hysteresis_pct
            if deviation > upper_threshold:
                logger.info(
                    "Kelly mode upgrade: NOMINAL -> AGGRESSIVE "
                    "(deviation=%.4f > threshold=%.4f)",
                    deviation, upper_threshold,
                )
                return "AGGRESSIVE"
            return current_mode

        if new_mode == "NOMINAL" and current_mode == "DEFENSIVE":
            std = float(np.std(lookback, ddof=1)) if len(lookback) > 1 else 0.0
            lower_threshold = -(BAND_MULTIPLIER * std / sma) + self.hysteresis_pct if sma > 0 else self.hysteresis_pct
            if deviation > lower_threshold:
                logger.info(
                    "Kelly mode upgrade: DEFENSIVE -> NOMINAL "
                    "(deviation=%.4f > threshold=%.4f)",
                    deviation, lower_threshold,
                )
                return "NOMINAL"
            return current_mode

        # Default: accept transition
        logger.info("Kelly mode transition: %s -> %s", current_mode, new_mode)
        return new_mode
