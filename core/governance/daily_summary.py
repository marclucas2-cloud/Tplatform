"""Phase 9.2 — Daily governance summary for Telegram.

Generates a compact daily summary of desk state, sent at 07:00 CET.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("daily_summary")


def generate_daily_summary() -> str:
    """Generate compact governance summary for Telegram."""
    lines = []
    lines.append(f"GOVERNANCE DAILY — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Books status
    try:
        from core.governance.registry_loader import load_books_registry
        books = load_books_registry()
        for book_id, cfg in books.items():
            mode = cfg.get("mode_authorized", "?")
            icon = {"live_allowed": "LIVE", "paper_only": "PAPER", "disabled": "OFF"}.get(mode, "?")
            capital = cfg.get("capital_budget_usd", 0)
            lines.append(f"  [{icon}] {book_id} (${capital:,})")
    except Exception as e:
        lines.append(f"  Books: error ({e})")

    lines.append("")

    # Whitelist summary
    try:
        from core.governance.live_whitelist import load_live_whitelist
        wl = load_live_whitelist()
        strategies = wl.get("strategies", {}) if isinstance(wl, dict) else {}
        by_status: dict[str, int] = {}
        for _sid, cfg in strategies.items():
            st = cfg.get("status", "?")
            by_status[st] = by_status.get(st, 0) + 1
        parts = [f"{st}: {n}" for st, n in sorted(by_status.items())]
        lines.append(f"  Whitelist: {', '.join(parts)}")
    except Exception as e:
        lines.append(f"  Whitelist: error ({e})")

    # Kill switches
    try:
        from core.governance.kill_switches_scoped import is_killed
        killed, reason = is_killed(book_id="__global__")
        if killed:
            lines.append(f"  KILL SWITCH ACTIVE: {reason}")
        else:
            lines.append("  Kill switches: all clear")
    except Exception:
        lines.append("  Kill switches: check error")

    # Safety mode
    try:
        from core.governance.safety_mode_flag import is_safety_mode_active
        active, details = is_safety_mode_active()
        if active:
            lines.append(f"  SAFETY MODE: {details.get('reason', '?')}")
    except Exception:
        pass

    # Data freshness
    try:
        from core.governance.data_freshness import check_data_freshness
        stale_books = []
        for book_id in ["binance_crypto", "ibkr_futures"]:
            fresh, _details = check_data_freshness(book_id)
            if not fresh:
                stale_books.append(book_id)
        if stale_books:
            lines.append(f"  STALE DATA: {', '.join(stale_books)}")
    except Exception:
        pass

    # Backup status
    try:
        from scripts.backup_state import list_backups
        backups = list_backups()
        if backups:
            last = backups[0]
            lines.append(f"  Last backup: {last.get('date', '?')} ({last.get('copied', '?')} files)")
        else:
            lines.append("  Last backup: NONE")
    except Exception:
        pass

    lines.append("")
    lines.append("Full status: /governance dashboard")

    return "\n".join(lines)


def send_daily_summary() -> bool:
    """Generate and send the daily governance summary via Telegram."""
    try:
        summary = generate_daily_summary()
        from core.worker.alerts import send_alert
        send_alert(summary, level="info")
        logger.info("Daily governance summary sent")
        return True
    except Exception as e:
        logger.error("Failed to send daily summary: %s", e)
        return False
