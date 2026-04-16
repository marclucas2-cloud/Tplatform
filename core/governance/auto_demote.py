"""Phase 7.5 — Auto-demotion strategies.

Demote automatique d'une strategie si:
  - drawdown_strat_pct > strategy budget
  - timeouts repetes (count > seuil sur 24h)
  - reconciliation diverge > seuil sur 7j
  - divergence backtest vs live > 2 sigma sur 30j

Action: edit live_whitelist.yaml status live_* -> paper_only + alert.
Pour idempotence + audit, on utilise un fichier `data/state/auto_demoted.json`
qui trace les demotes auto.

API:
    check_and_auto_demote(strategy_id, book_id, metrics) -> bool
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
AUTO_DEMOTE_STATE = ROOT / "data" / "state" / "global" / "auto_demoted.json"

# Default thresholds, can be overridden per strat in risk_registry.yaml
DEFAULT_THRESHOLDS = {
    "drawdown_strat_pct_max": -0.20,        # -20% drawdown strat
    "consecutive_losses_max": 7,
    "timeout_count_24h_max": 5,
    "reconcile_divergence_count_7d_max": 3,
    "divergence_sigma_max": 2.5,
}


def _load_state() -> dict:
    if not AUTO_DEMOTE_STATE.exists():
        return {}
    try:
        return json.loads(AUTO_DEMOTE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    AUTO_DEMOTE_STATE.parent.mkdir(parents=True, exist_ok=True)
    AUTO_DEMOTE_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_already_auto_demoted(strategy_id: str) -> bool:
    state = _load_state()
    return strategy_id in state and state[strategy_id].get("active", False)


def check_and_auto_demote(
    strategy_id: str, book_id: str, metrics: dict,
    thresholds: dict | None = None,
) -> bool:
    """Check si la strat doit etre demoted, et si oui, l'execute.

    Args:
        strategy_id: canonical strategy id
        book_id: book containing the strategy
        metrics: {drawdown_strat_pct, consecutive_losses, timeout_count_24h,
                  reconcile_divergence_count_7d, divergence_sigma}
        thresholds: override thresholds per strat (optional)

    Returns:
        True if demoted (action taken), False otherwise.
    """
    if is_already_auto_demoted(strategy_id):
        return False  # idempotent

    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    triggers = []

    if metrics.get("drawdown_strat_pct", 0) <= th["drawdown_strat_pct_max"]:
        triggers.append(f"drawdown {metrics['drawdown_strat_pct']*100:.1f}% <= {th['drawdown_strat_pct_max']*100}%")
    if metrics.get("consecutive_losses", 0) >= th["consecutive_losses_max"]:
        triggers.append(f"consecutive_losses {metrics['consecutive_losses']} >= {th['consecutive_losses_max']}")
    if metrics.get("timeout_count_24h", 0) >= th["timeout_count_24h_max"]:
        triggers.append(f"timeouts_24h {metrics['timeout_count_24h']} >= {th['timeout_count_24h_max']}")
    if metrics.get("reconcile_divergence_count_7d", 0) >= th["reconcile_divergence_count_7d_max"]:
        triggers.append(f"reconcile_divergence_7d {metrics['reconcile_divergence_count_7d']}")
    if abs(metrics.get("divergence_sigma", 0)) >= th["divergence_sigma_max"]:
        triggers.append(f"divergence_sigma {metrics['divergence_sigma']:.1f}")

    if not triggers:
        return False

    # Demote: enregistrer l'action
    state = _load_state()
    state[strategy_id] = {
        "active": True,
        "book_id": book_id,
        "demoted_at": datetime.now(timezone.utc).isoformat(),
        "triggers": triggers,
        "metrics_snapshot": metrics,
        "next_action": "Edit config/live_whitelist.yaml: change status to paper_only + commit + redeploy",
    }
    _save_state(state)
    logger.critical(
        f"AUTO-DEMOTE TRIGGERED: strat={strategy_id} book={book_id} "
        f"triggers={triggers}. EDIT live_whitelist.yaml MANUALLY."
    )
    # Note: pas de modification automatique du YAML (audit + safety).
    # L'operateur doit valider et commit.
    return True


def reset_auto_demote(strategy_id: str, reset_by: str = "operator") -> None:
    """Operateur acquite et autorise re-promotion (apres fix)."""
    state = _load_state()
    if strategy_id in state:
        state[strategy_id]["active"] = False
        state[strategy_id]["reset_at"] = datetime.now(timezone.utc).isoformat()
        state[strategy_id]["reset_by"] = reset_by
        _save_state(state)
        logger.warning(f"auto_demote reset for {strategy_id} by {reset_by}")
