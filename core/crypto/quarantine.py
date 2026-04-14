"""Strategy quarantine — enforce 7d paper signals before live execution.

A strategy is considered "quarantined" if it has been seen less than
QUARANTINE_DAYS days since its first signal. During quarantine:
  - The strategy can emit signals normally (logged, counted)
  - The crypto cycle refuses to execute LIVE orders from it
  - The strategy is effectively in paper-signal mode

Once the strategy has been continuously seen for QUARANTINE_DAYS,
it automatically exits quarantine and starts trading live.

Rationale: before risking real capital, we want N days of evidence
that the strategy's signal_fn runs without errors and produces
signals in realistic volume. This would have caught the STRAT-001
bug (30 silent rejects/day) much earlier if the strat had been newly
added to a fresh registry.

State: data/crypto/strat_quarantine.json
Override: set ENV QUARANTINE_BYPASS=<strat_id> to bypass manually.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "crypto"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE = _DATA_DIR / "strat_quarantine.json"

QUARANTINE_DAYS = 7
MIN_SIGNALS_FOR_RELEASE = 5  # also need at least 5 signals observed


def _load_state() -> dict[str, dict]:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, dict]) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"quarantine: save failed: {e}")


def observe_signal(strat_id: str) -> None:
    """Record that strat_id emitted a signal. Used by the crypto cycle."""
    state = _load_state()
    now = datetime.now(UTC).isoformat()
    rec = state.get(strat_id, {
        "first_seen": now,
        "last_seen": now,
        "signal_count": 0,
    })
    rec["last_seen"] = now
    rec["signal_count"] = rec.get("signal_count", 0) + 1
    state[strat_id] = rec
    _save_state(state)


def is_quarantined(strat_id: str) -> tuple[bool, str]:
    """Return (is_quarantined, reason). False = strat can trade live."""
    # Manual bypass
    bypass = os.environ.get("QUARANTINE_BYPASS", "")
    if strat_id in [s.strip() for s in bypass.split(",")]:
        return False, "bypassed via env"

    state = _load_state()
    rec = state.get(strat_id)

    if not rec:
        return True, "never seen — first signal observation needed"

    first_seen = rec.get("first_seen", "")
    try:
        first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
    except Exception:
        return True, "corrupted first_seen ts"

    age_days = (datetime.now(UTC) - first_dt).total_seconds() / 86400
    if age_days < QUARANTINE_DAYS:
        remaining = QUARANTINE_DAYS - age_days
        return True, f"quarantine {age_days:.1f}/{QUARANTINE_DAYS}d, {remaining:.1f}d remaining"

    signal_count = rec.get("signal_count", 0)
    if signal_count < MIN_SIGNALS_FOR_RELEASE:
        return True, f"only {signal_count}/{MIN_SIGNALS_FOR_RELEASE} signals observed (need more data)"

    return False, f"released after {age_days:.1f}d, {signal_count} signals"


def bootstrap_existing(strat_ids: list[str]) -> int:
    """Bootstrap pre-existing strategies as released from quarantine.

    Called at worker startup to prevent the new quarantine feature from
    blocking strategies that were already running in production before
    the feature was deployed. Strategies not yet seen get `first_seen`
    set to QUARANTINE_DAYS + 1 days ago so they are immediately released.
    Strategies already tracked are not modified.

    Returns the number of strategies bootstrapped.
    """
    state = _load_state()
    now = datetime.now(UTC)
    backdated = now - timedelta(days=QUARANTINE_DAYS + 1)
    bootstrapped = 0
    for sid in strat_ids:
        if sid in state:
            continue
        state[sid] = {
            "first_seen": backdated.isoformat(),
            "last_seen": now.isoformat(),
            "signal_count": MIN_SIGNALS_FOR_RELEASE,  # consider them already validated
            "bootstrapped": True,
        }
        bootstrapped += 1
    if bootstrapped > 0:
        _save_state(state)
        logger.info(f"quarantine: bootstrapped {bootstrapped} pre-existing strategies")
    return bootstrapped


def get_quarantine_status() -> list[dict]:
    """Return quarantine status for all observed strategies."""
    state = _load_state()
    out = []
    for strat_id, rec in state.items():
        quar, reason = is_quarantined(strat_id)
        out.append({
            "strat_id": strat_id,
            "first_seen": rec.get("first_seen", "?"),
            "last_seen": rec.get("last_seen", "?"),
            "signal_count": rec.get("signal_count", 0),
            "quarantined": quar,
            "reason": reason,
        })
    return out
