"""
D4-02 — Cross-Asset Correlation Monitor.

Real-time correlation between asset classes:
  BTC / SPY, BTC / EURUSD, SPY / EURUSD, SPY / DAX, BTC / Gold

Alerts:
  - Rolling 5d corr > 0.7 between unrelated pairs → "Correlation regime shift"
  - All corr > 0.5 simultaneously → "Risk-on/off regime, diversification reduced"
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = ROOT / "data" / "risk" / "cross_asset_corr.json"

# Pairs to monitor
MONITORED_PAIRS = [
    ("BTC", "SPY"),
    ("BTC", "EURUSD"),
    ("SPY", "EURUSD"),
    ("SPY", "DAX"),
    ("BTC", "GOLD"),
]


class CrossAssetCorrelationMonitor:
    """Monitors correlations between asset classes.

    Usage::

        monitor = CrossAssetCorrelationMonitor()
        report = monitor.update({
            "BTC": [0.01, -0.02, 0.03, ...],   # daily returns
            "SPY": [0.005, -0.01, 0.008, ...],
            ...
        })
    """

    def __init__(
        self,
        high_corr_threshold: float = 0.7,
        all_corr_threshold: float = 0.5,
        lookback_days: int = 5,
    ):
        self._high_threshold = high_corr_threshold
        self._all_threshold = all_corr_threshold
        self._lookback = lookback_days
        self._last_report: dict | None = None

    def update(self, returns_by_asset: dict[str, list]) -> dict:
        """Compute cross-asset correlations and check for alerts.

        Args:
            returns_by_asset: {asset_name: [daily_return, ...]}
                At least `lookback_days` observations needed.

        Returns:
            Report dict with correlations, alerts, and diversification score.
        """
        correlations = {}
        alerts = []

        for asset_a, asset_b in MONITORED_PAIRS:
            ret_a = returns_by_asset.get(asset_a, [])
            ret_b = returns_by_asset.get(asset_b, [])

            if len(ret_a) < self._lookback or len(ret_b) < self._lookback:
                correlations[f"{asset_a}/{asset_b}"] = None
                continue

            # Use last N days
            a = np.array(ret_a[-self._lookback:])
            b = np.array(ret_b[-self._lookback:])

            if np.std(a) < 1e-12 or np.std(b) < 1e-12:
                correlations[f"{asset_a}/{asset_b}"] = 0.0
                continue

            corr = float(np.corrcoef(a, b)[0, 1])
            correlations[f"{asset_a}/{asset_b}"] = round(corr, 3)

            # Alert on high correlation
            if abs(corr) > self._high_threshold:
                alerts.append({
                    "type": "HIGH_CORRELATION",
                    "pair": f"{asset_a}/{asset_b}",
                    "correlation": round(corr, 3),
                    "message": f"Correlation regime shift: {asset_a}/{asset_b} = {corr:.2f}",
                })

        # Check if ALL pairs above threshold (risk-on/off)
        valid_corrs = [v for v in correlations.values() if v is not None]
        if valid_corrs and all(abs(c) > self._all_threshold for c in valid_corrs):
            alerts.append({
                "type": "ALL_CORRELATED",
                "avg_corr": round(np.mean(np.abs(valid_corrs)), 3),
                "message": "Risk-on/off regime detected — diversification reduced",
            })

        # Diversification score: lower avg corr = better
        avg_abs_corr = float(np.mean(np.abs(valid_corrs))) if valid_corrs else 0
        div_score = max(0, round((1 - avg_abs_corr) * 100, 1))

        report = {
            "timestamp": datetime.now(UTC).isoformat(),
            "correlations": correlations,
            "alerts": alerts,
            "diversification_score": div_score,
            "avg_abs_correlation": round(avg_abs_corr, 3),
        }

        self._last_report = report
        self._save(report)
        return report

    def get_hrp_penalty(self) -> float:
        """HRP weight penalty multiplier based on correlation regime.

        Returns 1.0 if normal, < 1.0 if high correlation (reduce leverage).
        """
        if not self._last_report:
            return 1.0
        avg = self._last_report.get("avg_abs_correlation", 0)
        if avg > 0.7:
            return 0.5
        if avg > 0.5:
            return 0.7
        return 1.0

    def _save(self, report: dict) -> None:
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save cross-asset corr: %s", e)
