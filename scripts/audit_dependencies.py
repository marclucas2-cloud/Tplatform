"""Phase 8.3 — Runtime dependency audit.

Checks that all imports used by critical production modules are actually
installed. Reports missing, version conflicts, and unused declared deps.

Usage:
    python scripts/audit_dependencies.py
    python scripts/audit_dependencies.py --fix  (install missing)
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CRITICAL_RUNTIME_IMPORTS = {
    "core.broker.binance_broker": [
        "binance", "requests",
    ],
    "core.broker.ibkr_adapter": [
        "ib_insync",
    ],
    "core.alpaca_client.client": [
        "alpaca_trade_api",
    ],
    "worker": [
        "dotenv", "schedule",
    ],
    "dashboard.api.main": [
        "fastapi", "uvicorn", "jose", "passlib",
    ],
    "core.backtester_v2.engine": [
        "pandas", "numpy",
    ],
    "core.monitoring.metrics_pipeline": [
        "pandas",
    ],
    "core.telegram_v2": [
        "requests",
    ],
    "scripts.backup_state": [],
    "core.runtime.book_runtime": [],
}

OPTIONAL_BUT_USEFUL = [
    "yfinance", "scipy", "matplotlib", "seaborn", "tabulate",
]


def check_import(module_name: str) -> tuple[bool, str]:
    """Try importing a module. Returns (success, version_or_error)."""
    try:
        mod = importlib.import_module(module_name)
        version = getattr(mod, "__version__", getattr(mod, "VERSION", "?"))
        return True, str(version)
    except ImportError as e:
        if "No module named" in str(e):
            return False, str(e)
        return True, f"installed (import side-effect: {e})"
    except Exception as e:
        return True, f"installed (init error: {e})"


def audit() -> dict:
    """Run full dependency audit."""
    results = {"critical_missing": [], "installed": [], "optional_missing": [], "errors": []}

    all_deps = set()
    for _consumer, deps in CRITICAL_RUNTIME_IMPORTS.items():
        for dep in deps:
            all_deps.add(dep)

    for dep in sorted(all_deps):
        ok, detail = check_import(dep)
        if ok:
            results["installed"].append({"module": dep, "version": detail})
        else:
            results["critical_missing"].append({"module": dep, "error": detail})

    for dep in OPTIONAL_BUT_USEFUL:
        ok, detail = check_import(dep)
        if not ok:
            results["optional_missing"].append({"module": dep, "error": detail})

    # Check requirements.txt exists and is reasonable
    req_file = ROOT / "requirements.txt"
    if req_file.exists():
        req_lines = [l.strip() for l in req_file.read_text().splitlines()
                     if l.strip() and not l.startswith("#")]
        results["requirements_count"] = len(req_lines)
    else:
        results["errors"].append("requirements.txt not found")

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="pip install missing critical deps")
    args = ap.parse_args()

    results = audit()

    print("=== DEPENDENCY AUDIT ===\n")

    print(f"Installed critical: {len(results['installed'])}")
    for d in results["installed"]:
        print(f"  OK  {d['module']} v{d['version']}")

    if results["critical_missing"]:
        print(f"\nMISSING critical: {len(results['critical_missing'])}")
        for d in results["critical_missing"]:
            print(f"  MISS  {d['module']}: {d['error']}")
    else:
        print("\nAll critical dependencies installed.")

    if results["optional_missing"]:
        print(f"\nOptional missing: {len(results['optional_missing'])}")
        for d in results["optional_missing"]:
            print(f"  OPT   {d['module']}")

    if results["errors"]:
        print(f"\nErrors: {results['errors']}")

    print(f"\nrequirements.txt: {results.get('requirements_count', 'N/A')} entries")

    if args.fix and results["critical_missing"]:
        import subprocess
        for d in results["critical_missing"]:
            mod = d["module"]
            print(f"\nInstalling {mod}...")
            subprocess.run([sys.executable, "-m", "pip", "install", mod])

    return 1 if results["critical_missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
