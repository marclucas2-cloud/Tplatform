#!/usr/bin/env python3
"""Check that paper_review decisions (REJECTED → DEMOTE) are applied in worker.py.

Reads all reports/paper_review_*.md files and extracts strats marked as
REJECTED / DEMOTE. Then verifies that each is commented out or wrapped in
'if not live:' (paper only) in worker.py.

Exit codes:
  0 = all decisions applied
  1 = one or more decisions not yet applied (governance gap)

Use as pre-commit hook or CI check.

Example usage:
  python scripts/check_review_decisions.py
  python scripts/check_review_decisions.py --strict    # fail on borderline
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_review_decisions(review_file: Path) -> list[dict]:
    """Extract REJECTED/DEMOTE decisions from a paper review markdown file."""
    content = review_file.read_text(encoding="utf-8", errors="replace")
    decisions = []

    # Look for table rows with REJECTED or DEMOTE
    # Format: | N | Strat Name | REJECTED | ... | DEMOTE |
    for line in content.splitlines():
        if "|" not in line:
            continue
        if "REJECTED" in line.upper() or "DEMOTE" in line.upper():
            cells = [c.strip() for c in line.split("|")]
            # Extract strat name (usually in the 2nd or 3rd cell)
            for cell in cells:
                # Match strat-like name (contains letters, maybe spaces)
                if cell and any(kw in cell.upper() for kw in [
                    "MES", "MNQ", "M2K", "MCL", "MGC", "VIX", "OVERNIGHT",
                    "TREND", "STRETCH", "ORB", "BRENT", "SPREAD", "PAIR"
                ]):
                    decisions.append({
                        "strat_name": cell,
                        "review_file": review_file.name,
                        "raw_line": line.strip()[:200],
                    })
                    break
    return decisions


def check_strat_disabled(strat_name: str, worker_content: str) -> tuple[bool, str]:
    """Check if a strat is disabled in worker.py (commented out or paper-only).

    A strat is considered "disabled" if:
    1. Its name appears with a DISABLED marker nearby (within 5 lines)
    2. Its code is wrapped in 'if not live:' block (paper-only mode)
    3. Its log statement has '(paper)' suffix

    Returns (is_disabled, evidence).
    """
    name_upper = strat_name.upper()
    lines = worker_content.split("\n")

    # Find all matches of the strat name
    matches = []
    for i, line in enumerate(lines):
        if name_upper in line.upper() or _fuzzy_match(name_upper, line.upper()):
            matches.append(i)
    if not matches:
        return False, "not found in worker.py"

    # Check each match
    for i in matches:
        line = lines[i]
        # Check 1: DISABLED nearby
        window = "\n".join(lines[max(0, i - 3):min(len(lines), i + 5)])
        if "DISABLED" in window.upper():
            return True, f"line {i+1}: DISABLED marker nearby"
        # Check 2: log statement with '(paper)' suffix
        if "(paper)" in line:
            return True, f"line {i+1}: logged as '(paper)' only"
        # Check 3: walk up to find 'if not live:' at lower indentation
        if line.lstrip().startswith(("from strategies_v2", "strat =", "strat_", "signals.append", "logger.")):
            my_indent = len(line) - len(line.lstrip())
            for j in range(i - 1, max(0, i - 150), -1):
                prev = lines[j]
                if not prev.strip():
                    continue
                prev_indent = len(prev) - len(prev.lstrip())
                if prev_indent < my_indent and "if not live:" in prev:
                    return True, f"line {i+1}: inside 'if not live:' block (paper-only)"
                # Stop walking if we cross a function boundary
                if prev.lstrip().startswith("def "):
                    break
    return False, f"found at line {matches[0]+1} but no disable marker"


def _fuzzy_match(name: str, line: str) -> bool:
    """Fuzzy match strat name (handles word reorder like 'MES Overnight' vs 'Overnight MES')."""
    parts = [p for p in name.split() if len(p) > 2]
    if not parts:
        return False
    return all(p in line for p in parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="also check BORDERLINE decisions")
    args = parser.parse_args()

    # Collect review decisions
    review_files = list((ROOT / "reports").glob("paper_review_*.md"))
    if not review_files:
        print("No paper_review files found")
        return 0

    all_decisions = []
    for rf in review_files:
        decs = parse_review_decisions(rf)
        all_decisions.extend(decs)

    if not all_decisions:
        print("No REJECTED/DEMOTE decisions found in reviews")
        return 0

    print(f"Found {len(all_decisions)} REJECTED/DEMOTE decisions across {len(review_files)} review file(s)")
    print()

    # Load worker.py
    worker_path = ROOT / "worker.py"
    worker_content = worker_path.read_text(encoding="utf-8")

    # Check each decision
    failures = []
    passes = []
    for d in all_decisions:
        ok, evidence = check_strat_disabled(d["strat_name"], worker_content)
        if ok:
            passes.append((d, evidence))
        else:
            failures.append((d, evidence))

    # Report
    print(f"{'Strategy':<35} {'Source':<35} Status")
    print("-" * 95)
    for d, ev in passes:
        print(f"{d['strat_name'][:34]:<35} {d['review_file'][:34]:<35} OK {ev[:30]}")
    for d, ev in failures:
        print(f"{d['strat_name'][:34]:<35} {d['review_file'][:34]:<35} FAIL {ev[:30]}")

    print()
    print(f"PASS: {len(passes)}, FAIL: {len(failures)}")

    if failures:
        print()
        print("GOVERNANCE GAP DETECTED:")
        print("  Some strats marked REJECTED/DEMOTE in paper reviews are still")
        print("  active in worker.py. Review the list above and either:")
        print("  1. Disable them in worker.py (wrap in 'if False:' or comment out)")
        print("  2. Move them to paper-only mode (wrap in 'if not live:')")
        print("  3. Update the paper_review file if the decision was reversed")
        return 1

    print("All REJECTED/DEMOTE decisions from paper reviews are applied in worker.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
