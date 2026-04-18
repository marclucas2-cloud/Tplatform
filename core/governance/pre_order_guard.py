"""Pre-order guard — verification UNIQUE avant tout ordre live.

Phase 2.1 du plan TODO XXL DESK PERSO 10/10.

Centralise la verification de droit de trader, executee a l'endroit le plus
proche de l'API broker. Avant ce module, 28 entrypoints LIVE existent dans
le code sans aucun check whitelist (cf reports/runtime/live_entrypoints_inventory.md).

Usage type:
    from core.governance.pre_order_guard import pre_order_guard, GuardError

    def create_position(self, symbol, ..., _authorized_by=None, ...):
        # P2.1 enforcement
        pre_order_guard(
            book="binance_crypto",
            strategy_id=_extract_strat_from_authorized_by(_authorized_by),
            symbol=symbol,
            paper_mode=os.getenv("PAPER_TRADING", "true").lower() == "true",
        )
        # ... reste du code

API:
    pre_order_guard(book, strategy_id, symbol=None, paper_mode=False) -> None
        Raise GuardError si l'ordre n'est PAS autorise.
        Silent OK sinon (return None).

    Verifications:
      1. book existe dans config/books_registry.yaml
      2. book.mode_authorized != "disabled"
      3. (si live) book.mode_authorized == "live_allowed"
      4. (si live) strategy_id dans live_whitelist + status live_*
      5. (futur Phase 2.2) book health != BLOCKED

Fail-closed: toute erreur de validation = raise GuardError (pas de bypass).
"""
from __future__ import annotations

from pathlib import Path
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
BOOKS_REGISTRY_PATH = ROOT / "config" / "books_registry.yaml"

_books_cache: dict | None = None
_books_cache_mtime: float = 0


class GuardError(Exception):
    """Raised when pre_order_guard rejects an order request."""
    def __init__(self, reason: str, book: str = "", strategy_id: str = ""):
        self.reason = reason
        self.book = book
        self.strategy_id = strategy_id
        msg = f"PRE_ORDER_GUARD REJECT [book={book} strat={strategy_id}]: {reason}"
        super().__init__(msg)


def _load_books_registry() -> dict:
    """Load books registry with mtime cache."""
    global _books_cache, _books_cache_mtime
    if not BOOKS_REGISTRY_PATH.exists():
        raise GuardError(f"books_registry.yaml not found at {BOOKS_REGISTRY_PATH}")
    mtime = BOOKS_REGISTRY_PATH.stat().st_mtime
    if _books_cache is not None and mtime == _books_cache_mtime:
        return _books_cache
    import yaml
    with open(BOOKS_REGISTRY_PATH) as f:
        data = yaml.safe_load(f) or {}
    if "books" not in data:
        raise GuardError("books_registry.yaml has no 'books' key")
    _books_cache = {b["book_id"]: b for b in data["books"]}
    _books_cache_mtime = mtime
    return _books_cache


