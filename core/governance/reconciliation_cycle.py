"""Periodic reconciliation cycle (Phase 6 XXL).

Wraps reconcile_book() for all configured books and:
1. Persists report to data/reconciliation/{book}_{YYYY-MM-DD}.json
2. Sends Telegram alert if divergences detected (severity matrix below)
3. Emits metrics for dashboard / auto_demote

Severity matrix:
- only_in_local : phantom position (local thinks open, broker doesn't) -> CRITICAL
                  Likely cause: order failed silently, never adopted into broker.
- only_in_broker: orphan position (broker has it, local doesn't track) -> CRITICAL
                  Likely cause: manual trade, or worker missed a fill event.
- state_file_corrupted : critical alert + auto-failover (caller should reload)
- error : broker query failure -> WARNING (transient may auto-heal next cycle)

Integration in worker.py:

    from core.governance.reconciliation_cycle import run_reconciliation_cycle
    scheduler.add_job(
        lambda: run_reconciliation_cycle(
            books=("binance_crypto", "ibkr_futures", "alpaca_us"),
            alert_callback=_send_alert,
            metrics_callback=_metrics.gauge,
        ),
        trigger="interval", minutes=15, id="reconciliation",
    )
"""
from __future__ import annotations

import logging
from typing import Callable

from core.governance.reconciliation import (
    reconcile_book,
    save_reconciliation_report,
)

logger = logging.getLogger(__name__)


def _is_paper_only(book_id: str) -> bool:
    """Check if book is paper_only in books_registry (affects alert severity)."""
    try:
        from pathlib import Path
        import yaml
        root = Path(__file__).resolve().parent.parent.parent
        registry = root / "config" / "books_registry.yaml"
        if not registry.exists():
            return False
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
        for b in data.get("books", []) or []:
            if b.get("book_id") == book_id:
                return b.get("mode_authorized") == "paper_only"
    except Exception:
        pass
    return False


def run_reconciliation_cycle(
    books: tuple[str, ...] = ("binance_crypto", "ibkr_futures", "alpaca_us", "ibkr_eu"),
    alert_callback: Callable[[str, str], None] | None = None,
    metrics_callback: Callable[[str, float, dict], None] | None = None,
) -> dict[str, dict]:
    """Run reconciliation against each book + alert on divergences.

    Returns dict {book_id: result}. Never raises (per-book exceptions captured).
    """
    out: dict[str, dict] = {}
    for book_id in books:
        try:
            result = reconcile_book(book_id)
        except (ValueError, Exception) as exc:
            logger.error(f"reconciliation cycle error on {book_id}: {exc}")
            result = {
                "book": book_id,
                "error": f"reconcile_book exception: {exc}",
                "divergences": [],
            }
        out[book_id] = result

        try:
            save_reconciliation_report(result)
        except Exception as exc:
            logger.warning(f"save_reconciliation_report failed for {book_id}: {exc}")

        # Determine book mode (live vs paper) to tune severity. paper_only books
        # are expected to have local_positions (simulation) without real broker
        # positions — this is NOT critical, just informational/warning.
        is_paper_book = _is_paper_only(book_id)

        # Alert on divergences using severity matrix
        if alert_callback is not None:
            for div in result.get("divergences", []):
                dtype = div.get("type", "unknown")
                if dtype in ("only_in_broker", "only_in_local"):
                    syms = div.get("symbols", [])
                    if is_paper_book:
                        # Paper book simulation is locally-maintained, not pushed
                        # to broker. Divergence expected — warning only.
                        severity = "warning"
                        label = "RECONCILIATION INFO"
                        msg = (
                            f"{label} [{book_id} paper_only] {dtype}: "
                            f"symbols={syms}. Local simulation only, no broker push."
                        )
                    else:
                        severity = "critical"
                        label = "RECONCILIATION CRITICAL"
                        msg = (
                            f"{label} [{book_id}] {dtype}: "
                            f"symbols={syms}. Manual reconcile needed."
                        )
                    try:
                        alert_callback(msg, severity)
                    except Exception as exc:
                        logger.warning(f"alert_callback error: {exc}")
                    # F2 plan 9.0: persist incident in JSONL timeline for post-mortem
                    try:
                        from core.monitoring.incident_report import log_incident_auto
                        log_incident_auto(
                            category="reconciliation",
                            severity=severity,
                            source="reconciliation_cycle",
                            message=msg,
                            context={
                                "book": book_id,
                                "book_mode": "paper_only" if is_paper_book else "live_allowed",
                                "divergence_type": dtype,
                                "symbols": syms,
                                "broker_positions": result.get("broker_positions", []),
                                "local_positions": result.get("local_positions", []),
                            },
                        )
                    except Exception:
                        pass
                elif dtype == "state_file_corrupted":
                    try:
                        alert_callback(
                            f"RECONCILIATION [{book_id}]: state file corrupted - "
                            f"{div.get('err')}",
                            "critical",
                        )
                    except Exception as exc:
                        logger.warning(f"alert_callback error: {exc}")

        # Soft warning if broker query errored (transient)
        if result.get("error") and alert_callback is not None:
            try:
                alert_callback(
                    f"RECONCILIATION [{book_id}] broker query failed: {result['error']}",
                    "warning",
                )
            except Exception as exc:
                logger.warning(f"alert_callback error: {exc}")

        # Metrics
        if metrics_callback is not None:
            try:
                metrics_callback(
                    f"reconciliation.{book_id}.divergences",
                    float(len(result.get("divergences", []))),
                    {"book": book_id},
                )
                metrics_callback(
                    f"reconciliation.{book_id}.broker_positions",
                    float(len(result.get("broker_positions", []))),
                    {"book": book_id},
                )
            except Exception as exc:
                logger.debug(f"metrics_callback error: {exc}")

    return out
