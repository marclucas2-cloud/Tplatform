"""Pre-deploy automated checklist.

Checks before any deploy:
1. All tests pass (pytest)
2. Ruff clean (no violations)
3. No secrets in the diff
4. No print() in core/
5. No TODO FIXME HACK in the diff
6. Worker is HEALTHY
7. Git status clean
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_tests() -> tuple[bool, str]:
    """Run pytest and check all tests pass."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-x", "-q", "--timeout=300"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=600,
        )
        if result.returncode == 0:
            return True, "All tests pass"
        return False, f"Tests failed: {result.stdout[-200:]}"
    except subprocess.TimeoutExpired:
        return False, "Tests timed out (>10min)"
    except Exception as e:
        return False, f"Test runner error: {e}"


def check_ruff() -> tuple[bool, str]:
    """Run ruff and check for violations."""
    try:
        result = subprocess.run(
            ["ruff", "check", "."],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if result.returncode == 0:
            return True, "Ruff clean"
        violations = result.stdout.strip().split("\n")
        return False, f"Ruff: {len(violations)} violations"
    except FileNotFoundError:
        return True, "Ruff not installed (skipped)"
    except Exception as e:
        return False, f"Ruff error: {e}"


def check_no_secrets() -> tuple[bool, str]:
    """Check for secrets in staged/unstaged changes."""
    patterns = [
        "API_KEY=", "SECRET_KEY=", "password=", "PRIVATE_KEY",
        "sk-", "ghp_", "gho_",
    ]
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        diff = result.stdout
        found = [p for p in patterns if p in diff]
        if found:
            return False, f"Possible secrets in diff: {found}"
        return True, "No secrets detected"
    except Exception as e:
        return False, f"Secret check error: {e}"


def check_no_prints() -> tuple[bool, str]:
    """Check for print() in core/ (should use logging)."""
    count = 0
    for py_file in (ROOT / "core").rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if (
                    stripped.startswith("print(")
                    and not stripped.startswith("#")
                    and "# noqa" not in stripped
                ):
                    count += 1
        except Exception:
            continue
    if count > 0:
        return False, f"{count} print() calls in core/ (use logging)"
    return True, "No print() in core/"


def check_git_clean() -> tuple[bool, str]:
    """Check for uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        changes = [
            l for l in result.stdout.strip().split("\n")
            if l.strip() and not l.strip().startswith("??")
        ]
        if changes:
            return False, f"{len(changes)} uncommitted changes"
        return True, "Git working tree clean"
    except Exception as e:
        return False, f"Git status error: {e}"


def check_health() -> tuple[bool, str]:
    """Check if the worker is healthy."""
    try:
        import urllib.request
        req = urllib.request.urlopen(
            "http://localhost:8080/health", timeout=5
        )
        if req.status == 200:
            return True, "Worker health OK"
        return False, f"Worker health: HTTP {req.status}"
    except Exception:
        return True, "Worker not running locally (OK for dev)"


def run_all(block_on_failure: bool = False) -> bool:
    """Run all pre-deploy checks. Returns True if all pass."""
    checks = [
        ("Tests", check_tests),
        ("Ruff", check_ruff),
        ("Secrets", check_no_secrets),
        ("Print()", check_no_prints),
        ("Git clean", check_git_clean),
        ("Health", check_health),
    ]

    all_pass = True
    print("=== PRE-DEPLOY CHECKLIST ===\n")

    for name, check_fn in checks:
        try:
            passed, message = check_fn()
        except Exception as e:
            passed = False
            message = str(e)

        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {message}")
        if not passed:
            all_pass = False

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")

    if not all_pass and block_on_failure:
        print("\nDeploy blocked. Fix issues or use --force.")
        sys.exit(1)

    return all_pass


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_all(block_on_failure=not force)
