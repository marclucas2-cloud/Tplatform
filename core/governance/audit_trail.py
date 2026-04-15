"""Audit trail standard — append-only JSONL for every LIVE order.

Goal: any LIVE order must be reconstructible post-mortem from a single line.

Schema per line (JSON):
  ts                  : ISO UTC timestamp of the decision
  book                : ibkr_futures / binance_crypto / alpaca_us / ...
  strategy_id         : canonical id (must be in live_whitelist.yaml)
  runtime_source      : worker.py function / module that took the decision
  whitelist_version   : metadata.version from live_whitelist.yaml
  book_health         : book status at decision time (GREEN/DEGRADED/BLOCKED)
  symbol              : trading symbol
  side                : BUY / SELL
  qty                 : quantity (contracts / shares / notional)
  entry_price_est     : estimated entry (signal or bar close)
  stop_loss           : SL price
  take_profit         : TP price
  risk_usd            : worst-case risk in USD
  risk_budget_usd     : total risk budget at decision time
  current_risk_usd    : cumulative risk BEFORE this order
  sizing_source       : risk_budget | kelly | fixed | ...
  authorized_by       : string identifying the caller
  broker_response     : raw response dict from broker (fill price, order id, ...)
  result              : ACCEPTED | REJECTED | FAILED | SKIPPED

Storage: data/audit/orders_{YYYY-MM-DD}.jsonl (rotated daily).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_DIR = ROOT / "data" / "audit"

_lock = threading.Lock()


def _audit_file_for_today() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"orders_{today}.jsonl"


def record_order_decision(
    *,
    book: str,
    strategy_id: str,
    runtime_source: str,
    symbol: str,
    side: str,
    qty: float,
    entry_price_est: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    risk_usd: float | None = None,
    risk_budget_usd: float | None = None,
    current_risk_usd: float | None = None,
    sizing_source: str = "unknown",
    authorized_by: str = "unknown",
    broker_response: dict | None = None,
    result: str = "UNKNOWN",
    extra: dict | None = None,
) -> None:
    """Append one audit line for a live order decision.

    Never raises — failures are logged but silent, audit must not block
    the critical path.
    """
    try:
        from core.governance.live_whitelist import get_live_whitelist_version
    except Exception:
        get_live_whitelist_version = lambda: "unknown"

    try:
        from core.governance.book_health import get_book_health
        book_status = get_book_health(book, use_cache=True).status.value
    except Exception:
        book_status = "UNKNOWN"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "book": book,
        "strategy_id": strategy_id,
        "runtime_source": runtime_source,
        "whitelist_version": get_live_whitelist_version(),
        "book_health": book_status,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price_est": entry_price_est,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_usd": risk_usd,
        "risk_budget_usd": risk_budget_usd,
        "current_risk_usd": current_risk_usd,
        "sizing_source": sizing_source,
        "authorized_by": authorized_by,
        "broker_response": broker_response or {},
        "result": result,
    }
    if extra:
        entry["extra"] = extra

    try:
        path = _audit_file_for_today()
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.error(f"[audit_trail] failed to write: {e}")


def read_recent(days: int = 1, book: str | None = None) -> list[dict]:
    """Return audit entries from the last N days, optionally filtered by book."""
    results = []
    if not AUDIT_DIR.exists():
        return results
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        d = today - timedelta(days=i)
        path = AUDIT_DIR / f"orders_{d.strftime('%Y-%m-%d')}.jsonl"
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if book is None or entry.get("book") == book:
                            results.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[audit_trail] read failed {path.name}: {e}")
    return results
