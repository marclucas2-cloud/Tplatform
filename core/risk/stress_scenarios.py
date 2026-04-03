"""
D2-03 — Historical Stress Scenarios.

Applies known crisis scenarios to the current portfolio to estimate
worst-case losses. Validates that hard floor (10% DD) is not breached
in at least 4/6 scenarios.

Scenarios:
  1. COVID_CRASH     : -34% SPY in 23 days
  2. CHF_FLOOR       : +30% EURCHF in 1 day (carry crash)
  3. FTX_COLLAPSE    : -25% BTC in 3 days
  4. VOLMAGEDDON     : VIX +100% in 1 day
  5. CORR_SPIKE      : All cross-asset corr → 0.9 for 10 days
  6. LIQUIDITY_DRAIN : Spreads x5, volume /5 for 5 days
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = ROOT / "data" / "risk" / "stress_test_report.json"

# Scenario definitions: daily returns shocks per asset class
# Keys: FX, CRYPTO, US_EQUITY, EU_EQUITY, FUTURES
SCENARIOS = {
    "COVID_CRASH": {
        "description": "-34% SPY in 23 days (Feb-Mar 2020)",
        "days": 23,
        "daily_shocks": {
            "US_EQUITY": -0.018,    # ~-34% over 23 days
            "EU_EQUITY": -0.020,    # ~-37% DAX
            "CRYPTO": -0.025,       # -45% BTC
            "FX": -0.003,           # -6% carry pairs
            "FUTURES": -0.015,      # -30% MES
        },
    },
    "CHF_FLOOR": {
        "description": "+30% EURCHF in 1 day (Jan 2015, carry crash)",
        "days": 1,
        "daily_shocks": {
            "FX": -0.15,           # -15% on carry pairs in 1 day
            "US_EQUITY": -0.02,
            "EU_EQUITY": -0.03,
            "CRYPTO": 0.0,
            "FUTURES": -0.02,
        },
    },
    "FTX_COLLAPSE": {
        "description": "-25% BTC in 3 days (Nov 2022)",
        "days": 3,
        "daily_shocks": {
            "CRYPTO": -0.10,        # -25% over 3 days
            "US_EQUITY": -0.005,
            "EU_EQUITY": -0.005,
            "FX": -0.001,
            "FUTURES": -0.005,
        },
    },
    "VOLMAGEDDON": {
        "description": "VIX +100% in 1 day (Feb 2018)",
        "days": 1,
        "daily_shocks": {
            "US_EQUITY": -0.04,
            "EU_EQUITY": -0.03,
            "FUTURES": -0.05,
            "CRYPTO": -0.08,
            "FX": -0.01,
        },
    },
    "CORR_SPIKE": {
        "description": "All correlations → 0.9 for 10 days (risk-off)",
        "days": 10,
        "daily_shocks": {
            "US_EQUITY": -0.008,
            "EU_EQUITY": -0.008,
            "FUTURES": -0.008,
            "CRYPTO": -0.012,
            "FX": -0.005,
        },
    },
    "LIQUIDITY_DRAIN": {
        "description": "Spreads x5, volume /5 for 5 days",
        "days": 5,
        "daily_shocks": {
            "US_EQUITY": -0.006,
            "EU_EQUITY": -0.008,
            "FUTURES": -0.005,
            "CRYPTO": -0.010,
            "FX": -0.004,
        },
        "slippage_multiplier": 5.0,
    },
    # === SYNTHETIC (non-historical) scenarios — GPT audit recommendation ===
    "CORR_ONE": {
        "description": "SYNTHETIC: all correlations = 1.0 for 5 days (worst case diversification failure)",
        "days": 5,
        "daily_shocks": {
            "US_EQUITY": -0.015,
            "EU_EQUITY": -0.015,
            "FUTURES": -0.015,
            "CRYPTO": -0.015,
            "FX": -0.015,
        },
    },
    "ZERO_LIQUIDITY": {
        "description": "SYNTHETIC: zero liquidity for 3 days (no exits possible, slippage x10)",
        "days": 3,
        "daily_shocks": {
            "US_EQUITY": -0.02,
            "EU_EQUITY": -0.02,
            "FUTURES": -0.02,
            "CRYPTO": -0.03,
            "FX": -0.01,
        },
        "slippage_multiplier": 10.0,
    },
    "SLIPPAGE_EXTREME": {
        "description": "SYNTHETIC: slippage x5 on all trades for 10 days (execution breakdown)",
        "days": 10,
        "daily_shocks": {
            "US_EQUITY": -0.003,
            "EU_EQUITY": -0.003,
            "FUTURES": -0.003,
            "CRYPTO": -0.005,
            "FX": -0.002,
        },
        "slippage_multiplier": 5.0,
    },
}


@dataclass
class ScenarioResult:
    """Result for one stress scenario."""
    name: str
    description: str
    days: int
    pnl_pct: float
    pnl_usd: float
    max_dd_pct: float
    hard_floor_breached: bool   # DD > 10%
    kill_switches_triggered: int


@dataclass
class StressTestReport:
    """Full stress test report across all scenarios."""
    scenarios: list
    capital: float
    hard_floor_pct: float
    scenarios_passed: int
    scenarios_failed: int
    verdict: str                # PASS / FAIL
    timestamp: str = ""


class StressScenarioEngine:
    """Applies historical stress scenarios to current portfolio.

    Usage::

        engine = StressScenarioEngine()
        report = engine.run(
            positions_by_class={"CRYPTO": 10000, "FX": 15000, ...},
            capital=45000,
        )
    """

    def __init__(self, hard_floor_pct: float = 0.10):
        self._hard_floor = hard_floor_pct

    def run(
        self,
        positions_by_class: dict[str, float],
        capital: float,
        kelly_fraction: float = 0.25,
    ) -> StressTestReport:
        """Run all stress scenarios.

        Args:
            positions_by_class: Exposure per asset class in USD.
                e.g. {"CRYPTO": 10000, "FX": 15000, "US_EQUITY": 0}
            capital: Total portfolio capital.
            kelly_fraction: Current Kelly sizing fraction.

        Returns:
            StressTestReport with results per scenario.
        """
        results = []
        passed = 0
        failed = 0

        for name, scenario in SCENARIOS.items():
            sr = self._run_scenario(
                name, scenario, positions_by_class, capital, kelly_fraction,
            )
            results.append(asdict(sr))
            if sr.hard_floor_breached:
                failed += 1
            else:
                passed += 1

        # Verdict: must pass at least 4/6
        verdict = "PASS" if passed >= 4 else "FAIL"

        report = StressTestReport(
            scenarios=results,
            capital=round(capital, 2),
            hard_floor_pct=self._hard_floor * 100,
            scenarios_passed=passed,
            scenarios_failed=failed,
            verdict=verdict,
            timestamp=datetime.now(UTC).isoformat(),
        )

        self._save_report(report)
        return report

    def _run_scenario(
        self,
        name: str,
        scenario: dict,
        positions_by_class: dict[str, float],
        capital: float,
        kelly_fraction: float,
    ) -> ScenarioResult:
        """Simulate one stress scenario."""
        days = scenario["days"]
        shocks = scenario["daily_shocks"]
        slippage_mult = scenario.get("slippage_multiplier", 1.0)

        equity = capital
        peak = capital
        max_dd = 0.0
        kill_switches = 0

        for _ in range(days):
            daily_pnl = 0.0
            for ac, exposure in positions_by_class.items():
                shock = shocks.get(ac, 0.0)
                # Apply Kelly scaling
                daily_pnl += exposure * shock * kelly_fraction
                # Extra slippage cost
                if slippage_mult > 1.0:
                    daily_pnl -= abs(exposure) * 0.001 * (slippage_mult - 1)

            equity += daily_pnl
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak if peak > 0 else 0
            if dd < max_dd:
                max_dd = dd

            # Kill switch would trigger at various DD levels
            if dd < -0.05:
                kill_switches += 1

        pnl_total = equity - capital
        pnl_pct = pnl_total / capital * 100 if capital > 0 else 0

        return ScenarioResult(
            name=name,
            description=scenario["description"],
            days=days,
            pnl_pct=round(pnl_pct, 2),
            pnl_usd=round(pnl_total, 2),
            max_dd_pct=round(max_dd * 100, 2),
            hard_floor_breached=max_dd < -self._hard_floor,
            kill_switches_triggered=kill_switches,
        )

    def _save_report(self, report: StressTestReport) -> None:
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(report), f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save stress report: %s", e)
