"""Boot preflight checks for worker startup.

A5/E1/E3 plan 9.0 (2026-04-19): consolidate critical preflight into one
module called at worker startup. Fail-closed on missing/stale critical
infrastructure so the worker does not silently skip cycles.

Checks:
  - canonical registries present + parseable (books_registry.yaml,
    live_whitelist.yaml, quant_registry.yaml)
  - equity_state files for live books (E3)
  - data freshness on parquets backing live/paper strategies (A5)
  - IBKR gateway TCP ping when a live ibkr_* book is authorized (E1)

Design: returns a PreflightResult with OK/FAIL/SKIP entries. Caller decides
whether to exit. worker.py calls boot_preflight(fail_closed=True) early in
main() — any FAIL raises SystemExit(2).

Philosophy: preflight is NOT about blocking trading (that is pre_order_guard's
job). Preflight ensures the boot environment is sane BEFORE cycles schedule.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
BOOKS_REGISTRY = ROOT / "config" / "books_registry.yaml"
LIVE_WHITELIST = ROOT / "config" / "live_whitelist.yaml"
QUANT_REGISTRY = ROOT / "config" / "quant_registry.yaml"

# Maximum staleness tolerated for a parquet backing a LIVE strategy.
# Paper strats tolerate up to 168h (1 week) since retrospective cycles accept
# older data. Live strategies require fresh data to avoid deciding on stale
# quotes.
MAX_PARQUET_AGE_HOURS_LIVE = 48.0
MAX_PARQUET_AGE_HOURS_PAPER = 168.0

# Content freshness: max age in DAYS of the last visible bar via the safe loader.
# Catches the "file rewritten daily but bars frozen" bug observed 2026-03-27 -> 2026-04-24
# where mtime stayed fresh while content stalled. Stricter than mtime check.
MAX_PARQUET_CONTENT_AGE_DAYS_LIVE = 4
MAX_PARQUET_CONTENT_AGE_DAYS_PAPER = 10


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    message: str
    severity: str = "critical"    # critical | warning | info


@dataclass
class PreflightResult:
    started_at: str
    finished_at: str = ""
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def critical_failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "critical"]

    @property
    def all_passed(self) -> bool:
        return len(self.critical_failures) == 0

    def summary(self) -> str:
        lines = [
            f"=== Boot Preflight ({self.started_at})",
            f"  checks: {len(self.checks)}",
            f"  critical failures: {len(self.critical_failures)}",
            "",
        ]
        for c in self.checks:
            mark = "OK " if c.passed else "FAIL"
            sev = f"[{c.severity}]"
            lines.append(f"  {mark} {c.name} {sev}: {c.message}")
        return "\n".join(lines)


def _check_registry_file(name: str, path: Path) -> PreflightCheck:
    if not path.exists():
        return PreflightCheck(name=f"registry::{name}",
                              passed=False,
                              message=f"{path.relative_to(ROOT)} not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return PreflightCheck(name=f"registry::{name}",
                              passed=False,
                              message=f"{path.name} parse error: {e}")
    if not data:
        return PreflightCheck(name=f"registry::{name}",
                              passed=False,
                              message=f"{path.name} empty")
    return PreflightCheck(name=f"registry::{name}",
                          passed=True,
                          message=f"{path.relative_to(ROOT)} OK")


def _check_equity_state(book_id: str, is_paper: bool) -> PreflightCheck:
    """E3: require equity_state.json for live books.

    Paper books may legitimately have no equity snapshot yet (first boot).
    Live books MUST have a snapshot from the previous session so the risk
    manager has a baseline.
    """
    path = ROOT / "data" / "state" / book_id / "equity_state.json"
    if path.exists():
        return PreflightCheck(name=f"equity_state::{book_id}",
                              passed=True,
                              message=f"{path.relative_to(ROOT)} present")
    if is_paper:
        return PreflightCheck(name=f"equity_state::{book_id}",
                              passed=True,
                              severity="info",
                              message=f"{path.relative_to(ROOT)} absent (paper mode, tolerated)")
    return PreflightCheck(name=f"equity_state::{book_id}",
                          passed=False,
                          message=(
                              f"equity_state absent for live book: {path.relative_to(ROOT)}. "
                              f"Worker cannot compute DD baseline. Exit fail-closed."
                          ))


def _check_parquet_freshness(
    parquet_path: Path,
    max_age_hours: float,
    tag: str,
) -> PreflightCheck:
    if not parquet_path.exists():
        return PreflightCheck(name=f"data::{tag}",
                              passed=False,
                              severity="warning",
                              message=f"{parquet_path.name} not found")
    age_seconds = datetime.now(UTC).timestamp() - parquet_path.stat().st_mtime
    age_hours = age_seconds / 3600.0
    if age_hours > max_age_hours:
        return PreflightCheck(name=f"data::{tag}",
                              passed=False,
                              severity="warning",
                              message=(
                                  f"{parquet_path.name} stale: {age_hours:.1f}h old "
                                  f"(max {max_age_hours:.0f}h for {tag})"
                              ))
    return PreflightCheck(name=f"data::{tag}",
                          passed=True,
                          message=f"{parquet_path.name} fresh ({age_hours:.1f}h old)")


def _check_parquet_content_freshness(
    parquet_path: Path,
    max_age_days: int,
    tag: str,
) -> PreflightCheck:
    """Check the AGE of the last visible bar in the parquet (not the file mtime).

    Catches the "file rewritten daily but content stale" bug (legacy ``datetime``
    column populated with NaT for new rows -> safe loader visible last bar
    stalls while file mtime stays fresh).

    Severity = critical because a stale content silently corrupts every sleeve
    that depends on this parquet for the entire stale window.
    """
    if not parquet_path.exists():
        return PreflightCheck(
            name=f"data_content::{tag}",
            passed=True,
            severity="info",
            message=f"{parquet_path.name} absent (skipped)",
        )
    try:
        from core.data.parquet_safe_loader import parquet_content_age_days
        age_days = parquet_content_age_days(parquet_path)
    except Exception as exc:
        return PreflightCheck(
            name=f"data_content::{tag}",
            passed=False,
            severity="warning",
            message=f"{parquet_path.name} loader exception: {exc}",
        )
    if age_days is None:
        return PreflightCheck(
            name=f"data_content::{tag}",
            passed=False,
            severity="warning",
            message=f"{parquet_path.name} empty or unreadable",
        )
    if age_days > max_age_days:
        return PreflightCheck(
            name=f"data_content::{tag}",
            passed=False,
            severity="critical",
            message=(
                f"{parquet_path.name} CONTENT STALE: last visible bar "
                f"{age_days}d old (max {max_age_days}d for {tag}). File "
                f"mtime can be fresh while bars are frozen — safe loader sees "
                f"frozen content. Audit refresh script + datetime column."
            ),
        )
    return PreflightCheck(
        name=f"data_content::{tag}",
        passed=True,
        message=f"{parquet_path.name} content fresh (last bar {age_days}d old)",
    )


def _check_ibkr_gateway_tcp(host: str, port: int) -> PreflightCheck:
    """E1: quick TCP probe to IB Gateway. 3s timeout to keep preflight snappy."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=3.0):
            pass
        return PreflightCheck(name=f"ibkr_gateway::{port}",
                              passed=True,
                              severity="warning",
                              message=f"TCP {host}:{port} reachable")
    except (socket.timeout, OSError) as e:
        return PreflightCheck(name=f"ibkr_gateway::{port}",
                              passed=False,
                              severity="warning",
                              message=(
                                  f"IB Gateway TCP {host}:{port} unreachable: {e}. "
                                  f"Futures/FX cycles will skip. Check gateway + 2FA."
                              ))


