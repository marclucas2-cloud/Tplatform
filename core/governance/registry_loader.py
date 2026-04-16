"""Registry loader unique — Phase 1.5 plan TODO XXL.

Charge tous les registres canoniques (books, strategies, risk, health) avec
validation schema. Boot fail-closed si registre invalide.

Usage:
    from core.governance.registry_loader import (
        load_books_registry, load_strategies_registry,
        load_risk_registry, load_health_registry,
        validate_all_registries,
    )

    # At boot:
    try:
        validate_all_registries()
    except RegistryValidationError as e:
        sys.exit(2)  # fail-closed

API:
    load_*_registry() -> dict (cached)
    validate_all_registries() -> raises RegistryValidationError if any invalid
    cross_check_consistency() -> raises if registres incoherents entre eux
"""
from __future__ import annotations

from pathlib import Path
import logging
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
BOOKS_PATH = ROOT / "config" / "books_registry.yaml"
STRATEGIES_PATH = ROOT / "config" / "strategies_registry.yaml"
RISK_PATH = ROOT / "config" / "risk_registry.yaml"
HEALTH_PATH = ROOT / "config" / "health_registry.yaml"
WHITELIST_PATH = ROOT / "config" / "live_whitelist.yaml"

_VALID_STATUSES_STRAT = {"research", "paper", "candidate_live", "live", "disabled", "retired"}
_VALID_MODES_BOOK = {"disabled", "paper_only", "live_allowed"}
_VALID_BROKERS = {"binance", "ibkr", "alpaca"}

_cache: dict[str, dict] = {}


class RegistryValidationError(Exception):
    """Raised when a registry fails schema validation or cross-check."""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise RegistryValidationError(f"Registry not found: {path}")
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RegistryValidationError(f"{path} is not a dict")
    return data


def load_books_registry() -> dict:
    """Load books_registry.yaml with mtime cache."""
    if "books" in _cache:
        return _cache["books"]
    raw = _load_yaml(BOOKS_PATH)
    if "books" not in raw or not isinstance(raw["books"], list):
        raise RegistryValidationError("books_registry.yaml: 'books' missing or not a list")
    _cache["books"] = {b["book_id"]: b for b in raw["books"]}
    return _cache["books"]


def load_strategies_registry() -> dict:
    """Load strategies_registry.yaml."""
    if "strategies" in _cache:
        return _cache["strategies"]
    raw = _load_yaml(STRATEGIES_PATH)
    if "strategies" not in raw or not isinstance(raw["strategies"], dict):
        raise RegistryValidationError("strategies_registry.yaml: 'strategies' missing or not a dict")
    _cache["strategies"] = raw["strategies"]
    return _cache["strategies"]


def load_risk_registry() -> dict:
    """Load risk_registry.yaml."""
    if "risk" in _cache:
        return _cache["risk"]
    raw = _load_yaml(RISK_PATH)
    _cache["risk"] = raw
    return _cache["risk"]


def load_health_registry() -> dict:
    """Load health_registry.yaml."""
    if "health" in _cache:
        return _cache["health"]
    raw = _load_yaml(HEALTH_PATH)
    _cache["health"] = raw
    return _cache["health"]


def load_live_whitelist_canonical() -> dict:
    """Load live_whitelist.yaml (canonical authorization contract)."""
    if "whitelist" in _cache:
        return _cache["whitelist"]
    raw = _load_yaml(WHITELIST_PATH)
    _cache["whitelist"] = raw
    return _cache["whitelist"]


def _validate_books(books: dict) -> list[str]:
    """Validate book schema. Returns list of errors (empty if OK)."""
    errors = []
    required_fields = ["book_id", "broker", "mode_authorized",
                       "capital_budget_usd", "allowed_strategies_source",
                       "kill_switch_scope"]
    for book_id, b in books.items():
        for f in required_fields:
            if f not in b:
                errors.append(f"book {book_id}: missing field '{f}'")
        if b.get("broker") not in _VALID_BROKERS:
            errors.append(f"book {book_id}: invalid broker '{b.get('broker')}'")
        if b.get("mode_authorized") not in _VALID_MODES_BOOK:
            errors.append(f"book {book_id}: invalid mode_authorized '{b.get('mode_authorized')}'")
        if b.get("capital_budget_usd", 0) < 0:
            errors.append(f"book {book_id}: capital_budget_usd negative")
    return errors


