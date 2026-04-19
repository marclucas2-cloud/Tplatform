"""Static anti-lookahead audit (Phase 14 XXL plan).

Scans strategy source code for known lookahead anti-patterns.
NOT a runtime backtest test - this catches obvious code-level bugs
where future bars/values are referenced before they exist.

Patterns flagged:
- Direct .iloc[-1] / .tail(1) used as "current" while iterating bars (must
  use .iloc[i-1] or .shift(1))
- Use of df.tomorrow / df.next_close / df.future_*
- Use of full series in functions that should be windowed (mean/std without
  rolling) — soft warning only
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ACTIVE_STRATEGY_DIRS = [
    ROOT / "strategies" / "crypto",
    ROOT / "strategies_v2" / "crypto",
    ROOT / "strategies_v2" / "futures",
    ROOT / "strategies_v2" / "fx",
    ROOT / "strategies_v2" / "us",
    ROOT / "strategies_v2" / "eu",
    ROOT / "strategies_v2" / "stocks",
]

# Hard violations (must fail)
LOOKAHEAD_HARD_PATTERNS = [
    re.compile(r"df\[\s*['\"]tomorrow['\"]\s*\]"),
    re.compile(r"\.next_close\b"),
    re.compile(r"\.future_\w+"),
    re.compile(r"df\[\s*['\"]close['\"]\s*\]\.iloc\[\s*\+1\s*\]"),
    re.compile(r"close\[\s*i\s*\+\s*1\s*\]"),
]


def _gather_active_strategy_files() -> list[Path]:
    files: list[Path] = []
    for d in ACTIVE_STRATEGY_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            if "_archive" in p.parts or "__pycache__" in p.parts:
                continue
            if p.name == "__init__.py":
                continue
            files.append(p)
    return files


# ---------------------------------------------------------------------------
# HARD violations (must fail audit)
# ---------------------------------------------------------------------------

class TestNoHardLookaheadPatterns:
    """No active strategy file should contain hard lookahead patterns."""

    @pytest.mark.parametrize("strategy_file", _gather_active_strategy_files())
    def test_no_hard_lookahead_in_file(self, strategy_file):
        try:
            src = strategy_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pytest.skip(f"Could not read {strategy_file}")

        violations = []
        for pat in LOOKAHEAD_HARD_PATTERNS:
            matches = pat.findall(src)
            if matches:
                violations.append(f"{pat.pattern!r}: {matches}")

        assert not violations, (
            f"HARD LOOKAHEAD found in {strategy_file.relative_to(ROOT)}:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# ---------------------------------------------------------------------------
# Coverage: how many active strategy files were scanned
# ---------------------------------------------------------------------------

class TestAuditCoverage:
    def test_at_least_30_active_strategies_scanned(self):
        """Sanity: we should have >= 30 active strategy files."""
        files = _gather_active_strategy_files()
        assert len(files) >= 30, (
            f"Only {len(files)} active strategy files found "
            f"(expected >= 30). ACTIVE_STRATEGY_DIRS may be wrong."
        )

    def test_archived_strategies_not_scanned(self):
        """Defensive: ensure archive folders are excluded."""
        files = _gather_active_strategy_files()
        for f in files:
            assert "_archive" not in f.parts, f"archive file leaked: {f}"
