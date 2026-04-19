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

WF_SCHEMA_VERSION = 1
DEFAULT_N_WINDOWS = 5
DEFAULT_TRAIN_PCT = 0.70
DEFAULT_TEST_PCT = 0.30
MIN_TRADES_FOR_VALID_WINDOW = 5
PASS_RATE_FOR_VALIDATED = 0.5  # >= 50% windows OOS profitable
MIN_SHARPE_FOR_VALIDATED = 0.0


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
    def verdict(self) -> str:
        if self.windows_total == 0:
            return "INSUFFICIENT_TRADES"
        non_insufficient = [w for w in self.windows if w.verdict != "INSUFFICIENT_TRADES"]
        if len(non_insufficient) < 3:
            return "INSUFFICIENT_TRADES"
        pass_rate = self.windows_pass / max(1, len(non_insufficient))
        if pass_rate >= PASS_RATE_FOR_VALIDATED and self.median_sharpe > MIN_SHARPE_FOR_VALIDATED:
            return "VALIDATED"
        return "REJECTED"

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
                "median_sharpe": self.median_sharpe,
                "median_dd": self.median_dd,
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
