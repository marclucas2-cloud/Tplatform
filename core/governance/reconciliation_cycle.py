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


def _get_book_meta(book_id: str) -> dict:
    """Return book metadata dict (paper_only, source_of_truth) for severity tuning.

    Keys:
      - paper_only: bool — book is in paper_only mode
      - source_of_truth: str — "simulation_local" | "broker" | "" (default)

    source_of_truth=simulation_local means broker positions are non-canonical
    for this book and reconciliation divergences are pure information,
    not even worth a WARNING level (they are expected by design).
    """
    meta = {"paper_only": False, "source_of_truth": ""}
    try:
        from pathlib import Path
        import yaml
        root = Path(__file__).resolve().parent.parent.parent
        registry = root / "config" / "books_registry.yaml"
        if not registry.exists():
            return meta
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
        for b in data.get("books", []) or []:
            if b.get("book_id") == book_id:
                meta["paper_only"] = b.get("mode_authorized") == "paper_only"
                meta["source_of_truth"] = b.get("source_of_truth", "") or ""
                return meta
    except Exception:
        pass
    return meta


def _is_paper_only(book_id: str) -> bool:
    """Back-compat shim. Prefer _get_book_meta()."""
    return _get_book_meta(book_id)["paper_only"]


def _autocleanup_only_in_local(book_id: str, symbols: list[str]) -> int:
    """Remove `symbols` from the local state file for `book_id`.

    Called when reconciliation detects only_in_local on a LIVE book :
    le broker a deja close ces positions, le local state est juste lagging.
    Avant ce cleanup auto, l'attente du futures_runner cycle (1x/jour 16h
    Paris) ou du boot reconcile pouvait laisser le state stale 21h+, ce qui
    bloquait CAM avec "already positioned" sur des positions inexistantes.

    Returns nombre de symbols effectivement retires du state.
    Safe : ne touche que la cle correspondant au symbol; pas de write si
    aucun match.
    """
    try:
        from pathlib import Path
        import json
        root = Path(__file__).resolve().parent.parent.parent

        # Map book_id -> state file (live only ; paper books pas concernes)
        STATE_PATHS = {
            "ibkr_futures": root / "data" / "state" / "futures_positions_live.json",
            # Add other live books here when reconcile_book is wired for them.
        }
        path = STATE_PATHS.get(book_id)
        if path is None or not path.exists():
            return 0

        data = json.loads(path.read_text(encoding="utf-8")) or {}
        removed = 0
        for sym in symbols:
            if sym in data:
                del data[sym]
                removed += 1
        if removed > 0:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info(
                f"reconcile auto-cleanup: removed {removed} stale symbols "
                f"from {path.name}: {symbols}"
            )
        return removed
    except Exception as exc:
        logger.warning(f"reconcile auto-cleanup failed for {book_id}: {exc}")
        return 0


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

        # Determine book mode (live vs paper) and source of truth to tune severity.
        # - live book: divergence is CRITICAL (manual reconcile needed)
        # - paper book + source_of_truth=simulation_local: divergence is INFO
        #   (broker positions are non-canonical by design, no action needed)
        # - paper book otherwise: divergence is WARNING (expected but trace-worthy)
        meta = _get_book_meta(book_id)
        is_paper_book = meta["paper_only"]
        sim_is_canonical = meta["source_of_truth"] == "simulation_local"

        # Alert on divergences using severity matrix.
        #
        # 2026-05-07 update: only_in_local downgraded from CRITICAL to WARNING
        # for live books. Rationale: only_in_local means the broker has CLOSED
        # the position (TP/SL fill, manual close) but the local state file
        # hasn't propagated yet. The risk is strictly zero — the broker is
        # canonical, the position is gone, there's nothing to manage. The
        # only consequence is the strategy can't re-open until cleanup.
        #
        # only_in_broker stays CRITICAL because it means a position exists
        # broker-side that our risk pipeline doesn't know about (orphan,
        # unprotected by our SL/TP logic). That is genuinely dangerous.
        #
        # Reproduction of the asymmetry:
        # - 2026-05-07 06:43 UTC: MGC live TP'd at $4759 (broker closed)
        # - 06:55 → 11:00 UTC: 16 CRITICAL alerts on only_in_local=['MGC']
        #   for a position that was 100% safe (closed broker-side).
        if alert_callback is not None:
            for div in result.get("divergences", []):
                dtype = div.get("type", "unknown")
                if dtype in ("only_in_broker", "only_in_local"):
                    syms = div.get("symbols", [])
                    if is_paper_book and sim_is_canonical:
                        # Source-of-truth = simulation locale, donc les positions
                        # broker sont non-canoniques par design. Pas un signal
                        # operationnel : INFO uniquement, pas de warning.
                        severity = "info"
                        label = "RECONCILIATION INFO"
                        msg = (
                            f"{label} [{book_id} paper_only non-canonical] {dtype}: "
                            f"symbols={syms}. source_of_truth=simulation_local, "
                            f"broker positions ignored by design."
                        )
                    elif is_paper_book:
                        # Paper book simulation is locally-maintained, not pushed
                        # to broker. Divergence expected — warning only.
                        severity = "warning"
                        label = "RECONCILIATION INFO"
                        msg = (
                            f"{label} [{book_id} paper_only] {dtype}: "
                            f"symbols={syms}. Local simulation only, no broker push."
                        )
                    elif dtype == "only_in_local":
                        # Live book, but the broker has already closed the
                        # position. Cosmetic state lag, not a CRO issue.
                        # Refactor 2026-05-10 : auto-cleanup le state file
                        # immediatement (le futures_runner cycle reconcile
                        # ne tourne qu'une fois par jour à 16h Paris, ce qui
                        # avait laisse le state stale 21h le 2026-05-09).
                        cleanup_done = _autocleanup_only_in_local(book_id, syms)
                        severity = "warning"
                        label = "RECONCILIATION WARNING"
                        cleanup_suffix = (
                            f" -> auto-cleanup OK ({cleanup_done} symbols)"
                            if cleanup_done > 0
                            else " (no cleanup needed or path unsupported)"
                        )
                        msg = (
                            f"{label} [{book_id}] only_in_local: "
                            f"symbols={syms}. Broker has CLOSED these positions"
                            f"{cleanup_suffix}."
                        )
                    else:
                        severity = "critical"
                        label = "RECONCILIATION CRITICAL"
                        msg = (
                            f"{label} [{book_id}] {dtype}: "
                            f"symbols={syms}. Manual reconcile needed."
                        )
                    # Paper_only divergence is expected: simulation locale vs
                    # positions broker auto-fillees. On garde JSONL + syslog
                    # pour tracabilite mais on evite Telegram (spam ~180/24h).
                    if is_paper_book:
                        if sim_is_canonical:
                            logger.info(f"ALERT_INFO: {msg}")
                        else:
                            logger.warning(f"ALERT_WARN: {msg}")
                    else:
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
                                "source_of_truth": meta["source_of_truth"] or "broker",
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
