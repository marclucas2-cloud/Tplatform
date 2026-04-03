"""
Tests unitaires — ScalingDecisionReport (scaling gates).

Couvre :
  - Load gate config
  - Evaluate gate PASS (all conditions met)
  - Evaluate gate FAIL (some conditions missing)
  - Evaluate gate ABORT (critical failure)
  - Report generation (markdown format)
  - All 3 gates (M1, M2, M3)
  - Edge: empty KPI dict
  - Edge: unknown gate name
  - Edge: partial KPI values
  - Next steps generation per decision type
"""

import sys
from pathlib import Path

import pytest

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.scaling_decision import ScalingDecisionReport

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def config_path():
    """Path to the real scaling_gates.yaml config."""
    return ROOT / "config" / "scaling_gates.yaml"


@pytest.fixture
def reporter(config_path):
    """Fresh ScalingDecisionReport."""
    return ScalingDecisionReport(gate_config_path=config_path)


@pytest.fixture
def kpi_m1_pass():
    """KPI dict that passes all gate_M1 v2 conditions (primary + secondary + abort)."""
    return {
        # Primary conditions
        "calendar_days": 30,
        "trades": 25,
        "strategies_active": 5,
        "drawdown_pct": 3.0,
        "single_loss_pct": 1.0,
        "critical_bugs": 0,
        "reconciliation_errors": 0,
        # Secondary conditions
        "sharpe_period": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "slippage_ratio": 2.0,
        "execution_quality": 0.90,
        # Abort condition KPIs (must be within safe range)
        "drawdown_abort_pct": 3.0,
        "critical_bugs_abort": 0,
        "consecutive_losing_weeks": 0,
    }


@pytest.fixture
def kpi_m2_pass():
    """KPI dict that passes all gate_M2 conditions."""
    return {
        "trades": 150,
        "sharpe_60d": 1.0,
        "drawdown_pct": 3.0,
        "cost_ratio": 0.20,
        "live_vs_paper_sharpe_ratio": 0.7,
    }


@pytest.fixture
def kpi_m3_pass():
    """KPI dict that passes all gate_M3 conditions."""
    return {
        "trades": 200,
        "sharpe_90d": 1.2,
        "drawdown_pct": 3.0,
        "strategies_live": 8,
    }


# =============================================================================
# LOAD CONFIG
# =============================================================================

class TestLoadConfig:
    def test_config_loads_successfully(self, reporter):
        assert len(reporter.available_gates) == 3

    def test_available_gates(self, reporter):
        assert "gate_M1" in reporter.available_gates
        assert "gate_M2" in reporter.available_gates
        assert "gate_M3" in reporter.available_gates

    def test_unknown_gate_raises(self, reporter):
        with pytest.raises(ValueError, match="Unknown gate"):
            reporter.evaluate_gate("gate_M99", {})


# =============================================================================
# GATE PASS
# =============================================================================

class TestGatePass:
    def test_gate_m1_pass(self, reporter, kpi_m1_pass):
        result = reporter.evaluate_gate("gate_M1", kpi_m1_pass)
        assert result["decision"] == "PASS"
        assert result["passed_count"] == result["total_count"]
        assert all(c["passed"] for c in result["conditions"])

    def test_gate_m2_pass(self, reporter, kpi_m2_pass):
        result = reporter.evaluate_gate("gate_M2", kpi_m2_pass)
        assert result["decision"] == "PASS"
        assert result["passed_count"] == result["total_count"]

    def test_gate_m3_pass(self, reporter, kpi_m3_pass):
        result = reporter.evaluate_gate("gate_M3", kpi_m3_pass)
        assert result["decision"] == "PASS"
        assert result["passed_count"] == result["total_count"]

    def test_pass_has_recommendation(self, reporter, kpi_m1_pass):
        result = reporter.evaluate_gate("gate_M1", kpi_m1_pass)
        assert "recommendation" in result
        assert len(result["recommendation"]) > 0

    def test_pass_next_steps_include_deposit(self, reporter, kpi_m1_pass):
        result = reporter.evaluate_gate("gate_M1", kpi_m1_pass)
        assert any("capital" in s.lower() or "deposit" in s.lower()
                    for s in result["next_steps"])


# =============================================================================
# GATE FAIL
# =============================================================================

