#!/usr/bin/env python3
"""Find strategy modules with no imports in production code.

Usage: python scripts/find_unused_strategies.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {"__pycache__", ".claude", "worktrees", "_archive"}
EXCLUDE_PREFIXES = ("tests/", "tests\\")


def file_to_module(p: Path) -> str:
    rel = p.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def collect_strategy_modules() -> list[str]:
    modules = []
    for base in ("strategies", "strategies_v2"):
        base_path = ROOT / base
        if not base_path.exists():
            continue
        for p in base_path.rglob("*.py"):
            if p.name == "__init__.py":
                continue
            if any(d in str(p) for d in EXCLUDE_DIRS):
                continue
            modules.append(file_to_module(p))
    return sorted(modules)


def is_imported(mod: str) -> tuple[bool, str | None]:
    """Search for 'import mod' or 'from mod import' anywhere in production code."""
    pat_from = f"from {mod} import"
    pat_imp = f"import {mod}"
    for p in ROOT.rglob("*.py"):
        if any(d in p.parts for d in EXCLUDE_DIRS):
            continue
        relstr = str(p.relative_to(ROOT))
        if relstr.startswith(EXCLUDE_PREFIXES):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pat_from in src or pat_imp in src:
            # Skip self
            mod_path = ROOT.joinpath(*mod.split("."))
            if p.with_suffix("") == mod_path:
                continue
            return True, str(p.relative_to(ROOT))
    return False, None


def main() -> int:
    modules = collect_strategy_modules()
    print(f"Total strategy modules: {len(modules)}")
    print()
    print("UNUSED (no imports in production code, excl tests/archives):")
    print("=" * 70)
    unused = []
    for mod in modules:
        used, where = is_imported(mod)
        if not used:
            unused.append(mod)
            print(f"  {mod}")
    print()
    print(f"Total unused: {len(unused)} / {len(modules)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
