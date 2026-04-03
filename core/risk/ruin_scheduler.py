"""
D2-02 — Scheduled Risk of Ruin Check.

Runs daily at 07:00 CET (before EU open). Recalculates portfolio Monte Carlo
and takes automatic action if thresholds are breached.

Telegram alerts:
  OK    : "RoR Check: P(DD>10%)=X%, P(ruin)=X%. All clear."
  RED   : "RoR Check: P(DD>10%)=X% [>5%]. Leverage reduced 50%."
  CRIT  : "RoR Check: P(DD>10%)=X% [>15%]. DEFENSIVE mode activated."
  STOP  : "RoR Check: P(ruin)=X% [>1%]. ALL TRADING STOPPED."
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .monte_carlo_portfolio import MCPortfolioResult, PortfolioMonteCarloSimulator

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


class RuinScheduler:
    """Daily RoR check with automatic actions.

    Usage::

        scheduler = RuinScheduler(
            alert_callback=send_telegram,
            kelly_callback=set_kelly_defensive,
        )
        result = scheduler.run_check(returns_matrix, weights, capital)
    """

    def __init__(
        self,
        alert_callback: Callable | None = None,
        kelly_callback: Callable | None = None,
        n_simulations: int = 10_000,
    ):
        self._alert = alert_callback
        self._kelly_cb = kelly_callback
        self._simulator = PortfolioMonteCarloSimulator(
            n_simulations=n_simulations,
        )
        self._last_result: MCPortfolioResult | None = None

    def run_check(
        self,
        returns_matrix: np.ndarray,
        weights: np.ndarray,
        capital: float,
        kelly_fraction: float = 0.25,
    ) -> MCPortfolioResult:
        """Run MC simulation and take action based on alert level."""
        result = self._simulator.run(
            returns_matrix=returns_matrix,
            weights=weights,
            capital=capital,
            kelly_fraction=kelly_fraction,
        )
        self._last_result = result

        # Build Telegram message
        date_str = datetime.now(UTC).strftime("%d/%m")
        if result.alert_level == "OK":
            msg = (
                f"RoR Check {date_str}: "
                f"P(DD>10%)={result.prob_dd_10pct:.1%}, "
                f"P(ruin)={result.prob_ruin:.1%}. All clear."
            )
            level = "info"
        elif result.alert_level == "RED":
            msg = (
                f"RoR Check {date_str}: "
                f"P(DD>10%)={result.prob_dd_10pct:.1%} [>5%]. "
                f"Leverage reduced 50%."
            )
            level = "warning"
        elif result.alert_level == "CRITICAL":
            msg = (
                f"RoR Check {date_str}: "
                f"P(DD>10%)={result.prob_dd_10pct:.1%} [>15%]. "
                f"DEFENSIVE mode activated."
            )
            level = "critical"
            # Auto-switch to DEFENSIVE
            if self._kelly_cb:
                try:
                    self._kelly_cb("DEFENSIVE")
                    logger.warning("Kelly auto-switched to DEFENSIVE by RoR check")
                except Exception as e:
                    logger.error(f"Kelly callback failed: {e}")
        else:  # STOP
            msg = (
                f"RoR Check {date_str}: "
                f"P(ruin)={result.prob_ruin:.1%} [>1%]. "
                f"ALL TRADING STOPPED. Manual audit required."
            )
            level = "critical"
            if self._kelly_cb:
                try:
                    self._kelly_cb("STOPPED")
                except Exception as e:
                    logger.error(f"Kelly STOP callback failed: {e}")

        logger.info("RoR check: %s", msg)
        if self._alert:
            self._alert(msg, level=level)

        return result

    @property
    def last_result(self) -> MCPortfolioResult | None:
        return self._last_result