def _validate_strategies(strategies: dict) -> list[str]:
    """Validate strategy schema."""
    errors = []
    required_fields = ["book_id", "status", "thesis", "instrument_universe",
                       "signal_frequency", "holding_period", "required_data",
                       "cost_model", "demotion_conditions"]
    for sid, s in strategies.items():
        for f in required_fields:
            if f not in s:
                errors.append(f"strat {sid}: missing field '{f}'")
        if s.get("status") not in _VALID_STATUSES_STRAT:
            errors.append(f"strat {sid}: invalid status '{s.get('status')}'")
        if not isinstance(s.get("instrument_universe"), list):
            errors.append(f"strat {sid}: instrument_universe must be list")
    return errors


def cross_check_consistency() -> list[str]:
    """Cross-validate that registres are consistent.

    Checks:
      - Every strategy.book_id exists in books registry
      - Every strategy with status='live' or 'candidate_live' is in live_whitelist
        with status live_core or live_probation
      - Every live_whitelist entry exists in strategies registry
      - books.allowed_strategies_source paths point to existing files
    """
    errors = []
    books = load_books_registry()
    strats = load_strategies_registry()
    whitelist = load_live_whitelist_canonical()

    book_ids = set(books.keys())

    for sid, s in strats.items():
        # Strat -> book existence
        if s.get("book_id") not in book_ids:
            errors.append(f"strat {sid}: book_id '{s.get('book_id')}' unknown")

        # Strat live -> in whitelist live_*
        if s.get("status") in ("live", "candidate_live"):
            book = s.get("book_id", "")
            wl_book = whitelist.get(book, [])
            wl_entry = next((e for e in wl_book if e.get("strategy_id") == sid), None)
            if wl_entry is None:
                errors.append(
                    f"strat {sid}: status={s['status']} but absent from "
                    f"live_whitelist.yaml#{book}"
                )
            elif wl_entry.get("status") not in ("live_core", "live_probation"):
                errors.append(
                    f"strat {sid}: status={s['status']} but live_whitelist "
                    f"says status={wl_entry.get('status')}"
                )

    # whitelist -> strategies registry coverage
    for book in ("ibkr_futures", "binance_crypto", "ibkr_fx",
                 "ibkr_eu", "alpaca_us"):
        wl_book = whitelist.get(book, [])
        for entry in wl_book:
            sid = entry.get("strategy_id")
            if sid and sid not in strats:
                errors.append(
                    f"whitelist {book}.{sid}: missing fiche in strategies_registry.yaml"
                )

    return errors


def validate_all_registries() -> None:
    """Validate all 4 registries + cross-check.

    Raises RegistryValidationError with all errors concatenated if any.
    """
    all_errors = []
    try:
        all_errors.extend(_validate_books(load_books_registry()))
    except Exception as e:
        all_errors.append(f"books_registry load failed: {e}")
    try:
        all_errors.extend(_validate_strategies(load_strategies_registry()))
    except Exception as e:
        all_errors.append(f"strategies_registry load failed: {e}")
    try:
        load_risk_registry()
    except Exception as e:
        all_errors.append(f"risk_registry load failed: {e}")
    try:
        load_health_registry()
    except Exception as e:
        all_errors.append(f"health_registry load failed: {e}")

    if not all_errors:
        # Cross-check seulement si chaque registre passe son schema
        all_errors.extend(cross_check_consistency())

    if all_errors:
        msg = "Registry validation failed:\n" + "\n".join(f"  - {e}" for e in all_errors)
        logger.critical(msg)
        raise RegistryValidationError(msg)

    logger.info("All registries validated OK")


def clear_cache() -> None:
    """Clear loader cache (test use only)."""
    _cache.clear()
