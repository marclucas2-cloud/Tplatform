"""Safety mode FLAG file — Phase 2.2 audit fix.

Avant: SafetyMode DISABLE_TRADING faisait juste log + alert, pas de blocage.
Apres: ecrit/lit un flag file consulte par pre_order_guard pour fail-closed.

Fichier: data/state/safety_mode_active.flag
Format: JSON {"active": bool, "reason": str, "activated_at": iso8601, "activated_by": str}

API:
    activate_safety_mode(reason, activated_by)
    deactivate_safety_mode(deactivated_by)
    is_safety_mode_active() -> (bool, dict)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
FLAG_PATH = ROOT / "data" / "state" / "safety_mode_active.flag"


def activate_safety_mode(reason: str, activated_by: str = "system") -> None:
    """Active safety mode. Tous les pre_order_guard suivants vont bloquer."""
    FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active": True,
        "reason": reason,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activated_by": activated_by,
    }
    FLAG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.critical(f"SAFETY MODE ACTIVATED by {activated_by}: {reason}")


def deactivate_safety_mode(deactivated_by: str = "operator") -> None:
    """Desactive safety mode. Trading peut reprendre."""
    if not FLAG_PATH.exists():
        return
    try:
        payload = json.loads(FLAG_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload["active"] = False
    payload["deactivated_at"] = datetime.now(timezone.utc).isoformat()
    payload["deactivated_by"] = deactivated_by
    FLAG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.warning(f"SAFETY MODE DEACTIVATED by {deactivated_by}")


def is_safety_mode_active() -> tuple[bool, dict]:
    """Return (active, details). active=False if file missing or active=false."""
    if not FLAG_PATH.exists():
        return False, {}
    try:
        payload = json.loads(FLAG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        # Fail-closed: si flag corrupted, considere active pour safety
        logger.error(f"safety_mode flag corrupted, fail-closed: {e}")
        return True, {"reason": f"flag_corrupted: {e}"}
    return bool(payload.get("active", False)), payload
