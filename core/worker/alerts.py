"""Worker alert routing and structured event logging."""
import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("worker")

# Structured event log
_events_log_path = Path(__file__).parent.parent.parent / "logs" / "events.jsonl"
_events_log_path.parent.mkdir(parents=True, exist_ok=True)

# Local alert fallback JSONL (always written, even if Telegram fails)
# Defense-in-depth: trace on disk survives broker/network outage.
_ALERTS_FALLBACK_PATH = Path(__file__).parent.parent.parent / "data" / "alerts" / "alerts.jsonl"
_ALERTS_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)

# Per-key send-throttle for repeat alerts. Incident 2026-06-05: 5186 CRITICAL
# alerts in 24h from BRACKET WATCHDOG re-firing every 5 min on M2K/MES/MNQ.
# Telegram is unusable when one repeating failure spams the channel — gate by
# (level, dedup-key) to send the first instance immediately then suppress for
# _ALERT_DEDUP_TTL_SEC. The local JSONL fallback is always written so the
# suppressed copies are never lost (a checkup can still count them).
_ALERT_DEDUP_TTL_SEC = 3600  # 1h between Telegram pushes for the same key
_ALERT_LAST_SENT: dict[tuple[str, str], float] = {}

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


def _append_alert_fallback(
    message: str, level: str, telegram_ok: bool, suppressed: bool = False
) -> None:
    """Persist alert to local JSONL regardless of Telegram status.

    Defense-in-depth: if Telegram is down/token leaked/rate-limited, the
    alert stays on disk (data/alerts/alerts.jsonl) where a healthcheck
    or manual review can find it. Warning/critical also hit syslog via logger.
    """
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message[:2000],
        "telegram_ok": telegram_ok,
        "telegram_suppressed": suppressed,
    }
    try:
        with open(_ALERTS_FALLBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
    # Syslog path: logger.{critical,warning} goes to systemd journal.
    # Suppressed copies still hit syslog so journalctl shows the real recurrence
    # rate — only Telegram gets throttled.
    if level == "critical":
        logger.critical(f"ALERT_CRITICAL: {message[:500]}")
    elif level == "warning":
        logger.warning(f"ALERT_WARN: {message[:500]}")


_DEDUP_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_DEDUP_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T?[\d:.+ \-Z]*")


def _alert_dedup_key(message: str) -> str:
    """Derive a stable key for `message` so retries collapse to one row.

    The watchdog re-emits the same text every 5 min ("WATCHDOG could not repose
    bracket on M2K\nReason: ...\nWill retry in 5 min."). Hashing the raw text
    works but timestamps / order ids / prices in other alerts would defeat the
    dedup — strip numbers and ISO timestamps before hashing so structurally
    identical messages collide.
    """
    first_line = message.split("\n", 1)[0]
    stripped = _DEDUP_TIMESTAMP_RE.sub("", first_line)
    stripped = _DEDUP_NUMBER_RE.sub("#", stripped).strip().lower()
    return hashlib.sha1(stripped.encode("utf-8")).hexdigest()[:16]


def send_alert(message: str, level: str = "info"):
    """Unified alert: Telegram V2 + local JSONL fallback + syslog.

    - critical / warning -> first instance sent immediately; identical repeats
      within _ALERT_DEDUP_TTL_SEC are suppressed from Telegram only (JSONL
      always written so counts/audit survive).
    - info -> buffered into digest (never sent individually)

    Local JSONL fallback is ALWAYS written first, so trace survives even if
    Telegram token leaks / API is down / rate limit hit. Syslog path ensures
    `journalctl -u trading-worker` shows warnings/criticals for VPS ops.
    """
    suppressed = False
    if level in ("critical", "warning"):
        _key = _alert_dedup_key(message)
        _now = time.time()
        _last = _ALERT_LAST_SENT.get((level, _key), 0.0)
        if _last and (_now - _last) < _ALERT_DEDUP_TTL_SEC:
            suppressed = True
        else:
            _ALERT_LAST_SENT[(level, _key)] = _now

    telegram_ok = False
    if not suppressed:
        try:
            from core.telegram_v2 import tg
            if level == "critical":
                title = message.split("\n")[0][:60]
                details = "\n".join(message.split("\n")[1:])
                tg.critical(title, details=details)
                telegram_ok = True
            elif level == "warning":
                title = message.split("\n")[0][:60]
                details = "\n".join(message.split("\n")[1:])
                tg.warning(title, details=details)
                telegram_ok = True
            else:
                tg.info(message[:100])
                telegram_ok = True
        except Exception:
            try:
                from core.telegram_alert import send_alert as _legacy_alert
                _legacy_alert(message, level=level)
                telegram_ok = True
            except Exception:
                telegram_ok = False

    # Always persist warning/critical to local JSONL (info skipped — digest only).
    # `suppressed=True` rows mean the cause kept firing but Telegram was throttled —
    # the count in the JSONL is still the source of truth for "how often did this happen".
    if level in ("critical", "warning"):
        _append_alert_fallback(message, level, telegram_ok, suppressed=suppressed)


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
