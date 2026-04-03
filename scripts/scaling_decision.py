"""
Monthly scaling decision report generator.

Evaluates all KPI conditions for the current gate and produces
a PASS/FAIL/ABORT recommendation with detailed analysis.

Usage:
    python scripts/scaling_decision.py [--gate M1|M2|M3] [--output report.md]
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

DEFAULT_GATE_CONFIG = ROOT / "config" / "scaling_gates.yaml"

# Conditions that trigger ABORT when failed (critical failures)
ABORT_CONDITIONS = {"max_critical_bugs", "max_reconciliation_errors"}


class ScalingDecisionReport:
    """Evaluates KPI gates and generates scaling decision reports."""

    def __init__(self, gate_config_path: Path | None = None):
        config_path = Path(gate_config_path) if gate_config_path else DEFAULT_GATE_CONFIG
        with open(config_path) as f:
            self._config = yaml.safe_load(f)
        self._gates = self._config["gates"]

    @property
    def available_gates(self) -> list:
        """List of available gate names."""
        return list(self._gates.keys())

    def evaluate_gate(self, gate_name: str, kpi: dict) -> dict:
        """Evaluate all conditions for a gate.

        Supports two gate formats:
        - New multi-criteria format (conditions_primary / conditions_secondary /
          abort_conditions) used by gate_M1 v2.
        - Legacy flat format (conditions) used by gate_M2, gate_M3.

        Args:
            gate_name: e.g. 'gate_M1', 'gate_M2', 'gate_M3'
            kpi: dict of actual KPI values (keys matching condition names
                 without min_/max_ prefix)

        Returns:
            {
                gate: str,
                description: str,
                decision: "PASS" | "FAIL" | "ABORT" | "CONDITIONAL",
                conditions: [{name, threshold, actual, passed}],
                passed_count: int,
                total_count: int,
                recommendation: str,
                next_steps: [str],
                # Additional keys for multi-criteria gates:
                primary_results: [...],
                secondary_results: [...],
                abort_results: [...],
                secondary_passed_count: int,
                secondary_required: int,
            }
        """
        if gate_name not in self._gates:
            raise ValueError(
                f"Unknown gate '{gate_name}'. "
                f"Available: {self.available_gates}"
            )

        gate_cfg = self._gates[gate_name]

        # Dispatch to multi-criteria or legacy evaluation
        if "conditions_primary" in gate_cfg:
            return self._evaluate_gate_multi(gate_name, gate_cfg, kpi)
        else:
            return self._evaluate_gate_legacy(gate_name, gate_cfg, kpi)

    def _evaluate_gate_legacy(self, gate_name: str, gate_cfg: dict, kpi: dict) -> dict:
        """Legacy evaluation: flat conditions dict, PASS/FAIL/ABORT."""
        conditions_cfg = gate_cfg.get("conditions", {})
        decisions = gate_cfg.get("decisions", {})

        results = []
        has_abort_failure = False
        all_passed = True

        for cond_name, threshold in conditions_cfg.items():
            actual = self._get_kpi_value(cond_name, kpi)
            passed = self._evaluate_condition(cond_name, threshold, actual)

            results.append({
                "name": cond_name,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
            })

            if not passed:
                all_passed = False
                if cond_name in ABORT_CONDITIONS:
                    has_abort_failure = True

        if has_abort_failure:
            decision = "ABORT"
        elif all_passed:
            decision = "PASS"
        else:
            decision = "FAIL"

        recommendation = decisions.get(decision, "No recommendation defined")
        passed_count = sum(1 for r in results if r["passed"])
        next_steps = self._generate_next_steps(decision, results, gate_cfg)

        return {
            "gate": gate_name,
            "description": gate_cfg.get("description", ""),
            "decision": decision,
            "conditions": results,
            "passed_count": passed_count,
            "total_count": len(results),
            "recommendation": recommendation,
            "next_steps": next_steps,
        }

    def _evaluate_gate_multi(self, gate_name: str, gate_cfg: dict, kpi: dict) -> dict:
        """Multi-criteria evaluation: primary + secondary + abort conditions.

        Decision logic:
        1. ABORT conditions checked first — if ANY fires -> ABORT.
        2. PRIMARY conditions — ALL must pass.
        3. SECONDARY conditions — count passes vs min_secondary_count.
        4. Decision:
           - ALL primary PASS + secondary >= required -> PASS
           - ALL primary PASS + secondary < required  -> CONDITIONAL
           - ANY primary FAIL                         -> FAIL
           - ANY abort fires                          -> ABORT
        """
        decisions = gate_cfg.get("decisions", {})
        primary_cfg = gate_cfg.get("conditions_primary", {})
        secondary_cfg = gate_cfg.get("conditions_secondary", {})
        abort_cfg = gate_cfg.get("abort_conditions", {})

        secondary_required = secondary_cfg.get("min_secondary_count", 0)
        secondary_checks = secondary_cfg.get("checks", {})

        # --- 1. Evaluate ABORT conditions ---
        abort_results = []
        has_abort = False
        for cond_name, threshold in abort_cfg.items():
            actual = self._get_kpi_value(cond_name, kpi)
            passed = self._evaluate_condition(cond_name, threshold, actual)
            abort_results.append({
                "name": cond_name,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
            })
            if not passed:
                has_abort = True

        # --- 2. Evaluate PRIMARY conditions ---
        primary_results = []
        all_primary_passed = True
        for cond_name, threshold in primary_cfg.items():
            actual = self._get_kpi_value(cond_name, kpi)
            passed = self._evaluate_condition(cond_name, threshold, actual)
            primary_results.append({
                "name": cond_name,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
            })
            if not passed:
                all_primary_passed = False

        # --- 3. Evaluate SECONDARY conditions ---
        secondary_results = []
        for cond_name, threshold in secondary_checks.items():
            actual = self._get_kpi_value(cond_name, kpi)
            passed = self._evaluate_condition(cond_name, threshold, actual)
            secondary_results.append({
                "name": cond_name,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
            })

        secondary_passed = sum(1 for r in secondary_results if r["passed"])

        # --- 4. Decision ---
        if has_abort:
            decision = "ABORT"
        elif not all_primary_passed:
            decision = "FAIL"
        elif secondary_passed >= secondary_required:
            decision = "PASS"
        else:
            decision = "CONDITIONAL"

        # Merge all results for total counts
        all_results = primary_results + secondary_results
        passed_count = sum(1 for r in all_results if r["passed"])

        recommendation = decisions.get(decision, "No recommendation defined")
        next_steps = self._generate_next_steps(
            decision, all_results, gate_cfg,
            abort_results=abort_results,
        )

        return {
            "gate": gate_name,
            "description": gate_cfg.get("description", ""),
            "decision": decision,
            "conditions": all_results,
            "passed_count": passed_count,
            "total_count": len(all_results),
            "recommendation": recommendation,
            "next_steps": next_steps,
            "primary_results": primary_results,
            "secondary_results": secondary_results,
            "abort_results": abort_results,
            "secondary_passed_count": secondary_passed,
            "secondary_required": secondary_required,
        }

    def generate_report(self, gate_name: str, kpi: dict) -> str:
        """Generate a markdown report for the scaling decision.

        Supports both multi-criteria (primary/secondary/abort) and legacy
        (flat conditions) gate formats.

        Args:
            gate_name: gate identifier
            kpi: actual KPI values

        Returns:
            Markdown-formatted report string
        """
        result = self.evaluate_gate(gate_name, kpi)
        gate_cfg = self._gates[gate_name]
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"# Scaling Decision Report — {gate_name.upper()}",
            "",
            f"**Date:** {now}",
            f"**Gate:** {result['description']}",
            f"**Decision: {result['decision']}**",
            "",
            "## Capital",
            f"- Current: {f'${current_cap:,}' if isinstance((current_cap := gate_cfg.get('current_capital', 'N/A')), (int, float)) else str(current_cap)}",
            f"- Target: {f'${target_cap:,}' if isinstance((target_cap := gate_cfg.get('target_capital', 'N/A')), (int, float)) else str(target_cap)}",
            "",
        ]

        if "primary_results" in result:
            # Multi-criteria report layout
            lines.extend(self._report_section_table(
                "Abort Conditions", result["abort_results"]
            ))
            lines.extend(self._report_section_table(
                "Primary Conditions (ALL required)", result["primary_results"]
            ))
            sec_header = (
                f"Secondary Conditions "
                f"({result['secondary_passed_count']}/{result['secondary_required']} required)"
            )
            lines.extend(self._report_section_table(
                sec_header, result["secondary_results"]
            ))
        else:
            # Legacy flat report layout
            lines.extend([
                f"## KPI Conditions ({result['passed_count']}/{result['total_count']} passed)",
                "",
            ])
            lines.extend(self._format_conditions_table(result["conditions"]))

        lines.extend([
            "",
            "## Recommendation",
            "",
            f"{result['recommendation']}",
            "",
        ])

        if result["next_steps"]:
            lines.extend([
                "## Next Steps",
                "",
            ])
            for step in result["next_steps"]:
                lines.append(f"- {step}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Report formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_conditions_table(conditions: list) -> list:
        """Return markdown table lines for a list of condition results."""
        lines = [
            "| Condition | Threshold | Actual | Status |",
            "|-----------|-----------|--------|--------|",
        ]
        for cond in conditions:
            status = "PASS" if cond["passed"] else "FAIL"
            actual_str = str(cond["actual"]) if cond["actual"] is not None else "N/A"
            lines.append(
                f"| {cond['name']} | {cond['threshold']} | {actual_str} | {status} |"
            )
        return lines

    def _report_section_table(self, title: str, conditions: list) -> list:
        """Return a titled markdown section with a conditions table."""
        lines = [f"## {title}", ""]
        if not conditions:
            lines.append("_No conditions defined._")
            lines.append("")
            return lines
        lines.extend(self._format_conditions_table(conditions))
        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_kpi_value(self, cond_name: str, kpi: dict):
        """Extract KPI value matching a condition name (strip prefix)."""
        key = cond_name
        for prefix in ("min_", "max_", "zero_"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break
        return kpi.get(key)

    def _evaluate_condition(self, cond_name: str, threshold, actual) -> bool:
        """Evaluate a single condition.

        Returns True if the condition is satisfied.
        """
        if actual is None:
            return False

        if cond_name.startswith("min_"):
            return actual >= threshold
        elif cond_name.startswith("max_"):
            return actual <= threshold
        elif cond_name.startswith("zero_"):
            return actual == 0
        else:
            return actual == threshold

    def _generate_next_steps(
        self,
        decision: str,
        conditions: list,
        gate_cfg: dict,
        abort_results: list | None = None,
    ) -> list:
        """Generate actionable next steps based on decision and failed conditions."""
        steps = []

        if decision == "PASS":
            target = gate_cfg.get("target_capital")
            if target:
                steps.append(f"Deposit additional capital to reach ${target:,}")
            steps.append("Review and update strategy allocation for new capital level")
            steps.append("Set up monitoring for next gate evaluation")

        elif decision == "CONDITIONAL":
            steps.append("Maintain current capital, extend evaluation by 15 days")
            # List failed secondary conditions
            failed_secondary = [c for c in conditions if not c["passed"]]
            for cond in failed_secondary:
                name = cond["name"]
                threshold = cond["threshold"]
                actual = cond["actual"]
                if actual is None:
                    steps.append(f"Collect data for missing metric: {name}")
                elif name.startswith("min_"):
                    steps.append(
                        f"Improve {name}: current {actual}, need >= {threshold}"
                    )
                elif name.startswith("max_"):
                    steps.append(
                        f"Reduce {name}: current {actual}, need <= {threshold}"
                    )
            steps.append("Re-evaluate secondary conditions in 15 days")

        elif decision == "FAIL":
            failed = [c for c in conditions if not c["passed"]]
            for cond in failed:
                name = cond["name"]
                threshold = cond["threshold"]
                actual = cond["actual"]
                if actual is None:
                    steps.append(f"Collect data for missing metric: {name}")
                elif name.startswith("min_"):
                    steps.append(
                        f"Improve {name}: current {actual}, need >= {threshold}"
                    )
                elif name.startswith("max_"):
                    steps.append(
                        f"Reduce {name}: current {actual}, need <= {threshold}"
                    )
            steps.append("Re-evaluate in 2 weeks after fixes")

        elif decision == "ABORT":
            steps.append("CRITICAL: Stop live trading immediately")
            steps.append("Return to paper trading for minimum 30 days")
            # Surface which abort condition(s) fired
            if abort_results:
                fired = [r for r in abort_results if not r["passed"]]
                for r in fired:
                    steps.append(
                        f"Abort trigger: {r['name']} "
                        f"(threshold {r['threshold']}, actual {r['actual']})"
                    )
            steps.append("Root cause analysis on all critical failures")
            steps.append("Fix all issues before resuming live trading")

        return steps


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Monthly scaling decision report generator"
    )
    parser.add_argument(
        "--gate",
        choices=["M1", "M2", "M3"],
        default="M1",
        help="Gate to evaluate (default: M1)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path for the report (default: stdout)",
    )

    # KPI overrides via CLI
    parser.add_argument("--trades", type=int, help="Number of trades")
    parser.add_argument("--sharpe-30d", type=float, help="30-day Sharpe ratio")
    parser.add_argument("--sharpe-60d", type=float, help="60-day Sharpe ratio")
    parser.add_argument("--sharpe-90d", type=float, help="90-day Sharpe ratio")
    parser.add_argument("--drawdown-pct", type=float, help="Max drawdown %%")
    parser.add_argument("--single-loss-pct", type=float, help="Max single loss %%")
    parser.add_argument("--slippage-ratio", type=float, help="Slippage ratio")
    parser.add_argument("--critical-bugs", type=int, help="Number of critical bugs")
    parser.add_argument("--reconciliation-errors", type=int, help="Number of reconciliation errors")
    parser.add_argument("--execution-quality", type=float, help="Execution quality (0-1)")
    parser.add_argument("--cost-ratio", type=float, help="Cost ratio")
    parser.add_argument("--live-vs-paper-sharpe-ratio", type=float, help="Live vs paper Sharpe ratio")
    parser.add_argument("--strategies-live", type=int, help="Number of live strategies")

    args = parser.parse_args()

    # Build KPI dict from CLI args
    kpi = {}
    arg_map = {
        "trades": "trades",
        "sharpe_30d": "sharpe_30d",
        "sharpe_60d": "sharpe_60d",
        "sharpe_90d": "sharpe_90d",
        "drawdown_pct": "drawdown_pct",
        "single_loss_pct": "single_loss_pct",
        "slippage_ratio": "slippage_ratio",
        "critical_bugs": "critical_bugs",
        "reconciliation_errors": "reconciliation_errors",
        "execution_quality": "execution_quality",
        "cost_ratio": "cost_ratio",
        "live_vs_paper_sharpe_ratio": "live_vs_paper_sharpe_ratio",
        "strategies_live": "strategies_live",
    }
    for attr, key in arg_map.items():
        val = getattr(args, attr, None)
        if val is not None:
            kpi[key] = val

    gate_name = f"gate_{args.gate}"

    reporter = ScalingDecisionReport()
    report = reporter.generate_report(gate_name, kpi)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to {output_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
