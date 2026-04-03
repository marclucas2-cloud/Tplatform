"""
D2-01 — Portfolio Monte Carlo Forward Simulator.

Simulates 10,000 trajectories of the full multi-strategy portfolio over
252 days (1 year), using correlated returns via Cholesky decomposition.

Key outputs:
  - P(DD > 5%) and P(DD > 10%) over 1 year
  - P(ruin) = P(equity < 50% of peak)
  - Expected max DD (median of worst DD per trajectory)
  - Return distribution at 1 year (p5, p25, p50, p75, p95)

Action thresholds:
  P(DD > 10%) > 5%  → ALERT RED → reduce leverage 50%
  P(DD > 10%) > 15% → ALERT CRITICAL → Kelly → DEFENSIVE
  P(ruin) > 1%      → STOP → manual audit required
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = ROOT / "data" / "risk" / "monte_carlo_report.json"


@dataclass
class MCPortfolioResult:
    """Result of portfolio Monte Carlo simulation."""
    n_simulations: int
    horizon_days: int
    capital: float
    # Drawdown probabilities
    prob_dd_5pct: float
    prob_dd_10pct: float
    prob_ruin: float            # equity < 50% of peak
    # Max DD distribution
    median_max_dd: float
    p95_max_dd: float
    # Return distribution at horizon
    return_p5: float
    return_p25: float
    return_p50: float
    return_p75: float
    return_p95: float
    # Final equity distribution
    equity_p5: float
    equity_p50: float
    equity_p95: float
    # Alert level
    alert_level: str            # OK / RED / CRITICAL / STOP
    alert_message: str
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PortfolioMonteCarloSimulator:
    """Simulates portfolio equity paths with correlated strategy returns.

    Usage::

        sim = PortfolioMonteCarloSimulator()
        result = sim.run(
            returns_matrix=returns_df.values,   # (T, N) daily returns
            weights=np.array([0.2, 0.15, ...]), # HRP weights
            capital=45_000,
        )
        print(result.prob_dd_10pct, result.alert_level)
    """

    def __init__(
        self,
        n_simulations: int = 10_000,
        horizon_days: int = 252,
        seed: int = 42,
    ):
        self._n_sims = n_simulations
        self._horizon = horizon_days
        self._seed = seed

    def run(
        self,
        returns_matrix: np.ndarray,
        weights: np.ndarray,
        capital: float = 45_000,
        kelly_fraction: float = 0.25,
    ) -> MCPortfolioResult:
        """Run portfolio Monte Carlo simulation.

        Args:
            returns_matrix: (T, N) array of daily strategy returns.
                T = number of historical observations, N = number of strategies.
            weights: (N,) array of portfolio weights (should sum to ~1.0).
            capital: Current portfolio capital.
            kelly_fraction: Current Kelly fraction (scales returns).

        Returns:
            MCPortfolioResult with probabilities and distribution.
        """
        rng = np.random.default_rng(self._seed)
        T, N = returns_matrix.shape

        if len(weights) != N:
            raise ValueError(
                f"returns_matrix has {N} strategies but weights has {len(weights)}"
            )

        # Estimate mean and covariance from historical returns
        mean_returns = np.mean(returns_matrix, axis=0)  # (N,)
        cov_matrix = np.cov(returns_matrix, rowvar=False)  # (N, N)

        # Ensure covariance is positive semi-definite
        try:
            L = np.linalg.cholesky(cov_matrix)
        except np.linalg.LinAlgError:
            # Add small regularization
            cov_matrix += np.eye(N) * 1e-8
            L = np.linalg.cholesky(cov_matrix)

        # Scale returns by Kelly fraction
        scaled_mean = mean_returns * kelly_fraction
        scaled_L = L * kelly_fraction

        # Simulate trajectories
        max_dds = np.zeros(self._n_sims)
        final_returns = np.zeros(self._n_sims)
        dd_5_count = 0
        dd_10_count = 0
        ruin_count = 0

        for i in range(self._n_sims):
            # Generate correlated daily returns: (horizon, N)
            z = rng.standard_normal((self._horizon, N))
            daily_returns = z @ scaled_L.T + scaled_mean  # (horizon, N)

            # Portfolio daily return = weighted sum
            port_returns = daily_returns @ weights  # (horizon,)

            # Build equity curve
            equity_curve = capital * np.cumprod(1 + port_returns)
            peak = np.maximum.accumulate(equity_curve)
            drawdowns = (equity_curve - peak) / peak

            max_dd = np.min(drawdowns)
            max_dds[i] = max_dd
            final_returns[i] = (equity_curve[-1] / capital) - 1.0

            if max_dd < -0.05:
                dd_5_count += 1
            if max_dd < -0.10:
                dd_10_count += 1
            if equity_curve[-1] < capital * 0.50:
                ruin_count += 1

        # Compute statistics
        prob_dd_5 = dd_5_count / self._n_sims
        prob_dd_10 = dd_10_count / self._n_sims
        prob_ruin = ruin_count / self._n_sims

        # Alert level
        if prob_ruin > 0.01:
            alert_level = "STOP"
            alert_message = f"P(ruin)={prob_ruin:.1%} > 1% — MANUAL AUDIT REQUIRED"
        elif prob_dd_10 > 0.15:
            alert_level = "CRITICAL"
            alert_message = f"P(DD>10%)={prob_dd_10:.1%} > 15% — DEFENSIVE MODE"
        elif prob_dd_10 > 0.05:
            alert_level = "RED"
            alert_message = f"P(DD>10%)={prob_dd_10:.1%} > 5% — REDUCE LEVERAGE 50%"
        else:
            alert_level = "OK"
            alert_message = f"P(DD>10%)={prob_dd_10:.1%}, P(ruin)={prob_ruin:.1%} — All clear"

        final_equities = capital * (1 + final_returns)

        result = MCPortfolioResult(
            n_simulations=self._n_sims,
            horizon_days=self._horizon,
            capital=round(capital, 2),
            prob_dd_5pct=round(prob_dd_5, 4),
            prob_dd_10pct=round(prob_dd_10, 4),
            prob_ruin=round(prob_ruin, 4),
            median_max_dd=round(float(np.median(max_dds)) * 100, 2),
            p95_max_dd=round(float(np.percentile(max_dds, 5)) * 100, 2),
            return_p5=round(float(np.percentile(final_returns, 5)) * 100, 2),
            return_p25=round(float(np.percentile(final_returns, 25)) * 100, 2),
            return_p50=round(float(np.percentile(final_returns, 50)) * 100, 2),
            return_p75=round(float(np.percentile(final_returns, 75)) * 100, 2),
            return_p95=round(float(np.percentile(final_returns, 95)) * 100, 2),
            equity_p5=round(float(np.percentile(final_equities, 5)), 2),
            equity_p50=round(float(np.percentile(final_equities, 50)), 2),
            equity_p95=round(float(np.percentile(final_equities, 95)), 2),
            alert_level=alert_level,
            alert_message=alert_message,
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Save report
        self._save_report(result)

        return result

    def _save_report(self, result: MCPortfolioResult) -> None:
        """Save MC report to JSON."""
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, indent=2, default=str)
            logger.info("MC report saved to %s", REPORT_PATH)
        except Exception as e:
            logger.error("Failed to save MC report: %s", e)
