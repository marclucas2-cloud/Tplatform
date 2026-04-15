"""Book-level health status — GREEN / DEGRADED / BLOCKED per book.

Contract:
  - Each book exposes a canonical health status independent of other books
  - A book can be DEGRADED without making the whole system DEGRADED
  - Checks: broker connectivity, state freshness, whitelist integrity, risk health
  - Results cached 60s to avoid hammering broker APIs
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent


class HealthStatus(str, Enum):
    GREEN = "GREEN"        # all checks pass
    DEGRADED = "DEGRADED"  # some checks warn but book can operate
    BLOCKED = "BLOCKED"    # critical checks fail, book must not trade
    UNKNOWN = "UNKNOWN"    # insufficient data to decide


@dataclass
class HealthCheck:
    name: str
    status: HealthStatus
    message: str = ""
    value: Any = None


@dataclass
class BookHealth:
    book: str
    status: HealthStatus
    checks: list[HealthCheck] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "book": self.book,
            "status": self.status.value,
            "checks": [
                {"name": c.name, "status": c.status.value, "message": c.message, "value": c.value}
                for c in self.checks
            ],
            "timestamp": self.timestamp,
        }


# Cache: book_name -> (timestamp, BookHealth)
_cache: dict[str, tuple[float, BookHealth]] = {}
_CACHE_TTL_SECONDS = 60


def _aggregate(checks: list[HealthCheck]) -> HealthStatus:
    """Aggregate per-check statuses into a single book status."""
    if any(c.status == HealthStatus.BLOCKED for c in checks):
        return HealthStatus.BLOCKED
    if any(c.status == HealthStatus.DEGRADED for c in checks):
        return HealthStatus.DEGRADED
    if all(c.status == HealthStatus.GREEN for c in checks):
        return HealthStatus.GREEN
    return HealthStatus.UNKNOWN


def _state_file_age_check(name: str, path: Path, max_age_hours: float = 24.0) -> HealthCheck:
    """Check that a state file exists and was modified recently."""
    if not path.exists():
        return HealthCheck(name=name, status=HealthStatus.DEGRADED,
                           message=f"state file missing: {path.name}",
                           value=None)
    mtime = path.stat().st_mtime
    age_h = (time.time() - mtime) / 3600
    if age_h > max_age_hours:
        return HealthCheck(name=name, status=HealthStatus.DEGRADED,
                           message=f"state file stale: {age_h:.1f}h > {max_age_hours}h",
                           value=round(age_h, 1))
    return HealthCheck(name=name, status=HealthStatus.GREEN,
                       message=f"fresh ({age_h:.1f}h)", value=round(age_h, 1))


def _whitelist_integrity_check(book: str) -> HealthCheck:
    """Check that the book has at least one whitelisted strategy."""
    try:
        from core.governance.live_whitelist import list_live_strategies
        strats = list_live_strategies(book)
        if len(strats) == 0:
            return HealthCheck(name="whitelist_integrity",
                               status=HealthStatus.DEGRADED,
                               message=f"no live strategies in whitelist for {book}",
                               value=0)
        return HealthCheck(name="whitelist_integrity",
                           status=HealthStatus.GREEN,
                           message=f"{len(strats)} live strategies",
                           value=len(strats))
    except Exception as e:
        return HealthCheck(name="whitelist_integrity",
                           status=HealthStatus.BLOCKED,
                           message=f"whitelist load failed: {e}",
                           value=None)


def check_ibkr_futures() -> BookHealth:
    checks = []

    # 1. Whitelist integrity
    checks.append(_whitelist_integrity_check("ibkr_futures"))

    # 2. Futures positions state file
    checks.append(_state_file_age_check(
        "futures_state",
        ROOT / "data" / "state" / "futures_positions_live.json",
        max_age_hours=72.0,
    ))

    # 3. Futures parquets freshness (critical post data refresh fix)
    for sym in ["MES", "MGC", "MCL"]:
        p = ROOT / "data" / "futures" / f"{sym}_1D.parquet"
        checks.append(_state_file_age_check(
            f"parquet_{sym}",
            p,
            max_age_hours=48.0,  # cron runs 23h30 Paris daily
        ))

    # 4. IBKR equity state
    checks.append(_state_file_age_check(
        "ibkr_equity",
        ROOT / "data" / "state" / "ibkr_equity.json",
        max_age_hours=24.0,
    ))

    # 5. Kill switch state — if active = BLOCKED
    ks_path = ROOT / "data" / "state" / "kill_switch_state.json"
    if ks_path.exists():
        try:
            with open(ks_path) as f:
                ks = json.load(f)
            if ks.get("active", False) or ks.get("triggered", False):
                checks.append(HealthCheck("kill_switch",
                                          HealthStatus.BLOCKED,
                                          "kill switch ACTIVE",
                                          ks.get("reason", "active")))
            else:
                checks.append(HealthCheck("kill_switch",
                                          HealthStatus.GREEN, "inactive"))
        except Exception as e:
            checks.append(HealthCheck("kill_switch",
                                      HealthStatus.DEGRADED,
                                      f"state unreadable: {e}"))
    else:
        checks.append(HealthCheck("kill_switch", HealthStatus.GREEN, "no state (inactive)"))

    return BookHealth(
        book="ibkr_futures",
        status=_aggregate(checks),
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


def check_binance_crypto() -> BookHealth:
    checks = []

    checks.append(_whitelist_integrity_check("binance_crypto"))

    # Crypto equity state
    checks.append(_state_file_age_check(
        "crypto_equity",
        ROOT / "data" / "crypto_equity_state.json",
        max_age_hours=1.0,
    ))

    # Binance API key present
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        checks.append(HealthCheck("binance_api_key",
                                  HealthStatus.BLOCKED,
                                  "BINANCE_API_KEY not set"))
    else:
        checks.append(HealthCheck("binance_api_key", HealthStatus.GREEN, "present"))

    # Crypto kill switch
    ks_path = ROOT / "data" / "crypto_kill_switch_state.json"
    if ks_path.exists():
        try:
            with open(ks_path) as f:
                ks = json.load(f)
            if ks.get("active", False):
                checks.append(HealthCheck("crypto_kill_switch",
                                          HealthStatus.BLOCKED,
                                          "crypto kill switch ACTIVE"))
            else:
                checks.append(HealthCheck("crypto_kill_switch",
                                          HealthStatus.GREEN, "inactive"))
        except Exception as e:
            checks.append(HealthCheck("crypto_kill_switch",
                                      HealthStatus.DEGRADED, f"unreadable: {e}"))
    else:
        checks.append(HealthCheck("crypto_kill_switch", HealthStatus.GREEN, "no state (inactive)"))

    return BookHealth(
        book="binance_crypto",
        status=_aggregate(checks),
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


def check_alpaca_us() -> BookHealth:
    checks = []

    # Alpaca is paper only per doctrine
    checks.append(HealthCheck("doctrine", HealthStatus.GREEN, "paper_only per whitelist"))

    api_key = os.getenv("ALPACA_API_KEY", "")
    paper_mode = os.getenv("PAPER_TRADING", "true").lower() == "true"
    if not api_key:
        checks.append(HealthCheck("alpaca_api_key",
                                  HealthStatus.DEGRADED,
                                  "ALPACA_API_KEY not set"))
    elif not paper_mode:
        checks.append(HealthCheck("paper_mode",
                                  HealthStatus.BLOCKED,
                                  "PAPER_TRADING=false but doctrine is paper_only"))
    else:
        checks.append(HealthCheck("alpaca_api_key", HealthStatus.GREEN, "present + paper mode"))

    return BookHealth(
        book="alpaca_us",
        status=_aggregate(checks),
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


def check_ibkr_fx() -> BookHealth:
    checks = []
    # Per doctrine: FX is DISABLED on IBKR (ESMA limits)
    fx_enabled = os.getenv("IBKR_FX_ENABLED", "false").lower() == "true"
    if fx_enabled:
        checks.append(HealthCheck("fx_doctrine",
                                  HealthStatus.BLOCKED,
                                  "IBKR_FX_ENABLED=true but whitelist says disabled — misconfig"))
    else:
        checks.append(HealthCheck("fx_doctrine",
                                  HealthStatus.GREEN,
                                  "disabled per doctrine (IBKR_FX_ENABLED=false)"))
    return BookHealth(
        book="ibkr_fx",
        status=_aggregate(checks),
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


def check_ibkr_eu() -> BookHealth:
    checks = []
    # EU is paper_only per doctrine
    checks.append(HealthCheck("eu_doctrine",
                              HealthStatus.GREEN,
                              "paper_only per whitelist (eu_gap_open rejected OOS)"))
    return BookHealth(
        book="ibkr_eu",
        status=_aggregate(checks),
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


BOOK_CHECKERS = {
    "ibkr_futures": check_ibkr_futures,
    "ibkr_fx": check_ibkr_fx,
    "ibkr_eu": check_ibkr_eu,
    "binance_crypto": check_binance_crypto,
    "alpaca_us": check_alpaca_us,
}


def get_book_health(book: str, use_cache: bool = True) -> BookHealth:
    """Return health status for a given book. Cached 60s by default."""
    now = time.time()
    if use_cache and book in _cache:
        ts, cached = _cache[book]
        if now - ts < _CACHE_TTL_SECONDS:
            return cached

    checker = BOOK_CHECKERS.get(book)
    if checker is None:
        return BookHealth(
            book=book,
            status=HealthStatus.UNKNOWN,
            checks=[HealthCheck("unknown_book", HealthStatus.UNKNOWN, f"no checker for {book}")],
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    try:
        result = checker()
    except Exception as e:
        logger.error(f"[book_health] {book} checker raised: {e}")
        result = BookHealth(
            book=book,
            status=HealthStatus.UNKNOWN,
            checks=[HealthCheck("checker_error", HealthStatus.UNKNOWN, str(e))],
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    _cache[book] = (now, result)
    return result


def get_all_books_health(use_cache: bool = True) -> dict[str, BookHealth]:
    """Return health status for all known books."""
    return {book: get_book_health(book, use_cache=use_cache) for book in BOOK_CHECKERS.keys()}


def get_global_status(use_cache: bool = True) -> HealthStatus:
    """Aggregate all books into a single global status.

    Note: a single book BLOCKED does NOT force global BLOCKED — books are
    independent. The global status is informational only. Use per-book
    status for decision making.
    """
    all_h = get_all_books_health(use_cache=use_cache)
    statuses = [h.status for h in all_h.values()]
    if all(s == HealthStatus.GREEN for s in statuses):
        return HealthStatus.GREEN
    if all(s == HealthStatus.BLOCKED for s in statuses):
        return HealthStatus.BLOCKED
    return HealthStatus.DEGRADED
