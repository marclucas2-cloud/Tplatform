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
import random
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _state_file_age_check_any(
    name: str,
    paths: list[Path],
    max_age_hours: float = 24.0,
) -> HealthCheck:
    """Check a list of possible paths and use the freshest existing file."""
    existing = [p for p in paths if p.exists()]
    if not existing:
        return HealthCheck(
            name=name,
            status=HealthStatus.DEGRADED,
            message=f"state file missing: {paths[0].name}",
            value=None,
        )
    freshest = max(existing, key=lambda p: p.stat().st_mtime)
    check = _state_file_age_check(name, freshest, max_age_hours=max_age_hours)
    check.message = f"{freshest.name}: {check.message}"
    return check


def _tcp_connect_check(
    name: str,
    host: str,
    port: int,
    timeout_seconds: float = 2.0,
) -> HealthCheck:
    """Fast TCP connectivity check for broker gateways."""
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return HealthCheck(
                name=name,
                status=HealthStatus.GREEN,
                message=f"{host}:{port} reachable",
                value=f"{host}:{port}",
            )
    except Exception as e:
        return HealthCheck(
            name=name,
            status=HealthStatus.BLOCKED,
            message=f"{host}:{port} unreachable: {e}",
            value=f"{host}:{port}",
        )


def _quick_ibkr_snapshot(
    host: str,
    port: int,
    client_id: int | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[dict | None, list[dict] | None, str | None]:
    """Query IBKR once without the adapter reconnect backoff.

    Returns (info, positions, error_code). error_code is structured:
      - "ib_insync_unavailable": library missing
      - "connect_timeout": gateway didn't accept connection in time
      - "connect_refused:<msg>": gateway refused (often 2FA pending)
      - "no_managed_accounts": connected but managedAccounts() empty
      - "empty_summary:<account>": account known but summary returned no fields
      - "snapshot_error:<exc>": other unexpected exception
      - None: success
    """
    import asyncio

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    try:
        from ib_insync import IB
    except ImportError as e:
        return None, None, f"ib_insync_unavailable:{e}"

    cid = client_id or random.randint(780, 789)
    ib = IB()
    ib.RequestTimeout = timeout_seconds
    info: dict | None = None
    positions: list[dict] | None = None
    try:
        try:
            ib.connect(host, port, clientId=cid, timeout=timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            return None, None, f"connect_timeout:host={host},port={port},cid={cid},t={timeout_seconds}s"
        except ConnectionRefusedError as e:
            return None, None, f"connect_refused:{e}"
        except Exception as e:
            return None, None, f"connect_error:{type(e).__name__}:{e}"

        accounts = ib.managedAccounts()
        if not accounts:
            return None, None, f"no_managed_accounts:host={host},port={port},cid={cid}"

        account_id = accounts[0]
        try:
            summary = {tag.tag: tag.value for tag in ib.accountSummary(account_id)}
        except Exception as e:
            return None, None, f"summary_fetch_error:account={account_id}:{e}"

        if not summary:
            return None, None, f"empty_summary:account={account_id}"

        info = {
            "account_number": account_id,
            "equity": float(summary.get("NetLiquidation", 0) or 0),
            "cash": float(summary.get("TotalCashValue", 0) or 0),
            "buying_power": float(summary.get("BuyingPower", 0) or 0),
            "currency": summary.get("Currency", "USD"),
        }

        positions = []
        try:
            for pos in ib.positions():
                if abs(float(pos.position)) <= 0:
                    continue
                positions.append(
                    {
                        "symbol": pos.contract.symbol,
                        "qty": float(pos.position),
                        "avg_entry": float(getattr(pos, "avgCost", 0) or 0),
                    }
                )
        except Exception as e:
            # Positions optional — log but don't fail snapshot if account info ok
            logger.debug(f"_quick_ibkr_snapshot positions error: {e}")
            positions = []

        return info, positions, None
    except Exception as e:
        return None, None, f"snapshot_error:{type(e).__name__}:{e}"
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _data_freshness_checks(book: str) -> list[HealthCheck]:
    """Expand canonical data freshness checks from the shared module."""
    try:
        from core.governance.data_freshness import check_data_freshness
        fresh, details = check_data_freshness(book)
    except Exception as e:
        return [HealthCheck("data_freshness", HealthStatus.DEGRADED, f"check error: {e}")]

    if "note" in details:
        return [HealthCheck("data_freshness", HealthStatus.GREEN, str(details["note"]))]

    checks: list[HealthCheck] = []
    for rel_path, meta in details.items():
        name = f"data::{Path(rel_path).name}"
        status_name = meta.get("status")
        if status_name == "fresh":
            checks.append(
                HealthCheck(
                    name=name,
                    status=HealthStatus.GREEN,
                    message=f"fresh ({meta.get('age_hours', '?')}h <= {meta.get('max_hours', '?')}h)",
                    value=meta.get("age_hours"),
                )
            )
        else:
            msg = "missing" if status_name == "missing" else f"stale ({meta.get('age_hours', '?')}h > {meta.get('max_hours', '?')}h)"
            checks.append(
                HealthCheck(
                    name=name,
                    status=HealthStatus.DEGRADED,
                    message=msg,
                    value=meta.get("age_hours"),
                )
            )
    return checks


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

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    gateway_check = _tcp_connect_check("ibkr_gateway", host, port, timeout_seconds=2.0)
    checks.append(gateway_check)

    snapshot_info = None
    snapshot_positions = None
    snapshot_error = None
    if gateway_check.status == HealthStatus.GREEN:
        snapshot_info, snapshot_positions, snapshot_error = _quick_ibkr_snapshot(
            host=host,
            port=port,
            client_id=78,
            timeout_seconds=5.0,
        )
        if snapshot_info and float(snapshot_info.get("equity", 0) or 0) > 0:
            checks.append(
                HealthCheck(
                    "ibkr_account",
                    HealthStatus.GREEN,
                    f"authenticated (equity={snapshot_info.get('equity')}, account={snapshot_info.get('account_number')})",
                    snapshot_info.get("equity"),
                )
            )
        else:
            # Map structured error code -> status. Some errors are transient
            # (timeout, account empty during 2FA refresh) -> DEGRADED, not BLOCKED.
            err = snapshot_error or "unknown"
            if err.startswith(("connect_timeout", "summary_fetch_error", "no_managed_accounts")):
                # Transient - keep book operational with stale state
                status = HealthStatus.DEGRADED
                msg = f"snapshot transient failure ({err}); using local state"
            elif err.startswith(("connect_refused", "ib_insync_unavailable")):
                # Hard failure - book cannot operate
                status = HealthStatus.BLOCKED
                msg = f"snapshot hard failure ({err})"
            else:
                # Empty summary or unknown - DEGRADED, not BLOCKED
                status = HealthStatus.DEGRADED
                msg = f"snapshot incomplete ({err})"
            checks.append(HealthCheck("ibkr_account", status, msg, err))
    else:
        checks.append(
            HealthCheck(
                "ibkr_account",
                HealthStatus.BLOCKED,
                "skipped because gateway is unreachable",
            )
        )

    state_check = _state_file_age_check_any(
        "futures_state",
        [
            ROOT / "data" / "state" / "ibkr_futures" / "positions_live.json",
            ROOT / "data" / "state" / "futures_positions_live.json",
        ],
        max_age_hours=72.0,
    )
    if state_check.status != HealthStatus.GREEN:
        if snapshot_positions is not None and state_check.value is None:
            if len(snapshot_positions) == 0:
                state_check = HealthCheck(
                    "futures_state",
                    HealthStatus.GREEN,
                    "broker reports 0 live positions (local state optional)",
                    0,
                )
            else:
                state_check = HealthCheck(
                    "futures_state",
                    HealthStatus.DEGRADED,
                    f"state missing but broker reports {len(snapshot_positions)} live position(s)",
                    len(snapshot_positions),
                )
        elif snapshot_error:
            state_check.message += f"; broker snapshot unavailable: {snapshot_error}"
    checks.append(state_check)

    checks.extend(_data_freshness_checks("ibkr_futures"))

    equity_check = _state_file_age_check_any(
        "ibkr_equity",
        [
            ROOT / "data" / "state" / "ibkr_futures" / "equity_state.json",
            ROOT / "data" / "state" / "ibkr_equity.json",
        ],
        max_age_hours=24.0,
    )
    if equity_check.status != HealthStatus.GREEN:
        if snapshot_info and float(snapshot_info.get("equity", 0) or 0) > 0:
            equity_check = HealthCheck(
                "ibkr_equity",
                HealthStatus.GREEN,
                f"broker API ok (equity={snapshot_info.get('equity')})",
                snapshot_info.get("equity"),
            )
        elif snapshot_error:
            equity_check.message += f"; broker api unavailable: {snapshot_error}"
    checks.append(equity_check)

    # 5. Kill switch state - if active = BLOCKED
    ks_candidates = [
        ROOT / "data" / "state" / "global" / "kill_switch_state.json",
        ROOT / "data" / "state" / "kill_switch_state.json",
        ROOT / "data" / "kill_switch_state.json",
    ]
    ks_path = next((p for p in ks_candidates if p.exists()), ks_candidates[0])
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
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
    )


def check_binance_crypto() -> BookHealth:
    checks = []

    checks.append(_whitelist_integrity_check("binance_crypto"))

    equity_check = _state_file_age_check_any(
        "crypto_equity",
        [
            ROOT / "data" / "state" / "binance_crypto" / "equity_state.json",
            ROOT / "data" / "crypto_equity_state.json",
        ],
        max_age_hours=1.0,
    )
    if equity_check.status != HealthStatus.GREEN and os.getenv("BINANCE_API_KEY", ""):
        try:
            from core.broker.binance_broker import BinanceBroker
            info = BinanceBroker().get_account_info()
            if float(info.get("equity", 0) or 0) > 0:
                equity_check = HealthCheck(
                    "crypto_equity",
                    HealthStatus.GREEN,
                    f"broker API ok (equity={info.get('equity')})",
                    info.get("equity"),
                )
        except Exception as e:
            equity_check.message += f"; broker api unavailable: {e}"
    checks.append(equity_check)

    # Binance API key present
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        checks.append(HealthCheck("binance_api_key",
                                  HealthStatus.BLOCKED,
                                  "BINANCE_API_KEY not set"))
    else:
        checks.append(HealthCheck("binance_api_key", HealthStatus.GREEN, "present"))

    # Crypto kill switch
    ks_candidates = [
        ROOT / "data" / "state" / "binance_crypto" / "kill_switch_state.json",
        ROOT / "data" / "crypto_kill_switch_state.json",
    ]
    ks_path = next((p for p in ks_candidates if p.exists()), ks_candidates[0])
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
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
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
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
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
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
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
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
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
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
        )

    try:
        result = checker()
    except Exception as e:
        logger.error(f"[book_health] {book} checker raised: {e}")
        result = BookHealth(
            book=book,
            status=HealthStatus.UNKNOWN,
            checks=[HealthCheck("checker_error", HealthStatus.UNKNOWN, str(e))],
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
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
