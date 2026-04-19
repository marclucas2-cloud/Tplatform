"""Walk-forward canonical runner (Phase 9 XXL plan).

Goal: ONE canonical WF interface to replace the 20+ ad-hoc wf_*.py scripts that
produce inconsistent / buggy results (cf wf_crypto_all.py which was B&H-adjusted
not a real backtest, P0.2 audit 2026-04-18).

Standardized:
- Parameter contract: n_windows, train_pct, test_pct, min_trades_per_window, seed
- Output schema (dict + JSON manifest):
    {
      "schema_version": 1,
      "run_id": uuid,
      "started_at": ISO,
      "finished_at": ISO,
      "strategy_id": str,
      "params": {...},
      "n_windows": int,
      "windows": [
        {"window_idx": int, "train_start": ISO, "train_end": ISO,
         "test_start": ISO, "test_end": ISO, "metrics": {...}, "trades": int,
         "verdict": "PASS"|"FAIL"|"INSUFFICIENT_TRADES"},
         ...
      ],
      "summary": {
        "windows_pass": int, "windows_total": int,
        "median_sharpe": float, "median_dd": float,
        "verdict": "VALIDATED"|"REJECTED"|"INSUFFICIENT_TRADES",
      },
      "env_capture": {"git_sha": str, "python": str, "platform": str}
    }
- Reproducibility: seed-locked random generator + env_capture in manifest
- Verdict rule: VALIDATED iff windows_pass / windows_total >= 0.5 AND median Sharpe > 0

This module is the gateway. Existing wf_*.py scripts can migrate over time.
"""
from __future__ import annotations

import json
import logging
import math
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

WF_SCHEMA_VERSION = 2  # v2: + Deflated Sharpe p-value + grade (S/A/B) classification
DEFAULT_N_WINDOWS = 5
DEFAULT_TRAIN_PCT = 0.70
DEFAULT_TEST_PCT = 0.30
MIN_TRADES_FOR_VALID_WINDOW = 5

# Base gate (backwards-compat): at least half windows PASS and median Sharpe > 0
PASS_RATE_FOR_VALIDATED = 0.5
MIN_SHARPE_FOR_VALIDATED = 0.0

# Deflated Sharpe — Bailey & Lopez de Prado 2014
DSR_PVALUE_THRESHOLD_S = 0.05   # S-grade: strong evidence (95% confidence)
DSR_PVALUE_THRESHOLD_A = 0.10   # A-grade: moderate evidence (90% confidence)

# Grade tiers (used by promotion_gate fast-track)
S_GRADE_PASS_RATE = 0.80        # >= 4/5 windows PASS
S_GRADE_MEDIAN_SHARPE = 1.0
A_GRADE_PASS_RATE = 0.60        # >= 3/5 windows PASS
A_GRADE_MEDIAN_SHARPE = 0.5

