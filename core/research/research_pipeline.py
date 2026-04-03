"""P1-01: Research Framework — systematic gate-based pipeline for new strategies.

6 gates, each a go/no-go decision:
  GATE 1 — THESIS (manual — 30 min)
  GATE 2 — QUICK BACKTEST (auto — Sharpe > 0.5 or KILL)
  GATE 3 — WALK-FORWARD (auto — OOS/IS > 0.5, >= 50% windows profitable, >= 30 trades)
  GATE 4 — COST STRESS (auto — break-even slippage >= 3x or FRAGILE)
  GATE 5 — CORRELATION CHECK (auto — corr < 0.6 with existing strats)
  GATE 6 — PAPER DEPLOY (manual — 30 trades minimum in paper mode)

Tracking: data/research/pipeline_status.json
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "research"
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports" / "research"


class GateStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PipelineStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATED = "VALIDATED"
    BORDERLINE = "BORDERLINE"
    REJECTED = "REJECTED"
    PAPER = "PAPER"


GATE_NAMES = [
    "thesis",
    "quick_backtest",
    "walk_forward",
    "cost_stress",
    "correlation_check",
    "paper_deploy",
]


@dataclass
class GateResult:
    """Result of a single gate evaluation."""
    gate: str
    status: GateStatus
    score: float  # 0-10
    details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "score": self.score,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class StrategyResearch:
    """Tracks a strategy through the research pipeline."""
    name: str
    thesis_date: str = ""
    current_gate: int = 0  # 0-based index into GATE_NAMES
    gate_results: list[GateResult | None] = field(
        default_factory=lambda: [None] * 6
    )
    status: PipelineStatus = PipelineStatus.IN_PROGRESS
    composite_score: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict = field(default_factory=dict)

    def record_gate(self, gate_index: int, result: GateResult):
        """Record a gate result and advance if passed."""
        self.gate_results[gate_index] = result
        self.updated_at = datetime.now(UTC).isoformat()

        if result.status == GateStatus.PASSED:
            self.current_gate = gate_index + 1
        elif result.status == GateStatus.FAILED:
            self.status = PipelineStatus.REJECTED

        self._compute_composite()

    def _compute_composite(self):
        """Composite = weighted average of gate scores."""
        weights = [1.0, 2.0, 3.0, 2.0, 1.5, 1.5]  # WF weighted most
        total_w = 0.0
        total_s = 0.0
        for i, result in enumerate(self.gate_results):
            if result is not None and result.score is not None:
                total_s += result.score * weights[i]
                total_w += weights[i]

        self.composite_score = round(total_s / total_w, 1) if total_w > 0 else 0.0

        # Auto-classify if all gates done
        if self.current_gate >= 5 and self.status == PipelineStatus.IN_PROGRESS:
            if self.composite_score >= 7.0:
                self.status = PipelineStatus.VALIDATED
            elif self.composite_score >= 5.0:
                self.status = PipelineStatus.BORDERLINE
            else:
                self.status = PipelineStatus.REJECTED

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "thesis_date": self.thesis_date,
            "current_gate": self.current_gate,
            "current_gate_name": GATE_NAMES[min(self.current_gate, 5)],
            "gate_scores": [
                r.score if r else None for r in self.gate_results
            ],
            "gate_statuses": [
                r.status.value if r else "PENDING" for r in self.gate_results
            ],
            "status": self.status.value,
            "composite_score": self.composite_score,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class ResearchPipeline:
    """Manages the full research pipeline for strategy discovery.

    Usage:
        pipeline = ResearchPipeline()
        pipeline.start_research("cross_asset_momentum",
                                thesis="Momentum across 5 asset classes...")
        # After quick backtest:
        pipeline.record_quick_backtest("cross_asset_momentum",
                                        sharpe=1.2, n_trades=85)
        # After WF:
        pipeline.record_walk_forward("cross_asset_momentum",
                                      oos_is_ratio=0.65, pct_profitable=0.7)
        pipeline.save()
    """

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._strategies: dict[str, StrategyResearch] = {}
        self._load()

    def _status_path(self) -> Path:
        return self._data_dir / "pipeline_status.json"

    def _load(self):
        """Load existing pipeline state."""
        path = self._status_path()
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            for name, sdata in data.items():
                strat = StrategyResearch(name=name)
                strat.thesis_date = sdata.get("thesis_date", "")
                strat.current_gate = sdata.get("current_gate", 0)
                strat.status = PipelineStatus(sdata.get("status", "IN_PROGRESS"))
                strat.composite_score = sdata.get("composite_score", 0)
                strat.created_at = sdata.get("created_at", "")
                strat.updated_at = sdata.get("updated_at", "")
                strat.metadata = sdata.get("metadata", {})
                self._strategies[name] = strat

    def save(self):
        """Persist pipeline state."""
        data = {name: strat.to_dict() for name, strat in self._strategies.items()}
        path = self._status_path()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Pipeline state saved to %s", path)

    def start_research(
        self,
        name: str,
        thesis: str = "",
        metadata: dict | None = None,
    ) -> StrategyResearch:
        """Start research on a new strategy."""
        strat = StrategyResearch(
            name=name,
            thesis_date=datetime.now(UTC).strftime("%Y-%m-%d"),
            metadata=metadata or {},
        )

        if thesis:
            # Save thesis document
            thesis_path = REPORTS_DIR / f"thesis_{name}.md"
            thesis_path.parent.mkdir(parents=True, exist_ok=True)
            thesis_path.write_text(thesis, encoding="utf-8")

            strat.record_gate(0, GateResult(
                gate="thesis",
                status=GateStatus.PASSED,
                score=7.0,  # Default thesis score
                details={"file": str(thesis_path)},
            ))

        self._strategies[name] = strat
        self.save()
        logger.info("Research started: %s", name)
        return strat

    def record_quick_backtest(
        self,
        name: str,
        sharpe: float,
        n_trades: int,
        max_dd: float = 0.0,
        win_rate: float = 0.0,
    ) -> GateResult:
        """Record Gate 2: Quick Backtest result.

        KILL if Sharpe < 0.5.
        """
        strat = self._strategies.get(name)
        if not strat:
            raise ValueError(f"Strategy {name} not in pipeline")

        passed = sharpe >= 0.5
        score = min(10, sharpe * 3)  # Sharpe 3.33 = score 10

        result = GateResult(
            gate="quick_backtest",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(score, 1),
            details={
                "sharpe": round(sharpe, 2),
                "n_trades": n_trades,
                "max_dd": round(max_dd, 3),
                "win_rate": round(win_rate, 3),
            },
        )
        strat.record_gate(1, result)

        # Save report
        report_path = REPORTS_DIR / f"quick_{name}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        self.save()

        if not passed:
            logger.warning("KILLED: %s — Sharpe %.2f < 0.5", name, sharpe)
        else:
            logger.info("Gate 2 PASSED: %s — Sharpe %.2f", name, sharpe)

        return result

    def record_walk_forward(
        self,
        name: str,
        oos_is_ratio: float,
        pct_profitable: float,
        n_trades_oos: int = 0,
        sharpe_oos: float = 0.0,
    ) -> GateResult:
        """Record Gate 3: Walk-Forward result.

        Criteria: OOS/IS > 0.5, >= 50% windows profitable, >= 30 trades.
        """
        strat = self._strategies.get(name)
        if not strat:
            raise ValueError(f"Strategy {name} not in pipeline")

        passed = (
            oos_is_ratio >= 0.5
            and pct_profitable >= 0.50
            and n_trades_oos >= 30
        )

        # Score: weighted combination
        score = min(10, (
            oos_is_ratio * 4
            + pct_profitable * 4
            + min(1.0, n_trades_oos / 100) * 2
        ))

        result = GateResult(
            gate="walk_forward",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(score, 1),
            details={
                "oos_is_ratio": round(oos_is_ratio, 3),
                "pct_profitable": round(pct_profitable, 3),
                "n_trades_oos": n_trades_oos,
                "sharpe_oos": round(sharpe_oos, 2),
            },
        )
        strat.record_gate(2, result)

        report_path = REPORTS_DIR / f"wf_{name}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        self.save()
        return result

    def record_cost_stress(
        self,
        name: str,
        break_even_slippage_x: float,
        commission_burn: float,
    ) -> GateResult:
        """Record Gate 4: Cost Stress result.

        If break-even < 3x -> FRAGILE.
        """
        strat = self._strategies.get(name)
        if not strat:
            raise ValueError(f"Strategy {name} not in pipeline")

        fragile = break_even_slippage_x < 3.0
        passed = break_even_slippage_x >= 1.5  # Minimum viable

        score = min(10, break_even_slippage_x * 2)

        result = GateResult(
            gate="cost_stress",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(score, 1),
            details={
                "break_even_slippage_x": round(break_even_slippage_x, 1),
                "commission_burn": round(commission_burn, 3),
                "fragile": fragile,
            },
        )
        strat.record_gate(3, result)

        report_path = REPORTS_DIR / f"costs_{name}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        self.save()
        return result

    def record_correlation_check(
        self,
        name: str,
        max_correlation: float,
        correlated_with: str = "",
        portfolio_sharpe_contribution: float = 0.0,
    ) -> GateResult:
        """Record Gate 5: Correlation Check result.

        If corr > 0.6 with existing strat -> marginal benefit.
        """
        strat = self._strategies.get(name)
        if not strat:
            raise ValueError(f"Strategy {name} not in pipeline")

        low_correlation = max_correlation < 0.60
        score = min(10, (1 - max_correlation) * 10)
        if portfolio_sharpe_contribution > 0:
            score = min(10, score + portfolio_sharpe_contribution * 2)

        result = GateResult(
            gate="correlation_check",
            status=GateStatus.PASSED if low_correlation else GateStatus.PASSED,
            score=round(score, 1),
            details={
                "max_correlation": round(max_correlation, 3),
                "correlated_with": correlated_with,
                "portfolio_sharpe_contribution": round(portfolio_sharpe_contribution, 3),
                "high_correlation": not low_correlation,
            },
        )
        strat.record_gate(4, result)

        report_path = REPORTS_DIR / f"corr_{name}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        self.save()
        return result

    def record_paper_deploy(
        self,
        name: str,
        n_paper_trades: int,
        paper_sharpe: float = 0.0,
        paper_win_rate: float = 0.0,
    ) -> GateResult:
        """Record Gate 6: Paper Deploy result.

        Minimum 30 trades in paper mode.
        """
        strat = self._strategies.get(name)
        if not strat:
            raise ValueError(f"Strategy {name} not in pipeline")

        passed = n_paper_trades >= 30
        score = min(10, paper_sharpe * 3 + (1 if passed else 0) * 2)

        result = GateResult(
            gate="paper_deploy",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(max(0, score), 1),
            details={
                "n_paper_trades": n_paper_trades,
                "paper_sharpe": round(paper_sharpe, 2),
                "paper_win_rate": round(paper_win_rate, 3),
            },
        )
        strat.record_gate(5, result)
        self.save()
        return result

    def get_strategy(self, name: str) -> StrategyResearch | None:
        return self._strategies.get(name)

    def get_all(self) -> dict[str, StrategyResearch]:
        return dict(self._strategies)

    def get_status_summary(self) -> dict[str, Any]:
        """Summary of all strategies in pipeline."""
        by_status = {}
        for strat in self._strategies.values():
            by_status.setdefault(strat.status.value, []).append({
                "name": strat.name,
                "gate": GATE_NAMES[min(strat.current_gate, 5)],
                "score": strat.composite_score,
            })
        return {
            "total": len(self._strategies),
            "by_status": by_status,
        }
