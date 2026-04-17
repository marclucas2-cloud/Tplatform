"""Factory — build BookRuntime instances from canonical registries.

Reads books_registry.yaml + strategies_registry.yaml to configure each
book with its cycles, preflight checks, and reconciliation functions.
"""
from __future__ import annotations

import logging
from typing import Callable

from core.runtime.book_runtime import BookRuntime

logger = logging.getLogger("book_factory")


def build_runtimes_from_registry(
    cycle_registry: dict[str, dict[str, Callable]],
    preflight_registry: dict[str, Callable] | None = None,
    reconcile_registry: dict[str, Callable] | None = None,
    alert_fn: Callable[[str, str], None] | None = None,
) -> dict[str, BookRuntime]:
    """Build BookRuntime instances from canonical registries.

    Args:
        cycle_registry: {book_id: {cycle_name: callable}} — existing cycle functions
        preflight_registry: {book_id: preflight_fn} — optional preflight per book
        reconcile_registry: {book_id: reconcile_fn} — optional reconciliation
        alert_fn: shared alert callback (Telegram, etc.)

    Returns:
        {book_id: BookRuntime}
    """
    try:
        from core.governance.registry_loader import load_books_registry
        books_cfg = load_books_registry()
    except Exception as e:
        logger.error("Cannot load books_registry: %s", e)
        books_cfg = {}

    runtimes = {}
    for book_id, cycles in cycle_registry.items():
        book_cfg = books_cfg.get(book_id, {})
        broker = book_cfg.get("broker", "unknown")
        mode = book_cfg.get("mode_authorized", "paper_only")

        preflight_fn = (preflight_registry or {}).get(book_id)
        reconcile_fn = (reconcile_registry or {}).get(book_id)

        runtime = BookRuntime(
            book_id=book_id,
            broker=broker,
            mode=mode,
            cycles=cycles,
            preflight_fn=preflight_fn,
            reconcile_fn=reconcile_fn,
            alert_fn=alert_fn,
        )
        runtimes[book_id] = runtime
        logger.info(
            "Built runtime: %s (broker=%s, mode=%s, cycles=%s)",
            book_id, broker, mode, list(cycles.keys()),
        )

    return runtimes