# Default number of trials assumed when caller does not pass n_trials (conservative = 1 = no deflation)
DEFAULT_N_TRIALS = 1


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def compute_deflated_sharpe_pvalue(
    sharpe: float,
    n_observations: int,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe p-value (Bailey & Lopez de Prado 2014).

    Tests H0: true Sharpe <= 0 given we tried n_trials strategies and observed
    the maximum Sharpe = `sharpe`. Returns p-value. Low p-value (<0.05) =
    strong evidence that the observed edge is NOT a fluke from multiple testing.

    Args:
        sharpe: observed Sharpe ratio (annualized or raw — caller consistent)
        n_observations: sample size (T)
        n_trials: number of strategies tested before selecting this one (N)
        skewness: sample skewness (default 0 = assume normal)
        kurtosis: sample kurtosis (default 3 = normal)

    Returns:
        p-value in [0, 1]. Small = edge is significant, large = probably noise.
    """
    if n_observations < 2 or sharpe == 0.0:
        return 1.0  # cannot distinguish from null

    # Expected max Sharpe under null (strategies with true Sharpe = 0):
    # E[SR*] ≈ (1-γ) * Φ^-1(1 - 1/N) + γ * Φ^-1(1 - 1/(N*e))
    # Simplified: sqrt(2*ln(N)) for large N — standard extreme value approximation
    euler_mascheroni = 0.5772156649
    if n_trials <= 1:
        expected_max = 0.0
    else:
        try:
            term1 = math.sqrt(2.0 * math.log(n_trials)) if n_trials > 1 else 0.0
            # Refinement via Euler-Mascheroni correction
            expected_max = term1 - (euler_mascheroni / term1) if term1 > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            expected_max = 0.0

    # Variance of Sharpe estimator: 1 + (skew²/4 - γ4*kurt/4) * SR + ((γ4-1)/4) * SR²
    # Simplified standard form:
    sr2 = sharpe * sharpe
    var_term = max(1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * sr2, 1e-9)
    denom = math.sqrt(var_term / max(n_observations - 1, 1))
    if denom <= 0:
        return 1.0

    z = (sharpe - expected_max) / denom
    # Deflated Sharpe = Φ(z). p-value = 1 - DSR (probability edge is real)
    dsr = _normal_cdf(z)
    return max(0.0, min(1.0, 1.0 - dsr))


def classify_grade(
    pass_rate: float,
    median_sharpe: float,
    dsr_pvalue: float | None = None,
) -> str:
    """Classify a WF result as S-grade / A-grade / B-grade / REJECTED.

    S-grade: >=80% windows PASS AND median Sharpe >=1.0 [AND DSR p<=0.05 if provided]
    A-grade: >=60% windows PASS AND median Sharpe >=0.5 [AND DSR p<=0.10 if provided]
    B-grade: >=50% windows PASS AND median Sharpe >0.0 (legacy VALIDATED)
    REJECTED: else

    DSR is optional: if dsr_pvalue is None (caller did not pass n_bars_oos),
    DSR check is skipped. S/A tiers still require strong pass_rate + Sharpe.

    S-grade unlocks promotion_gate fast-track (14j paper instead of 30j).
    """
    dsr_s_ok = (dsr_pvalue is None) or (dsr_pvalue <= DSR_PVALUE_THRESHOLD_S)
    dsr_a_ok = (dsr_pvalue is None) or (dsr_pvalue <= DSR_PVALUE_THRESHOLD_A)
    if (pass_rate >= S_GRADE_PASS_RATE
            and median_sharpe >= S_GRADE_MEDIAN_SHARPE
            and dsr_s_ok):
        return "S"
    if (pass_rate >= A_GRADE_PASS_RATE
            and median_sharpe >= A_GRADE_MEDIAN_SHARPE
            and dsr_a_ok):
        return "A"
    if pass_rate >= PASS_RATE_FOR_VALIDATED and median_sharpe > MIN_SHARPE_FOR_VALIDATED:
        return "B"
    return "REJECTED"


@dataclass
class WindowResult:
    window_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    metrics: dict
    n_trades: int
    verdict: str  # "PASS" | "FAIL" | "INSUFFICIENT_TRADES"


@dataclass
class WFRunResult:
    run_id: str
    strategy_id: str
    params: dict
    windows: list[WindowResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def windows_pass(self) -> int:
        return sum(1 for w in self.windows if w.verdict == "PASS")

    @property
    def windows_total(self) -> int:
        return len(self.windows)

    @property
    def median_sharpe(self) -> float:
        sharpes = [w.metrics.get("sharpe", 0.0) for w in self.windows
                   if w.verdict != "INSUFFICIENT_TRADES"]
        if not sharpes:
            return 0.0
        sharpes.sort()
        n = len(sharpes)
        return sharpes[n // 2] if n % 2 else (sharpes[n // 2 - 1] + sharpes[n // 2]) / 2

    @property
    def median_dd(self) -> float:
        dds = [w.metrics.get("max_dd_pct", 0.0) for w in self.windows
               if w.verdict != "INSUFFICIENT_TRADES"]
        if not dds:
            return 0.0
        dds.sort()
        n = len(dds)
        return dds[n // 2] if n % 2 else (dds[n // 2 - 1] + dds[n // 2]) / 2

    @property
    def _pass_rate(self) -> float:
        non_insufficient = [w for w in self.windows if w.verdict != "INSUFFICIENT_TRADES"]
        if not non_insufficient:
            return 0.0
        return self.windows_pass / len(non_insufficient)

    @property
    def dsr_pvalue(self) -> float | None:
        """Deflated Sharpe p-value. Computed only if caller passed `n_bars_oos`
        (and optionally `n_trials`) in extra_params. None = DSR not computed."""
        non_insufficient = [w for w in self.windows if w.verdict != "INSUFFICIENT_TRADES"]
        if not non_insufficient:
            return None
        n_bars_oos = self.params.get("n_bars_oos")
        if n_bars_oos is None:
            # Fallback: sum bars across windows if each metrics dict has it
            bars = [w.metrics.get("n_bars_oos") for w in non_insufficient
                    if w.metrics.get("n_bars_oos")]
            if bars:
                n_bars_oos = sum(bars)
        if not n_bars_oos:
            return None
        n_trials = int(self.params.get("n_trials", DEFAULT_N_TRIALS))
        return compute_deflated_sharpe_pvalue(
            sharpe=self.median_sharpe,
            n_observations=max(int(n_bars_oos), 2),
            n_trials=max(n_trials, 1),
        )

    @property
    def grade(self) -> str:
        """S / A / B / REJECTED grade (see classify_grade)."""
        if self.windows_total == 0:
            return "REJECTED"
        non_insufficient = [w for w in self.windows if w.verdict != "INSUFFICIENT_TRADES"]
        if len(non_insufficient) < 3:
            return "REJECTED"
        return classify_grade(self._pass_rate, self.median_sharpe, self.dsr_pvalue)

    @property
    def verdict(self) -> str:
        if self.windows_total == 0:
            return "INSUFFICIENT_TRADES"
        non_insufficient = [w for w in self.windows if w.verdict != "INSUFFICIENT_TRADES"]
        if len(non_insufficient) < 3:
            return "INSUFFICIENT_TRADES"
        g = self.grade
        return "VALIDATED" if g in ("S", "A", "B") else "REJECTED"

    def to_dict(self) -> dict:
        return {
            "schema_version": WF_SCHEMA_VERSION,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "strategy_id": self.strategy_id,
            "params": self.params,
            "n_windows": self.windows_total,
            "windows": [
                {
                    "window_idx": w.window_idx,
                    "train_start": w.train_start,
                    "train_end": w.train_end,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "metrics": w.metrics,
                    "n_trades": w.n_trades,
                    "verdict": w.verdict,
                }
                for w in self.windows
            ],
            "summary": {
                "windows_pass": self.windows_pass,
                "windows_total": self.windows_total,
                "pass_rate": round(self._pass_rate, 4),
                "median_sharpe": self.median_sharpe,
                "median_dd": self.median_dd,
                "dsr_pvalue": (round(self.dsr_pvalue, 4)
                               if self.dsr_pvalue is not None else None),
                "grade": self.grade,
                "verdict": self.verdict,
            },
            "env_capture": _capture_env(),
        }

    def write_manifest(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
        path = output_dir / f"{self.strategy_id}_{date_str}_{self.run_id[:8]}.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        return path


def _capture_env() -> dict:
    """Snapshot reproducibility-relevant env."""
    git_sha = "unknown"
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return {
        "git_sha": git_sha,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def run_walk_forward(
    *,
    strategy_id: str,
    data_length: int,
    backtest_window_fn: Callable[[int, int, int], dict],
    n_windows: int = DEFAULT_N_WINDOWS,
    train_pct: float = DEFAULT_TRAIN_PCT,
    test_pct: float = DEFAULT_TEST_PCT,
    seed: int = 42,
    extra_params: dict | None = None,
) -> WFRunResult:
    """Run a canonical walk-forward backtest.

    Args:
        strategy_id: canonical id (must match live_whitelist if applicable)
        data_length: number of bars in the dataset
        backtest_window_fn: callable(train_start, train_end, test_end) -> dict
            with keys: sharpe, max_dd_pct, total_pnl_usd, n_trades
        n_windows: number of WF windows
        train_pct, test_pct: fraction of total data per window (must sum > 0)
        seed: random seed for reproducibility (caller may use it)
        extra_params: any strategy-specific params, recorded in manifest

    Returns:
        WFRunResult ready to .write_manifest()
    """
    if data_length < n_windows * 30:
        raise ValueError(
            f"data_length={data_length} too short for {n_windows} windows "
            f"(need >= {n_windows * 30})"
        )
    if not (0 < train_pct < 1 and 0 < test_pct < 1):
        raise ValueError(f"train_pct={train_pct}, test_pct={test_pct} must be in (0,1)")

    params = {
        "n_windows": n_windows,
        "train_pct": train_pct,
        "test_pct": test_pct,
        "seed": seed,
        "schema_version": WF_SCHEMA_VERSION,
    }
    if extra_params:
        params.update(extra_params)

    result = WFRunResult(
        run_id=str(uuid.uuid4()),
        strategy_id=strategy_id,
        params=params,
        started_at=datetime.now(UTC).isoformat(),
    )

    # Anchored walk-forward: train grows, test slides
    window_size = data_length // n_windows
    train_size = int(window_size * train_pct / (train_pct + test_pct))
    test_size = window_size - train_size

    for i in range(n_windows):
        win_start = i * window_size
        train_start = win_start
        train_end = win_start + train_size
        test_start = train_end
        test_end = min(test_start + test_size, data_length)

        try:
            metrics = backtest_window_fn(train_start, train_end, test_end)
        except Exception as exc:
            logger.error(f"Window {i} backtest_fn error: {exc}")
            metrics = {"sharpe": 0.0, "max_dd_pct": 0.0, "total_pnl_usd": 0.0, "n_trades": 0,
                       "error": str(exc)}

        n_trades = int(metrics.get("n_trades", 0))
        if n_trades < MIN_TRADES_FOR_VALID_WINDOW:
            verdict = "INSUFFICIENT_TRADES"
        else:
            sharpe = float(metrics.get("sharpe", 0.0))
            verdict = "PASS" if sharpe > MIN_SHARPE_FOR_VALIDATED else "FAIL"

        result.windows.append(WindowResult(
            window_idx=i,
            train_start=str(train_start),
            train_end=str(train_end),
            test_start=str(test_start),
            test_end=str(test_end),
            metrics=metrics,
            n_trades=n_trades,
            verdict=verdict,
        ))

    result.finished_at = datetime.now(UTC).isoformat()
    return result
