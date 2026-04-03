"""P1-03: Backtest Automation — automated gates 2-4 of research pipeline.

Usage:
  python -m core.research.auto_backtest strategies_v2/fx/carry_crash_protection.py

The script:
  1. Loads the strategy
  2. Runs quick backtest (2 years, no optimization)
  3. If Sharpe > 0.5 -> run WF (5 windows)
  4. If WF VALIDATED -> run cost stress (1x-5x slippage)
  5. Produces JSON + Markdown report
  6. Updates data/research/pipeline_status.json
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports" / "research"


@dataclass
class QuickBacktestResult:
    """Result of a quick 2-year backtest."""
    sharpe: float
    total_return: float
    max_drawdown: float
    win_rate: float
    n_trades: int
    profit_factor: float
    avg_trade_pnl: float
    passed: bool  # Sharpe >= 0.5

    def to_dict(self) -> dict:
        return {
            "sharpe": round(self.sharpe, 2),
            "total_return": round(self.total_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 3),
            "n_trades": self.n_trades,
            "profit_factor": round(self.profit_factor, 2),
            "avg_trade_pnl": round(self.avg_trade_pnl, 2),
            "passed": self.passed,
        }


@dataclass
class WalkForwardResult:
    """Result of walk-forward analysis."""
    n_windows: int
    oos_is_ratio: float
    pct_profitable: float
    n_trades_oos: int
    sharpe_oos: float
    sharpe_is: float
    windows: list[dict] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "n_windows": self.n_windows,
            "oos_is_ratio": round(self.oos_is_ratio, 3),
            "pct_profitable": round(self.pct_profitable, 3),
            "n_trades_oos": self.n_trades_oos,
            "sharpe_oos": round(self.sharpe_oos, 2),
            "sharpe_is": round(self.sharpe_is, 2),
            "passed": self.passed,
            "windows": self.windows,
        }


@dataclass
class CostStressResult:
    """Result of cost stress testing."""
    multipliers_tested: list[float] = field(default_factory=list)
    sharpe_at_multiplier: dict[str, float] = field(default_factory=dict)
    break_even_slippage_x: float = 0.0
    commission_burn: float = 0.0
    fragile: bool = False

    def to_dict(self) -> dict:
        return {
            "multipliers_tested": self.multipliers_tested,
            "sharpe_at_multiplier": {k: round(v, 2) for k, v in self.sharpe_at_multiplier.items()},
            "break_even_slippage_x": round(self.break_even_slippage_x, 1),
            "commission_burn": round(self.commission_burn, 3),
            "fragile": self.fragile,
        }


@dataclass
class AutoBacktestReport:
    """Full automated backtest report."""
    strategy_name: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    quick_backtest: QuickBacktestResult | None = None
    walk_forward: WalkForwardResult | None = None
    cost_stress: CostStressResult | None = None
    final_verdict: str = "PENDING"  # VALIDATED, BORDERLINE, REJECTED
    composite_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "timestamp": self.timestamp,
            "final_verdict": self.final_verdict,
            "composite_score": round(self.composite_score, 1),
            "quick_backtest": self.quick_backtest.to_dict() if self.quick_backtest else None,
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "cost_stress": self.cost_stress.to_dict() if self.cost_stress else None,
        }

    def save(self, path: Path | None = None):
        path = path or (REPORTS_DIR / f"auto_{self.strategy_name}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Auto backtest report saved to %s", path)


class AutoBacktester:
    """Automated backtest pipeline for strategy evaluation.

    Usage:
        bt = AutoBacktester()
        report = bt.run_full_pipeline(
            strategy_name="cross_asset_momentum",
            returns=daily_returns_series,
            trades=list_of_trade_dicts,
            cost_bps=2.0,
        )
    """

    def __init__(
        self,
        sharpe_threshold: float = 0.5,
        wf_windows: int = 5,
        wf_oos_ratio: float = 0.30,
        min_wf_trades: int = 30,
        min_wf_profitable: float = 0.50,
    ):
        self._sharpe_threshold = sharpe_threshold
        self._wf_windows = wf_windows
        self._wf_oos_ratio = wf_oos_ratio
        self._min_wf_trades = min_wf_trades
        self._min_wf_profitable = min_wf_profitable

    def run_full_pipeline(
        self,
        strategy_name: str,
        returns: pd.Series,
        trades: list[dict] | None = None,
        cost_bps: float = 2.0,
    ) -> AutoBacktestReport:
        """Run Gates 2-4 automatically.

        Args:
            strategy_name: Name of the strategy
            returns: Daily return series
            trades: List of trade dicts with {pnl, notional, commission}
            cost_bps: Cost per trade in bps for stress testing
        """
        report = AutoBacktestReport(strategy_name=strategy_name)

        # Gate 2: Quick backtest
        quick = self.quick_backtest(returns, trades)
        report.quick_backtest = quick

        if not quick.passed:
            report.final_verdict = "REJECTED"
            report.composite_score = quick.sharpe * 3  # 0-10 scale
            logger.warning("KILLED at Gate 2: %s — Sharpe %.2f", strategy_name, quick.sharpe)
            report.save()
            return report

        # Gate 3: Walk-Forward
        wf = self.walk_forward(returns, trades)
        report.walk_forward = wf

        if not wf.passed:
            report.final_verdict = "REJECTED"
            report.composite_score = (quick.sharpe * 3 + wf.sharpe_oos * 3) / 2
            logger.warning("REJECTED at Gate 3: %s", strategy_name)
            report.save()
            return report

        # Gate 4: Cost Stress
        cost = self.cost_stress(returns, trades, cost_bps)
        report.cost_stress = cost

        # Final verdict
        report.composite_score = (
            min(10, quick.sharpe * 3) * 0.2
            + min(10, wf.sharpe_oos * 3) * 0.4
            + min(10, cost.break_even_slippage_x * 2) * 0.2
            + (10 if wf.pct_profitable >= 0.7 else wf.pct_profitable * 14) * 0.2
        )

        if report.composite_score >= 7.0:
            report.final_verdict = "VALIDATED"
        elif report.composite_score >= 5.0:
            report.final_verdict = "BORDERLINE"
        else:
            report.final_verdict = "REJECTED"

        report.save()
        logger.info(
            "Pipeline complete: %s — %s (score %.1f)",
            strategy_name, report.final_verdict, report.composite_score,
        )
        return report

    def quick_backtest(
        self,
        returns: pd.Series,
        trades: list[dict] | None = None,
    ) -> QuickBacktestResult:
        """Gate 2: Quick backtest on full data, no optimization."""
        if returns.empty:
            return QuickBacktestResult(
                sharpe=0, total_return=0, max_drawdown=0, win_rate=0,
                n_trades=0, profit_factor=0, avg_trade_pnl=0, passed=False,
            )

        # Compute metrics from returns
        sharpe = self._compute_sharpe(returns)
        total_return = float((1 + returns).prod() - 1)
        max_dd = self._compute_max_dd(returns)

        # Trade-level metrics
        n_trades = len(trades) if trades else len(returns[returns != 0])
        if trades:
            pnls = [t.get("pnl", 0) for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            win_rate = len(wins) / len(pnls) if pnls else 0
            profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
            avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        else:
            positive = returns[returns > 0]
            win_rate = len(positive) / len(returns) if len(returns) > 0 else 0
            profit_factor = positive.sum() / abs(returns[returns < 0].sum()) if returns[returns < 0].sum() != 0 else 0
            avg_pnl = float(returns.mean())

        return QuickBacktestResult(
            sharpe=sharpe,
            total_return=total_return,
            max_drawdown=max_dd,
            win_rate=win_rate,
            n_trades=n_trades,
            profit_factor=min(profit_factor, 99.0),
            avg_trade_pnl=avg_pnl,
            passed=sharpe >= self._sharpe_threshold,
        )

    def walk_forward(
        self,
        returns: pd.Series,
        trades: list[dict] | None = None,
    ) -> WalkForwardResult:
        """Gate 3: Rolling walk-forward analysis."""
        n = len(returns)
        if n < 100:
            return WalkForwardResult(
                n_windows=0, oos_is_ratio=0, pct_profitable=0,
                n_trades_oos=0, sharpe_oos=0, sharpe_is=0, passed=False,
            )

        window_size = n // self._wf_windows
        oos_size = int(window_size * self._wf_oos_ratio)
        is_size = window_size - oos_size

        windows = []
        is_sharpes = []
        oos_sharpes = []
        oos_profitable = 0
        total_oos_trades = 0

        for i in range(self._wf_windows):
            start = i * window_size
            is_end = start + is_size
            oos_end = start + window_size

            if oos_end > n:
                break

            is_returns = returns.iloc[start:is_end]
            oos_returns = returns.iloc[is_end:oos_end]

            is_sharpe = self._compute_sharpe(is_returns)
            oos_sharpe = self._compute_sharpe(oos_returns)
            oos_ret = float((1 + oos_returns).prod() - 1)

            is_sharpes.append(is_sharpe)
            oos_sharpes.append(oos_sharpe)

            if oos_ret > 0:
                oos_profitable += 1

            oos_trades = len(oos_returns[oos_returns != 0])
            total_oos_trades += oos_trades

            windows.append({
                "window": i + 1,
                "is_sharpe": round(is_sharpe, 2),
                "oos_sharpe": round(oos_sharpe, 2),
                "oos_return": round(oos_ret, 4),
                "oos_trades": oos_trades,
            })

        n_windows = len(windows)
        if n_windows == 0:
            return WalkForwardResult(
                n_windows=0, oos_is_ratio=0, pct_profitable=0,
                n_trades_oos=0, sharpe_oos=0, sharpe_is=0, passed=False,
            )

        avg_is = np.mean(is_sharpes)
        avg_oos = np.mean(oos_sharpes)
        oos_is_ratio = avg_oos / avg_is if avg_is > 0 else 0
        pct_prof = oos_profitable / n_windows

        passed = (
            oos_is_ratio >= 0.5
            and pct_prof >= self._min_wf_profitable
            and total_oos_trades >= self._min_wf_trades
        )

        return WalkForwardResult(
            n_windows=n_windows,
            oos_is_ratio=oos_is_ratio,
            pct_profitable=pct_prof,
            n_trades_oos=total_oos_trades,
            sharpe_oos=avg_oos,
            sharpe_is=avg_is,
            windows=windows,
            passed=passed,
        )

    def cost_stress(
        self,
        returns: pd.Series,
        trades: list[dict] | None = None,
        base_cost_bps: float = 2.0,
    ) -> CostStressResult:
        """Gate 4: Stress test with increasing costs."""
        multipliers = [1.0, 2.0, 3.0, 5.0]
        sharpe_by_mult = {}

        for mult in multipliers:
            cost_bps = base_cost_bps * mult
            # Subtract costs from returns
            cost_per_trade = cost_bps / 10_000
            adjusted_returns = returns - cost_per_trade * (returns != 0).astype(float)
            sharpe = self._compute_sharpe(adjusted_returns)
            sharpe_by_mult[f"{mult:.0f}x"] = sharpe

        # Find break-even slippage multiplier
        base_sharpe = sharpe_by_mult.get("1x", 0)
        break_even_x = 0.0
        for mult in [1.0, 2.0, 3.0, 5.0, 7.0, 10.0]:
            cost_bps = base_cost_bps * mult
            cost_per_trade = cost_bps / 10_000
            adj = returns - cost_per_trade * (returns != 0).astype(float)
            if self._compute_sharpe(adj) <= 0:
                break_even_x = mult
                break
        else:
            break_even_x = 10.0  # Very robust

        # Commission burn
        if trades:
            total_comm = sum(t.get("commission", 0) for t in trades)
            total_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
            commission_burn = total_comm / total_profit if total_profit > 0 else 1.0
        else:
            commission_burn = 0.0

        return CostStressResult(
            multipliers_tested=multipliers,
            sharpe_at_multiplier=sharpe_by_mult,
            break_even_slippage_x=break_even_x,
            commission_burn=commission_burn,
            fragile=break_even_x < 3.0,
        )

    def _compute_sharpe(self, returns: pd.Series, annualization: float = 252.0) -> float:
        if returns.empty or returns.std() == 0:
            return 0.0
        return float(returns.mean() / returns.std() * np.sqrt(annualization))

    def _compute_max_dd(self, returns: pd.Series) -> float:
        cumulative = (1 + returns).cumprod()
        peak = cumulative.cummax()
        drawdown = (cumulative - peak) / peak
        return float(drawdown.min()) if not drawdown.empty else 0.0