def boot_preflight(
    *,
    check_equity_state: bool = True,
    check_data_freshness: bool = True,
    check_ibkr_gateway: bool = True,
    fail_closed: bool = False,
) -> PreflightResult:
    """Run all preflight checks. Returns PreflightResult.

    If fail_closed=True and any critical check fails, caller should
    sys.exit(2). If fail_closed=False (default: ops tooling mode), result is
    advisory only.
    """
    started = datetime.now(UTC).isoformat()
    result = PreflightResult(started_at=started)

    # Registries
    result.checks.append(_check_registry_file("books_registry", BOOKS_REGISTRY))
    result.checks.append(_check_registry_file("live_whitelist", LIVE_WHITELIST))
    result.checks.append(_check_registry_file("quant_registry", QUANT_REGISTRY))

    # If registries fail, bail early — downstream checks are useless
    if any(not c.passed and c.name.startswith("registry::") for c in result.checks):
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    try:
        books_data = yaml.safe_load(BOOKS_REGISTRY.read_text(encoding="utf-8")) or {}
        books = books_data.get("books", []) or []
    except Exception:
        books = []

    # Equity state per book (E3)
    if check_equity_state:
        for book in books:
            book_id = book.get("book_id")
            mode = book.get("mode_authorized", "disabled")
            if not book_id or mode == "disabled":
                continue
            is_paper = (mode != "live_allowed")
            result.checks.append(_check_equity_state(book_id, is_paper))

    # Data freshness (A5): check the few parquets backing live strategies.
    # Two checks per parquet:
    # 1. mtime freshness (file written recently)
    # 2. CONTENT freshness via safe loader (last visible bar fresh) — catches the
    #    "file rewritten but bars frozen" bug observed 2026-03-27 -> 2026-04-24.
    if check_data_freshness:
        critical_parquets = [
            # (path relative to ROOT, tag, live=True means 48h mtime / 4d content max)
            ("data/futures/MES_1D.parquet", "MES_1D", True),
            ("data/futures/MES_LONG.parquet", "MES_LONG", True),
            ("data/futures/MNQ_1D.parquet", "MNQ_1D", True),
            ("data/futures/M2K_1D.parquet", "M2K_1D", True),
            ("data/futures/MGC_1D.parquet", "MGC_1D", True),
            ("data/futures/MCL_1D.parquet", "MCL_1D", True),
            ("data/futures/VIX_1D.parquet", "VIX_1D", True),
        ]
        for rel, tag, is_live in critical_parquets:
            path = ROOT / rel
            max_hours = MAX_PARQUET_AGE_HOURS_LIVE if is_live else MAX_PARQUET_AGE_HOURS_PAPER
            max_days = (
                MAX_PARQUET_CONTENT_AGE_DAYS_LIVE if is_live
                else MAX_PARQUET_CONTENT_AGE_DAYS_PAPER
            )
            # Skip if file simply doesn't exist yet (fresh install / CI env)
            if not path.exists():
                result.checks.append(PreflightCheck(
                    name=f"data::{tag}",
                    passed=True,
                    severity="info",
                    message=f"{rel} absent (skipped, not fatal at boot)",
                ))
                continue
            result.checks.append(_check_parquet_freshness(path, max_hours, tag))
            result.checks.append(_check_parquet_content_freshness(path, max_days, tag))

    # IBKR gateway TCP probe (E1)
    if check_ibkr_gateway:
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        port_live = int(os.environ.get("IBKR_PORT", "4002"))
        any_ibkr_live = any(
            b.get("broker") == "ibkr" and b.get("mode_authorized") == "live_allowed"
            for b in books
        )
        if any_ibkr_live:
            result.checks.append(_check_ibkr_gateway_tcp(host, port_live))

    result.finished_at = datetime.now(UTC).isoformat()

    if fail_closed and not result.all_passed:
        for c in result.critical_failures:
            logger.critical(f"PREFLIGHT FAIL: {c.name}: {c.message}")
        raise SystemExit(2)

    return result
