"""Live whitelist loader + enforcement — single source of truth.

Reads config/live_whitelist.yaml at boot and exposes a decision API that
the worker MUST call before executing any LIVE order.

Contract:
  - is_strategy_live_allowed(strategy_id, book) -> bool
  - If strategy_id is NOT in whitelist for book, return False and log a warning
  - Status `live_core` and `live_probation` are allowed live
  - Status `paper_only` and `disabled` are NOT allowed live
  - Loader caches the parsed whitelist in memory with a file mtime check
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
WHITELIST_PATH = ROOT / "config" / "live_whitelist.yaml"

# Statuses that allow live execution
# live_micro added 2026-04-22 (desk productif plan): small real money, size caps
# per core/governance/live_micro_sizing.py grade.
LIVE_STATUSES = {"live_core", "live_probation", "live_micro"}
# Statuses that forbid live execution
# frozen added 2026-04-22: hors rotation business, pas rejete (re-activable).
BLOCK_STATUSES = {"paper_only", "disabled", "frozen"}

# In-memory cache with mtime invalidation
_cache: dict[str, Any] = {
    "data": None,
    "mtime": 0.0,
    "path": None,
}


class LiveWhitelistError(Exception):
    """Raised when the whitelist is missing, malformed, or inconsistent."""


def load_live_whitelist(path: Path | None = None) -> dict[str, Any]:
    """Load and cache the whitelist YAML. Returns parsed dict.

    Cache is invalidated when the file mtime changes (hot reload supported).
    """
    p = Path(path) if path else WHITELIST_PATH
    if not p.exists():
        raise LiveWhitelistError(f"live_whitelist.yaml not found at {p}")

    mtime = p.stat().st_mtime
    if _cache["data"] is not None and _cache["mtime"] == mtime and _cache["path"] == str(p):
        return _cache["data"]

    try:
        import yaml
    except ImportError as e:
        raise LiveWhitelistError(f"PyYAML not installed: {e}")

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise LiveWhitelistError(f"Failed to parse {p}: {e}")

    if not isinstance(data, dict):
        raise LiveWhitelistError(f"Whitelist root must be a dict, got {type(data)}")

    # Basic schema validation
    if "metadata" not in data:
        raise LiveWhitelistError("Whitelist missing 'metadata' section")

    _cache["data"] = data
    _cache["mtime"] = mtime
    _cache["path"] = str(p)
    logger.info(
        f"[governance] live_whitelist loaded: version={data['metadata'].get('version', '?')}, "
        f"updated={data['metadata'].get('updated_at', '?')}, books={_count_books(data)}"
    )
    return data


def _count_books(data: dict) -> dict[str, int]:
    """Return count of strategies per book (excluding metadata)."""
    counts = {}
    for key, entries in data.items():
        if key == "metadata":
            continue
        if isinstance(entries, list):
            counts[key] = len(entries)
    return counts


def get_live_whitelist_version() -> str:
    """Return the whitelist version string (for audit trail)."""
    try:
        data = load_live_whitelist()
        return str(data.get("metadata", {}).get("version", "unknown"))
    except Exception:
        return "error"


def is_strategy_live_allowed(strategy_id: str, book: str | None = None) -> bool:
    """Check if a given strategy_id is allowed to execute LIVE orders.

    Args:
        strategy_id: canonical id (must match entry in live_whitelist.yaml)
        book: optional book filter; if provided, strategy must be in that book

    Returns:
        True iff whitelist contains strategy_id with status in LIVE_STATUSES
        False otherwise (missing, paper_only, disabled, or wrong book)
    """
    try:
        data = load_live_whitelist()
    except LiveWhitelistError as e:
        logger.error(f"[governance] whitelist load failed: {e}")
        # FAIL-CLOSED: if we can't read the whitelist, refuse live trading
        return False

    # Search across all books unless one is specified
    books_to_search = [book] if book else [k for k in data.keys() if k != "metadata"]
    for b in books_to_search:
        entries = data.get(b, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("strategy_id") == strategy_id:
                status = entry.get("status", "").lower()
                if status in LIVE_STATUSES:
                    return True
                if status in BLOCK_STATUSES:
                    return False
                logger.warning(
                    f"[governance] {strategy_id}: unknown status '{status}' — treating as blocked"
                )
                return False

    logger.warning(
        f"[governance] {strategy_id}: NOT in live_whitelist (book={book or 'any'}) — blocking"
    )
    return False


def is_strategy_frozen(strategy_id: str) -> bool:
    """True si la strategie est en status=frozen dans quant_registry.

    Phase 3.1 desk productif 2026-04-22: les sleeves groupe C sont mises hors
    rotation business sans etre disabled/rejetees. Les cycles runtime doivent
    early-return si frozen pour liberer la bande passante mentale.

    Frozen != disabled: frozen est re-activable (retour paper_only ou live_micro)
    sans perte d'historique, alors que disabled implique rejet structurel.
    """
    try:
        from core.governance.quant_registry import get_entry
        entry = get_entry(strategy_id)
        if entry is None:
            return False
        return entry.status == "frozen"
    except Exception:
        return False


def get_strategy_entry(strategy_id: str, book: str | None = None) -> dict | None:
    """Return the full whitelist entry for a strategy (for audit/introspection)."""
    try:
        data = load_live_whitelist()
    except LiveWhitelistError:
        return None
    books_to_search = [book] if book else [k for k in data.keys() if k != "metadata"]
    for b in books_to_search:
        entries = data.get(b, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("strategy_id") == strategy_id:
                result = dict(entry)
                result["_book"] = b
                return result
    return None


def list_live_strategies(book: str | None = None) -> list[dict]:
    """Return all strategies with status live_core or live_probation."""
    try:
        data = load_live_whitelist()
    except LiveWhitelistError:
        return []
    results = []
    books_to_search = [book] if book else [k for k in data.keys() if k != "metadata"]
    for b in books_to_search:
        entries = data.get(b, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("status", "").lower() in LIVE_STATUSES:
                result = dict(entry)
                result["_book"] = b
                results.append(result)
    return results
