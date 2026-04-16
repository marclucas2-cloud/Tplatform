"""Phase 9 — Dashboard CLI operateur.

Output compact verite-du-desk en < 5 minutes a lire.

Usage:
    python scripts/desk_status.py
    python scripts/desk_status.py --book binance_crypto
    python scripts/desk_status.py --json  (pour scripting)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def get_book_status(book_id: str) -> dict:
    """Get compact status for a book."""
    from core.governance.registry_loader import load_books_registry, load_strategies_registry
    from core.governance.kill_switches_scoped import is_killed
    from core.governance.safety_mode_flag import is_safety_mode_active
    from core.governance.live_whitelist import list_live_strategies
    from core.governance.data_freshness import check_data_freshness

    books = load_books_registry()
    strats = load_strategies_registry()
    book = books.get(book_id, {})
    if not book:
        return {"book_id": book_id, "error": "not in registry"}

    # Kill switches
    killed_global, ks_reason = is_killed(book_id="dummy")  # check global
    killed_book, kb_reason = is_killed(book_id=book_id)
    safety_active, safety_details = is_safety_mode_active()

    # Live strats for this book
    live = list_live_strategies(book_id)

    # All strats from registry assigned to this book
    book_strats = [s for sid, s in strats.items() if s.get("book_id") == book_id]
    by_status = {}
    for s in book_strats:
        by_status[s.get("status")] = by_status.get(s.get("status"), 0) + 1

    # Data freshness
    fresh, fresh_details = check_data_freshness(book_id)

    return {
        "book_id": book_id,
        "broker": book.get("broker"),
        "mode_authorized": book.get("mode_authorized"),
        "capital_budget_usd": book.get("capital_budget_usd"),
        "kill_global_active": killed_global,
        "kill_global_reason": ks_reason,
        "kill_book_active": killed_book,
        "kill_book_reason": kb_reason,
        "safety_mode_active": safety_active,
        "safety_mode_reason": safety_details.get("reason", "") if safety_active else "",
        "live_strats_count": len(live),
        "live_strat_ids": [e["strategy_id"] for e in live],
        "all_strats_by_status": by_status,
        "data_fresh": fresh,
        "data_freshness_details": fresh_details,
    }


def get_global_status() -> dict:
    """Global desk status."""
    books = ["binance_crypto", "ibkr_futures", "ibkr_eu", "ibkr_fx", "alpaca_us"]
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "books": {b: get_book_status(b) for b in books},
    }


def render_compact(status: dict) -> str:
    """Render < 5min readable summary."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  DESK STATUS — {status['ts']}")
    lines.append("=" * 70)

    # Per book
    for book_id, b in status["books"].items():
        if b.get("error"):
            lines.append(f"\n[{book_id}] ERROR: {b['error']}")
            continue
        # Status indicator
        ind = "OK"
        if b.get("kill_global_active") or b.get("kill_book_active"):
            ind = "KILLED"
        elif b.get("safety_mode_active"):
            ind = "SAFETY"
        elif not b.get("data_fresh"):
            ind = "STALE_DATA"
        elif b.get("mode_authorized") == "disabled":
            ind = "DISABLED"
        elif b.get("mode_authorized") == "paper_only":
            ind = "PAPER_ONLY"

        lines.append(f"\n[{ind}] {book_id} ({b.get('broker')})")
        lines.append(f"  mode={b.get('mode_authorized')}  capital=${b.get('capital_budget_usd', 0):,}")
        lines.append(f"  live strats: {b.get('live_strats_count', 0)} -> {b.get('live_strat_ids', [])}")
        lines.append(f"  by status: {b.get('all_strats_by_status', {})}")
        if b.get("kill_book_active"):
            lines.append(f"  KILL BOOK: {b['kill_book_reason']}")
        if b.get("safety_mode_active"):
            lines.append(f"  SAFETY MODE: {b['safety_mode_reason']}")
        if not b.get("data_fresh"):
            stale_files = [
                k for k, v in b.get("data_freshness_details", {}).items()
                if isinstance(v, dict) and v.get("status") == "stale"
            ]
            lines.append(f"  STALE DATA: {stale_files}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", help="Show only this book")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()

    status = get_global_status()
    if args.book:
        status["books"] = {args.book: status["books"].get(args.book, {})}

    if args.json:
        print(json.dumps(status, indent=2, default=str))
    else:
        print(render_compact(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
