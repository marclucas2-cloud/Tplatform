"""Signal funnel tracker for crypto strategies.

Records per-strategy daily stats:
  - signals_emitted: total signals from signal_fn
  - signals_risk_passed: survived the risk check
  - signals_executed: actually sent to broker and filled
  - signals_failed: broker rejected (error)
  - last_signal_ts / last_trade_ts: for idle detection

Persisted to data/crypto/signal_funnel.jsonl (one line per day per strat).
Used by the 07h daily digest to build a Telegram summary.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "crypto"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_FUNNEL_FILE = _DATA_DIR / "signal_funnel.jsonl"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _load_today_state() -> dict[str, dict[str, Any]]:
    """Load all records for today, keyed by strat_id."""
    if not _FUNNEL_FILE.exists():
        return {}
    state: dict[str, dict[str, Any]] = {}
    try:
        for line in _FUNNEL_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("date") == _today():
                state[rec["strat_id"]] = rec
    except Exception as e:
        logger.warning(f"signal_funnel: failed to load {e}")
    return state


def _save_today_state(state: dict[str, dict[str, Any]]) -> None:
    """Rewrite the funnel file, keeping only today and yesterday."""
    try:
        today = _today()
        # Keep yesterday's records + today's updated state
        lines = []
        if _FUNNEL_FILE.exists():
            for line in _FUNNEL_FILE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("date") != today:
                        lines.append(line)
                except Exception:
                    continue
        for rec in state.values():
            lines.append(json.dumps(rec))
        _FUNNEL_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning(f"signal_funnel: failed to save {e}")


def _record(strat_id: str, field: str, extra: dict[str, Any] | None = None) -> None:
    state = _load_today_state()
    rec = state.get(strat_id, {
        "date": _today(),
        "strat_id": strat_id,
        "signals_emitted": 0,
        "signals_risk_passed": 0,
        "signals_executed": 0,
        "signals_failed": 0,
        "last_signal_ts": None,
        "last_trade_ts": None,
        "pnl_day": 0.0,
    })
    rec[field] = rec.get(field, 0) + 1
    if extra:
        rec.update(extra)
    now_iso = datetime.now(UTC).isoformat()
    if field == "signals_emitted":
        rec["last_signal_ts"] = now_iso
    elif field == "signals_executed":
        rec["last_trade_ts"] = now_iso
    state[strat_id] = rec
    _save_today_state(state)


def record_signal_emitted(strat_id: str, action: str = "") -> None:
    _record(strat_id, "signals_emitted", {"last_action": action})


def record_risk_passed(strat_id: str) -> None:
    _record(strat_id, "signals_risk_passed")


def record_executed(strat_id: str, pnl: float = 0.0) -> None:
    state = _load_today_state()
    rec = state.get(strat_id, {
        "date": _today(),
        "strat_id": strat_id,
        "signals_emitted": 0,
        "signals_risk_passed": 0,
        "signals_executed": 0,
        "signals_failed": 0,
        "last_signal_ts": None,
        "last_trade_ts": None,
        "pnl_day": 0.0,
    })
    rec["signals_executed"] = rec.get("signals_executed", 0) + 1
    rec["last_trade_ts"] = datetime.now(UTC).isoformat()
    rec["pnl_day"] = rec.get("pnl_day", 0.0) + pnl
    state[strat_id] = rec
    _save_today_state(state)


def record_failed(strat_id: str, error_type: str = "") -> None:
    _record(strat_id, "signals_failed", {"last_error": error_type})


def get_today_summary() -> dict[str, dict[str, Any]]:
    """Return today's state for all strats."""
    return _load_today_state()


def format_digest(title: str = "CRYPTO SIGNAL FUNNEL") -> str:
    """Build a Telegram digest string for today's funnel state."""
    state = _load_today_state()
    if not state:
        return f"{title}\n(no signals today)"

    lines = [title, f"Date: {_today()}", ""]
    total_sig = total_exec = total_fail = 0
    for strat_id, rec in sorted(state.items()):
        sig = rec.get("signals_emitted", 0)
        risk = rec.get("signals_risk_passed", 0)
        exe = rec.get("signals_executed", 0)
        fail = rec.get("signals_failed", 0)
        pnl = rec.get("pnl_day", 0.0)
        total_sig += sig
        total_exec += exe
        total_fail += fail
        if sig == 0 and exe == 0:
            continue
        # Show funnel: emitted → risk_passed → executed (fails in parens)
        line = f"{strat_id}: {sig} sig → {risk} risk → {exe} exec"
        if fail > 0:
            line += f" ({fail} fail!)"
        if pnl != 0:
            line += f" pnl=${pnl:+.0f}"
        lines.append(line)
    lines.append("")
    lines.append(f"Total: {total_sig} signals, {total_exec} executed, {total_fail} failed")
    return "\n".join(lines)