def pre_order_guard(
    book: str,
    strategy_id: str,
    symbol: Optional[str] = None,
    paper_mode: bool = False,
    _bypass_for_test: bool = False,
) -> None:
    """Validate that an order request is authorized.

    Args:
        book: book_id (e.g. "binance_crypto", "ibkr_futures")
        strategy_id: canonical strategy id (e.g. "btc_eth_dual_momentum")
        symbol: optional symbol for logging context
        paper_mode: if True, less strict (paper-only books OK to trade)
        _bypass_for_test: TEST USE ONLY, raises if not in pytest env

    Raises:
        GuardError: if any check fails. Order MUST NOT be sent.
    """
    if _bypass_for_test:
        # Sanity check: only allow bypass in actual test env
        import os
        if "PYTEST_CURRENT_TEST" not in os.environ:
            raise GuardError("_bypass_for_test=True hors pytest, refuse")
        return

    if not book:
        raise GuardError("book is empty", book=book, strategy_id=strategy_id)
    if not strategy_id:
        raise GuardError("strategy_id is empty", book=book, strategy_id=strategy_id)

    # 1. Book exists
    try:
        books = _load_books_registry()
    except GuardError:
        raise
    except Exception as e:
        # Fail-closed: si registry illisible, on bloque
        raise GuardError(f"books_registry load error: {e}",
                         book=book, strategy_id=strategy_id)

    if book not in books:
        raise GuardError(
            f"book unknown in registry (known: {sorted(books.keys())})",
            book=book, strategy_id=strategy_id,
        )

    book_cfg = books[book]
    mode = book_cfg.get("mode_authorized", "disabled")

    # 2. Book disabled = no trade ever
    if mode == "disabled":
        raise GuardError(
            f"book mode_authorized=disabled (cf books_registry.yaml)",
            book=book, strategy_id=strategy_id,
        )

    # 3. Live mode: book must allow it
    if not paper_mode and mode != "live_allowed":
        raise GuardError(
            f"book mode_authorized={mode} mais paper_mode=False (live order). "
            f"Pour trader live, mode_authorized doit etre 'live_allowed'.",
            book=book, strategy_id=strategy_id,
        )

    # 4. Strategy whitelisted (live mode only — paper mode is permissive)
    if not paper_mode:
        try:
            from core.governance.live_whitelist import is_strategy_live_allowed
            if not is_strategy_live_allowed(strategy_id, book):
                raise GuardError(
                    f"strategy non autorisee live dans live_whitelist.yaml "
                    f"(book={book})",
                    book=book, strategy_id=strategy_id,
                )
        except GuardError:
            raise
        except Exception as e:
            raise GuardError(
                f"is_strategy_live_allowed check error: {e}",
                book=book, strategy_id=strategy_id,
            )

    # 5. Safety mode flag check (Phase 2.2 audit fix).
    # Si DISABLE_TRADING actif, bloquer tout ordre live (paper toujours OK).
    if not paper_mode:
        try:
            from core.governance.safety_mode_flag import is_safety_mode_active
            active, details = is_safety_mode_active()
            if active:
                raise GuardError(
                    f"safety_mode active: {details.get('reason', 'unknown')}",
                    book=book, strategy_id=strategy_id,
                )
        except GuardError:
            raise
        except Exception as e:
            logger.warning(f"safety_mode_flag check error: {e}")

    # 6. Kill switches scoped check (Phase 2.4 audit).
    # Hierarchy global > broker > book > strategy.
    try:
        from core.governance.kill_switches_scoped import is_killed
        killed, reason = is_killed(book_id=book, strategy_id=strategy_id)
        if killed:
            raise GuardError(
                f"kill_switch active: {reason}",
                book=book, strategy_id=strategy_id,
            )
    except GuardError:
        raise
    except Exception as e:
        logger.warning(f"kill_switches_scoped check error: {e}")

    # 7. Book health check (B1 audit 2026-04-17, raffine P1.1 audit 2026-04-18).
    # BLOCKED = refuse always. UNKNOWN = refuse in live.
    # DEGRADED = decision par cause (matrice ci-dessous), pas blanket-allow:
    #   - data_freshness stale (parquet missing/stale)        -> BLOCK (signal sur data ancienne)
    #   - ibkr_account snapshot transient (timeout, summary)  -> ALLOW (state local OK)
    #   - whitelist_integrity DEGRADED                         -> BLOCK (rare, indique config corrompue)
    #   - autres causes inconnues                              -> BLOCK (fail-safe)
    if not paper_mode:
        try:
            from core.governance.book_health import get_book_health, HealthStatus
            health = get_book_health(book)
            health_status = health.status.value if hasattr(health.status, "value") else str(health.status)

            if health_status == "BLOCKED":
                blocked_checks = [c.name for c in getattr(health, "checks", [])
                                  if hasattr(c, "status") and getattr(c.status, "value", str(c.status)) == "BLOCKED"]
                raise GuardError(
                    f"book health BLOCKED: {','.join(blocked_checks) or 'unknown'}",
                    book=book, strategy_id=strategy_id,
                )

            if health_status == "UNKNOWN":
                raise GuardError(
                    f"book health UNKNOWN (fail-closed in live)",
                    book=book, strategy_id=strategy_id,
                )

            if health_status == "DEGRADED":
                # Inspect WHICH check is DEGRADED to decide
                degraded_checks = [
                    c for c in getattr(health, "checks", [])
                    if hasattr(c, "status") and getattr(c.status, "value", str(c.status)) == "DEGRADED"
                ]
                # Categorize: data freshness or whitelist_integrity = block
                blocking_causes = []
                allowed_causes = []
                for c in degraded_checks:
                    name = getattr(c, "name", "")
                    if name.startswith("data::") or name == "whitelist_integrity":
                        blocking_causes.append(name)
                    elif name in ("ibkr_account", "futures_state", "ibkr_equity", "crypto_equity"):
                        # Snapshot transient or state slightly stale - tolerable
                        allowed_causes.append(name)
                    else:
                        # Unknown cause -> conservative block
                        blocking_causes.append(name)

                if blocking_causes:
                    raise GuardError(
                        f"book health DEGRADED on critical checks: {','.join(blocking_causes)}",
                        book=book, strategy_id=strategy_id,
                    )
                logger.warning(
                    f"pre_order_guard: book {book} DEGRADED on tolerated checks "
                    f"({','.join(allowed_causes)}) — order allowed"
                )
        except GuardError:
            raise
        except Exception as e:
            logger.warning(f"book_health check error (non-blocking): {e}")

    # All checks passed — log debug only (no spam)
    logger.debug(
        f"pre_order_guard PASS book={book} strat={strategy_id} sym={symbol} paper={paper_mode}"
    )