class TestGateFail:
    def test_gate_m1_fail_low_trades(self, reporter):
        kpi = {
            # Primary — trades too low
            "calendar_days": 30,
            "trades": 5,  # Below 20 threshold
            "strategies_active": 5,
            "drawdown_pct": 3.0,
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            # Secondary (all pass, but primary fails -> FAIL)
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            # Abort safe
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "FAIL"
        assert result["passed_count"] < result["total_count"]

    def test_gate_m1_fail_high_drawdown(self, reporter):
        kpi = {
            "calendar_days": 30,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 6.0,  # Above 5.0 primary threshold
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            "drawdown_abort_pct": 6.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "FAIL"

    def test_fail_identifies_failed_conditions(self, reporter):
        kpi = {
            "calendar_days": 10,  # Below 21
            "trades": 5,  # Below 20
            "strategies_active": 5,
            "drawdown_pct": 3.0,
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "FAIL"
        failed_primary = [c for c in result["primary_results"] if not c["passed"]]
        assert len(failed_primary) == 2  # calendar_days and trades

    def test_fail_next_steps_mention_improvements(self, reporter):
        kpi = {
            "calendar_days": 10,
            "trades": 5,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "FAIL"
        assert any("Improve" in s or "Reduce" in s or "Collect" in s
                    for s in result["next_steps"])


# =============================================================================
# GATE ABORT
# =============================================================================

class TestGateAbort:
    def test_abort_on_high_drawdown(self, reporter):
        kpi = {
            "calendar_days": 30,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 3.0,
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            "drawdown_abort_pct": 9.0,  # > 8.0 triggers ABORT
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "ABORT"

    def test_abort_on_critical_bugs(self, reporter):
        kpi = {
            "calendar_days": 30,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 3.0,
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 2,  # > 0 triggers ABORT
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "ABORT"

    def test_abort_next_steps_include_stop_trading(self, reporter):
        kpi = {
            "drawdown_abort_pct": 10.0,  # triggers abort
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "ABORT"
        assert any("stop" in s.lower() or "paper" in s.lower()
                    for s in result["next_steps"])


# =============================================================================
# REPORT GENERATION
# =============================================================================

class TestReportGeneration:
    def test_report_is_markdown(self, reporter, kpi_m1_pass):
        report = reporter.generate_report("gate_M1", kpi_m1_pass)
        assert report.startswith("# Scaling Decision Report")
        assert "| Condition |" in report  # Present in section tables
        assert "## Recommendation" in report
        # Multi-criteria report has section headers
        assert "Primary Conditions" in report
        assert "Secondary Conditions" in report

    def test_report_contains_decision(self, reporter, kpi_m1_pass):
        report = reporter.generate_report("gate_M1", kpi_m1_pass)
        assert "**Decision: PASS**" in report

    def test_report_fail_decision(self, reporter):
        kpi = {
            "calendar_days": 5,  # primary fail
            "trades": 2,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        report = reporter.generate_report("gate_M1", kpi)
        assert "**Decision: FAIL**" in report

    def test_report_contains_capital_info(self, reporter, kpi_m1_pass):
        report = reporter.generate_report("gate_M1", kpi_m1_pass)
        assert "10,000" in report  # current capital
        assert "15,000" in report  # target capital

    def test_report_contains_all_conditions(self, reporter, kpi_m1_pass):
        report = reporter.generate_report("gate_M1", kpi_m1_pass)
        assert "min_trades" in report
        assert "min_strategies_active" in report
        assert "max_drawdown_pct" in report

    def test_report_contains_next_steps(self, reporter):
        kpi = {
            "calendar_days": 5,
            "trades": 2,
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }
        report = reporter.generate_report("gate_M1", kpi)
        assert "## Next Steps" in report


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    def test_empty_kpi_all_fail(self, reporter):
        result = reporter.evaluate_gate("gate_M1", {})
        # Empty KPI -> abort conditions fail (None not <= threshold) -> ABORT
        assert result["decision"] in ("FAIL", "ABORT")
        assert result["passed_count"] == 0

    def test_empty_kpi_conditions_show_none(self, reporter):
        result = reporter.evaluate_gate("gate_M1", {})
        for cond in result["conditions"]:
            assert cond["actual"] is None
            assert cond["passed"] is False

    def test_result_structure(self, reporter, kpi_m1_pass):
        result = reporter.evaluate_gate("gate_M1", kpi_m1_pass)
        expected_keys = {
            "gate", "description", "decision", "conditions",
            "passed_count", "total_count", "recommendation", "next_steps",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_condition_structure(self, reporter, kpi_m1_pass):
        result = reporter.evaluate_gate("gate_M1", kpi_m1_pass)
        for cond in result["conditions"]:
            assert "name" in cond
            assert "threshold" in cond
            assert "actual" in cond
            assert "passed" in cond


# =============================================================================
# GATE M1 V2 — Multi-criteria logic (HARDEN-002)
# =============================================================================

class TestGateM1V2:
    """Tests for the new multi-criteria gate_M1 evaluation logic."""

    @staticmethod
    def _base_kpi_m1_all_pass():
        """Helper: KPI dict where ALL primary, secondary, and abort conditions pass."""
        return {
            # Primary
            "calendar_days": 30,
            "trades": 25,
            "strategies_active": 5,
            "drawdown_pct": 3.0,
            "single_loss_pct": 1.0,
            "critical_bugs": 0,
            "reconciliation_errors": 0,
            # Secondary
            "sharpe_period": 0.8,
            "win_rate": 0.55,
            "profit_factor": 1.5,
            "slippage_ratio": 2.0,
            "execution_quality": 0.90,
            # Abort (safe values)
            "drawdown_abort_pct": 3.0,
            "critical_bugs_abort": 0,
            "consecutive_losing_weeks": 0,
        }

    def test_gate_m1_v2_all_pass(self, reporter):
        """All primary pass + >= 3 secondary pass -> PASS."""
        kpi = self._base_kpi_m1_all_pass()
        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "PASS"
        assert all(c["passed"] for c in result["primary_results"])
        assert result["secondary_passed_count"] >= result["secondary_required"]
        assert result["secondary_passed_count"] == 5  # all 5 secondary pass
        assert "primary_results" in result
        assert "secondary_results" in result
        assert "abort_results" in result

    def test_gate_m1_v2_conditional(self, reporter):
        """All primary pass but only 2 secondary pass -> CONDITIONAL."""
        kpi = self._base_kpi_m1_all_pass()
        # Fail 3 out of 5 secondary conditions (keep only 2 passing)
        kpi["sharpe_period"] = 0.1    # below 0.5 -> fail
        kpi["win_rate"] = 0.30        # below 0.45 -> fail
        kpi["profit_factor"] = 0.8    # below 1.2 -> fail

        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "CONDITIONAL"
        assert all(c["passed"] for c in result["primary_results"])
        assert result["secondary_passed_count"] == 2
        assert result["secondary_passed_count"] < result["secondary_required"]
        assert "Maintain" in result["recommendation"]

    def test_gate_m1_v2_fail_primary(self, reporter):
        """A primary condition fails -> FAIL (even if all secondary pass)."""
        kpi = self._base_kpi_m1_all_pass()
        kpi["trades"] = 5  # below min_trades=20 -> primary fail

        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "FAIL"
        failed_primary = [c for c in result["primary_results"] if not c["passed"]]
        assert len(failed_primary) >= 1
        assert any(c["name"] == "min_trades" for c in failed_primary)

    def test_gate_m1_v2_abort_drawdown(self, reporter):
        """Drawdown > 8.0% abort threshold -> ABORT."""
        kpi = self._base_kpi_m1_all_pass()
        kpi["drawdown_abort_pct"] = 9.5  # above 8.0 -> abort

        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "ABORT"
        abort_fired = [r for r in result["abort_results"] if not r["passed"]]
        assert any(r["name"] == "max_drawdown_abort_pct" for r in abort_fired)
        # Abort should mention stop trading
        assert any("stop" in s.lower() for s in result["next_steps"])

    def test_gate_m1_v2_abort_bugs(self, reporter):
        """Critical bugs abort > 0 -> ABORT."""
        kpi = self._base_kpi_m1_all_pass()
        kpi["critical_bugs_abort"] = 1  # above 0 -> abort

        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "ABORT"
        abort_fired = [r for r in result["abort_results"] if not r["passed"]]
        assert any(r["name"] == "max_critical_bugs_abort" for r in abort_fired)

    def test_gate_m1_v2_abort_losing_weeks(self, reporter):
        """3 consecutive losing weeks -> ABORT."""
        kpi = self._base_kpi_m1_all_pass()
        kpi["consecutive_losing_weeks"] = 4  # above 3 -> abort

        result = reporter.evaluate_gate("gate_M1", kpi)

        assert result["decision"] == "ABORT"
        abort_fired = [r for r in result["abort_results"] if not r["passed"]]
        assert any(r["name"] == "max_consecutive_losing_weeks" for r in abort_fired)

    def test_gate_m1_v2_abort_overrides_primary_fail(self, reporter):
        """Abort takes priority even when primary also fails."""
        kpi = self._base_kpi_m1_all_pass()
        kpi["trades"] = 5              # primary fail
        kpi["drawdown_abort_pct"] = 10  # abort fires

        result = reporter.evaluate_gate("gate_M1", kpi)
        assert result["decision"] == "ABORT"

    def test_gate_m1_v2_backward_compat(self, reporter):
        """M2 and M3 still use legacy flat evaluation (backward compatibility)."""
        # gate_M2 with passing KPIs
        kpi_m2 = {
            "trades": 150,
            "sharpe_60d": 1.0,
            "drawdown_pct": 3.0,
            "cost_ratio": 0.20,
            "live_vs_paper_sharpe_ratio": 0.7,
        }
        result_m2 = reporter.evaluate_gate("gate_M2", kpi_m2)
        assert result_m2["decision"] == "PASS"
        assert result_m2["passed_count"] == result_m2["total_count"]
        # Legacy result should NOT have multi-criteria keys
        assert "primary_results" not in result_m2
        assert "secondary_results" not in result_m2
        assert "abort_results" not in result_m2

        # gate_M3 with passing KPIs
        kpi_m3 = {
            "trades": 200,
            "sharpe_90d": 1.2,
            "drawdown_pct": 3.0,
            "strategies_live": 8,
        }
        result_m3 = reporter.evaluate_gate("gate_M3", kpi_m3)
        assert result_m3["decision"] == "PASS"
        assert result_m3["passed_count"] == result_m3["total_count"]
        assert "primary_results" not in result_m3
