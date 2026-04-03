"""
D5-02 — Backtest Fidelity Score.

Compares backtest performance vs live/paper performance per strategy.

Score = weighted average of metric ratios (target = 1.0):
  > 0.8  : FIDELE — go-live autorise
  0.5-0.8: DEGRADE — go-live avec sizing reduit
  < 0.5  : ECHEC — ne PAS go-live, investiguer

Requires 30+ days of shadow trade data to produce a meaningful score.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = ROOT / "data" / "validation" / "fidelity_report.json"

# Backtest reference metrics per strategy (from WF validation)
BACKTEST_REFERENCE = {
    "fx_carry_vol_scaled": {"sharpe": 3.04, "win_rate": 0.65, "avg_trade_pct": 0.15, "max_dd_pct": -3.2, "trades_per_month": 22},
    "fx_carry_momentum": {"sharpe": 2.17, "win_rate": 0.62, "avg_trade_pct": 0.12, "max_dd_pct": -4.1, "trades_per_month": 22},
    "crypto_dual_momentum": {"sharpe": 1.20, "win_rate": 0.52, "avg_trade_pct": 0.35, "max_dd_pct": -8.0, "trades_per_month": 15},
    "crypto_vol_breakout": {"sharpe": 1.50, "win_rate": 0.48, "avg_trade_pct": 0.50, "max_dd_pct": -6.5, "trades_per_month": 8},
    "crypto_range_bb": {"sharpe": 1.35, "win_rate": 0.74, "avg_trade_pct": 0.18, "max_dd_pct": -4.0, "trades_per_month": 20},
}

METRIC_WEIGHTS = {
    "sharpe": 0.30,
    "win_rate": 0.20,
    "avg_trade_pct": 0.15,
    "max_dd_pct": 0.20,
    "trades_per_month": 0.15,
}


@dataclass
class FidelityResult:
    strategy: str
    score: float
    status: str             # FIDELE / DEGRADE / ECHEC
    metrics_comparison: dict
    recommendation: str
    min_go_live_sizing: float   # 1.0 if FIDELE, 0.5 if DEGRADE, 0.0 if ECHEC


class FidelityScorer:
    """Computes backtest-to-live fidelity score.

    Usage::

        scorer = FidelityScorer()
        result = scorer.score(
            strategy="fx_carry_vol_scaled",
            live_sharpe=2.50,
            live_win_rate=0.61,
            live_avg_trade_pct=0.13,
            live_max_dd_pct=-3.8,
            live_trades_per_month=20,
        )
    """

    def score(
        self,
        strategy: str,
        live_sharpe: float,
        live_win_rate: float,
        live_avg_trade_pct: float,
        live_max_dd_pct: float,
        live_trades_per_month: float,
    ) -> FidelityResult:
        """Compute fidelity score for a strategy."""
        ref = BACKTEST_REFERENCE.get(strategy)
        if not ref:
            return FidelityResult(
                strategy=strategy,
                score=0.0,
                status="UNKNOWN",
                metrics_comparison={},
                recommendation=f"No backtest reference for {strategy}",
                min_go_live_sizing=0.0,
            )

        live_metrics = {
            "sharpe": live_sharpe,
            "win_rate": live_win_rate,
            "avg_trade_pct": live_avg_trade_pct,
            "max_dd_pct": live_max_dd_pct,
            "trades_per_month": live_trades_per_month,
        }

        comparison = {}
        weighted_score = 0.0

        for metric, weight in METRIC_WEIGHTS.items():
            ref_val = ref[metric]
            live_val = live_metrics[metric]

            # For DD, closer to 0 is better (less negative)
            if metric == "max_dd_pct":
                if ref_val != 0:
                    ratio = min(ref_val / live_val, 1.5) if live_val != 0 else 1.0
                else:
                    ratio = 1.0
            else:
                if ref_val != 0:
                    ratio = min(live_val / ref_val, 1.5)
                else:
                    ratio = 1.0

            ratio = max(0, ratio)
            comparison[metric] = {
                "backtest": ref_val,
                "live": round(live_val, 4),
                "ratio": round(ratio, 3),
            }
            weighted_score += weight * ratio

        score = round(min(weighted_score, 1.5), 3)

        if score >= 0.8:
            status = "FIDELE"
            rec = "Go-live autorise, sizing nominal"
            sizing = 1.0
        elif score >= 0.5:
            status = "DEGRADE"
            rec = "Go-live avec sizing reduit 50%"
            sizing = 0.5
        else:
            status = "ECHEC"
            rec = "Ne PAS go-live. Investiguer le gap backtest→live"
            sizing = 0.0

        result = FidelityResult(
            strategy=strategy,
            score=score,
            status=status,
            metrics_comparison=comparison,
            recommendation=rec,
            min_go_live_sizing=sizing,
        )

        return result

    def score_all(self, live_metrics_by_strategy: dict) -> dict:
        """Score all strategies at once.

        Args:
            live_metrics_by_strategy: {strategy_id: {sharpe, win_rate, ...}}

        Returns:
            {strategy_id: FidelityResult.to_dict()}
        """
        results = {}
        for strat, metrics in live_metrics_by_strategy.items():
            r = self.score(
                strategy=strat,
                live_sharpe=metrics.get("sharpe", 0),
                live_win_rate=metrics.get("win_rate", 0),
                live_avg_trade_pct=metrics.get("avg_trade_pct", 0),
                live_max_dd_pct=metrics.get("max_dd_pct", 0),
                live_trades_per_month=metrics.get("trades_per_month", 0),
            )
            results[strat] = asdict(r)

        # Save
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "timestamp": datetime.now(UTC).isoformat(),
                "strategies": results,
            }
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save fidelity report: %s", e)

        return results
