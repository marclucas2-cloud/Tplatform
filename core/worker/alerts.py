"""Worker alert routing and structured event logging."""
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("worker")

# Structured event log
_events_log_path = Path(__file__).parent.parent.parent / "logs" / "events.jsonl"
_events_log_path.parent.mkdir(parents=True, exist_ok=True)

# Signal-to-fill monitoring
_SIGNAL_FILL_LOG = Path(__file__).parent.parent.parent / "data" / "monitoring" / "signal_fill_ratio.jsonl"
_SIGNAL_FILL_LOG.parent.mkdir(parents=True, exist_ok=True)
_SIGNAL_FILL_HISTORY: list[dict] = []
_SIGNAL_FILL_LAST_ALERT: float = 0  # throttle: 1 alert per 4h max


def log_event(action: str, strategy: str = "", details: dict | None = None):
    """Append a structured JSON event to logs/events.jsonl."""
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "strategy": strategy,
        "action": action,
        "details": details or {},
    }
    try:
        with open(_events_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


def send_alert(message: str, level: str = "info"):
    """Unified alert: routes to Telegram V2 smart notifications.

    - critical -> sent immediately (never throttled)
    - warning -> sent with 5 min throttle per type
    - info -> buffered into digest (never sent individually)
    """
    try:
        from core.telegram_v2 import tg
        if level == "critical":
            title = message.split("\n")[0][:60]
            details = "\n".join(message.split("\n")[1:])
            tg.critical(title, details=details)
        elif level == "warning":
            title = message.split("\n")[0][:60]
            details = "\n".join(message.split("\n")[1:])
            tg.warning(title, details=details)
        else:
            tg.info(message[:100])
        return
    except Exception:
        pass
    try:
        from core.telegram_alert import send_alert as _legacy_alert
        _legacy_alert(message, level=level)
    except Exception:
        pass


def record_signal_fill(cycle: str, n_signals: int, n_fills: int, n_errors: int):
    """Record signal-to-fill metrics and alert if fill ratio drops."""
    ratio = n_fills / n_signals if n_signals > 0 else None
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "cycle": cycle,
        "n_signals": n_signals,
        "n_fills": n_fills,
        "n_errors": n_errors,
        "fill_ratio": ratio,
    }

    try:
        with open(_SIGNAL_FILL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass

    _SIGNAL_FILL_HISTORY.append(entry)
    if len(_SIGNAL_FILL_HISTORY) > 24:
        _SIGNAL_FILL_HISTORY.pop(0)

    recent_with_signals = [
        e for e in _SIGNAL_FILL_HISTORY
        if e["cycle"] == cycle and e["n_signals"] > 0
    ]
    if not recent_with_signals:
        return

    global _SIGNAL_FILL_LAST_ALERT
    now = time.time()

    # Throttle: max 1 signal-to-fill alert per 4h to avoid spam
    if now - _SIGNAL_FILL_LAST_ALERT < 14400:
        return

    # Check consecutive errors
    consecutive_errors = 0
    for e in reversed(_SIGNAL_FILL_HISTORY):
        if e["cycle"] == cycle and e["n_errors"] > 0:
            consecutive_errors += 1
        else:
            break
    if consecutive_errors >= 3:
        msg = (
            f"SIGNAL-TO-FILL CRITICAL: {consecutive_errors} cycles consecutifs "
            f"avec erreurs ({cycle})"
        )
        logger.critical(msg)
        send_alert(msg, level="critical")
        _SIGNAL_FILL_LAST_ALERT = now
        return

    last_6 = recent_with_signals[-6:]
    last_12 = recent_with_signals[-12:]

    if len(last_12) >= 12:
        total_s = sum(e["n_signals"] for e in last_12)
        total_f = sum(e["n_fills"] for e in last_12)
        if total_s > 0 and total_f == 0:
            msg = (
                f"SIGNAL-TO-FILL: 0 fills sur {len(last_12)} cycles "
                f"({total_s} signaux) — {cycle}"
            )
            logger.warning(msg)
            send_alert(msg, level="warning")
            _SIGNAL_FILL_LAST_ALERT = now
            return

    if len(last_6) >= 6:
        total_s = sum(e["n_signals"] for e in last_6)
        total_f = sum(e["n_fills"] for e in last_6)
        if total_s > 0 and total_f / total_s < 0.5:
            msg = (
                f"SIGNAL-TO-FILL WARNING: {total_f}/{total_s} fills "
                f"({total_f/total_s:.0%}) sur {len(last_6)} cycles — {cycle}"
            )
            logger.warning(msg)
            send_alert(msg, level="warning")
            _SIGNAL_FILL_LAST_ALERT = now
