"""Kill switches scoped — Phase 2.4 audit.

Avant: kill_switch_state.json = un seul flag global. Si crypto trigger ->
kill IBKR aussi (cross-contamination).

Apres: kill switches scopes par book. Triggering crypto ne kill que crypto.

Hierarchy:
  GLOBAL > BROKER > BOOK > STRATEGY

API:
    activate_kill_switch(scope, reason, scope_id=None)
    is_killed(book_id, strategy_id=None) -> bool
    deactivate_kill_switch(scope, scope_id=None)
    get_active_kill_switches() -> list

Storage: data/state/kill_switches/scoped.json
Format:
    {
      "global": {"active": false, ...},
      "broker": {"binance": {"active": false}, ...},
      "book": {"binance_crypto": {"active": false}, ...},
      "strategy": {"btc_eth_dual_momentum": {"active": false}, ...}
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
KS_PATH = ROOT / "data" / "state" / "kill_switches" / "scoped.json"

VALID_SCOPES = ("global", "broker", "book", "strategy")

_BOOK_TO_BROKER = {
    "binance_crypto": "binance",
    "ibkr_futures": "ibkr",
    "ibkr_eu": "ibkr",
    "ibkr_fx": "ibkr",
    "alpaca_us": "alpaca",
}


def _load() -> dict:
    if not KS_PATH.exists():
        return {"global": {"active": False}, "broker": {}, "book": {}, "strategy": {}}
    try:
        return json.loads(KS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"kill_switches scoped load error: {e}")
        # Fail-closed: on retourne global active si fichier corrupted
        return {"global": {"active": True, "reason": f"load_error: {e}"}}


def _save(state: dict) -> None:
    KS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KS_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def activate_kill_switch(
    scope: str, reason: str, scope_id: str | None = None,
    activated_by: str = "system",
) -> None:
    """Active un kill switch.

    Args:
        scope: "global", "broker", "book", "strategy"
        reason: motif (loggue + audit)
        scope_id: requis pour broker/book/strategy (e.g. "binance", "ibkr_futures")
        activated_by: who triggered
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope} (use {VALID_SCOPES})")
    if scope != "global" and not scope_id:
        raise ValueError(f"scope_id required for scope={scope}")

    state = _load()
    payload = {
        "active": True,
        "reason": reason,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activated_by": activated_by,
    }
    if scope == "global":
        state["global"] = payload
    else:
        state.setdefault(scope, {})[scope_id] = payload
    _save(state)
    logger.critical(f"KILL SWITCH ACTIVATED scope={scope} id={scope_id or '*'} by={activated_by}: {reason}")


def deactivate_kill_switch(
    scope: str, scope_id: str | None = None,
    deactivated_by: str = "operator",
) -> None:
    state = _load()
    if scope == "global":
        if state.get("global", {}).get("active"):
            state["global"]["active"] = False
            state["global"]["deactivated_at"] = datetime.now(timezone.utc).isoformat()
            state["global"]["deactivated_by"] = deactivated_by
    else:
        scope_state = state.get(scope, {})
        if scope_id in scope_state and scope_state[scope_id].get("active"):
            scope_state[scope_id]["active"] = False
            scope_state[scope_id]["deactivated_at"] = datetime.now(timezone.utc).isoformat()
            scope_state[scope_id]["deactivated_by"] = deactivated_by
    _save(state)
    logger.warning(f"KILL SWITCH DEACTIVATED scope={scope} id={scope_id or '*'} by={deactivated_by}")


def is_killed(book_id: str, strategy_id: str | None = None) -> tuple[bool, str]:
    """Check si un trade pour ce book/strategy est bloque par un kill switch.

    Returns:
        (killed, reason). killed=True si N'IMPORTE quel niveau de la
        hierarchie (global/broker/book/strategy) est actif.
    """
    state = _load()

    # Global
    if state.get("global", {}).get("active"):
        return True, f"GLOBAL: {state['global'].get('reason', '?')}"

    # Broker
    broker = _BOOK_TO_BROKER.get(book_id)
    if broker:
        broker_state = state.get("broker", {}).get(broker, {})
        if broker_state.get("active"):
            return True, f"BROKER {broker}: {broker_state.get('reason', '?')}"

    # Book
    book_state = state.get("book", {}).get(book_id, {})
    if book_state.get("active"):
        return True, f"BOOK {book_id}: {book_state.get('reason', '?')}"

    # Strategy
    if strategy_id:
        strat_state = state.get("strategy", {}).get(strategy_id, {})
        if strat_state.get("active"):
            return True, f"STRAT {strategy_id}: {strat_state.get('reason', '?')}"

    return False, ""


def get_active_kill_switches() -> list[dict]:
    """Return list of currently active kill switches."""
    state = _load()
    active = []
    if state.get("global", {}).get("active"):
        active.append({"scope": "global", "scope_id": None, **state["global"]})
    for scope in ("broker", "book", "strategy"):
        for sid, st in state.get(scope, {}).items():
            if st.get("active"):
                active.append({"scope": scope, "scope_id": sid, **st})
    return active
