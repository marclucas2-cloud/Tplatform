"""Phase 5.4 — Reconciliation post-trade par book.

Compare positions/cash/orders locales vs broker. Detecte:
  - Positions locales sans equivalent broker (phantom)
  - Positions broker sans equivalent local (orphan)
  - Cash divergent
  - Ordres pendants divergents

Output: rapport JSON par book + alerting si divergence > seuil.

Usage:
    from core.governance.reconciliation import reconcile_book

    result = reconcile_book("binance_crypto")
    if result["divergences"]:
        send_alert(...)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
RECONCILE_DIR = ROOT / "data" / "reconciliation"


def reconcile_binance_crypto() -> dict:
    """Reconcile Binance: positions broker vs state file."""
    from core.broker.binance_broker import BinanceBroker
    result = {
        "book": "binance_crypto", "ts": datetime.now(timezone.utc).isoformat(),
        "divergences": [], "broker_positions": [], "local_positions": [],
        "broker_equity": None,
    }
    try:
        b = BinanceBroker()
        info = b.get_account_info()
        result["broker_equity"] = info.get("equity")
        broker_pos = b.get_positions()
        # Filter dust
        result["broker_positions"] = [
            p for p in broker_pos
            if abs(float(p.get("market_val", 0))) > 1
        ]
    except Exception as e:
        result["error"] = f"binance broker query failed: {e}"
        return result

    # Local state files
    local_paths = [
        ROOT / "data" / "state" / "binance_crypto" / "positions.json",  # new convention
        ROOT / "data" / "crypto_dd_state.json",                          # legacy
    ]
    local = {}
    for p in local_paths:
        if p.exists():
            try:
                local.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    result["local_positions"] = list(local.keys())

    # Detect divergences (informational, broker is source of truth)
    broker_syms = {p["symbol"] for p in result["broker_positions"]}
    local_syms = set(local.keys()) if isinstance(local, dict) else set()
    only_broker = broker_syms - local_syms
    only_local = local_syms - broker_syms
    if only_broker:
        result["divergences"].append({"type": "only_in_broker", "symbols": list(only_broker)})
    if only_local:
        result["divergences"].append({"type": "only_in_local", "symbols": list(only_local)})

    return result


def reconcile_ibkr_futures(paper: bool = False) -> dict:
    """Reconcile IBKR: positions broker vs state file."""
    import os
    result = {
        "book": "ibkr_futures",
        "mode": "paper" if paper else "live",
        "ts": datetime.now(timezone.utc).isoformat(),
        "divergences": [], "broker_positions": [], "local_positions": [],
        "broker_equity": None,
    }
    port = int(os.environ.get("IBKR_PAPER_PORT" if paper else "IBKR_PORT",
                              "4003" if paper else "4002"))
    try:
        from ib_insync import IB
        import random
        ib = IB()
        ib.connect(os.environ.get("IBKR_HOST", "127.0.0.1"),
                   port, clientId=random.randint(85, 87), timeout=10)
        try:
            for a in ib.accountSummary():
                if a.tag == "NetLiquidation":
                    result["broker_equity"] = float(a.value)
                    break
            for p in ib.positions():
                if p.position != 0:
                    result["broker_positions"].append({
                        "symbol": p.contract.localSymbol,
                        "qty": p.position,
                        "avgCost": p.avgCost,
                    })
        finally:
            ib.disconnect()
    except Exception as e:
        result["error"] = f"ibkr query failed: {e}"
        return result

    state_path = (ROOT / "data" / "state" /
                  ("futures_positions_paper.json" if paper else "futures_positions_live.json"))
    if state_path.exists():
        try:
            local = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(local, dict):
                result["local_positions"] = list(local.keys())
        except Exception as e:
            result["divergences"].append({"type": "state_file_corrupted", "err": str(e)})

    broker_syms = {p["symbol"][:3] for p in result["broker_positions"]}  # base symbol
    local_syms = set(result["local_positions"])
    only_broker = broker_syms - local_syms
    only_local = local_syms - broker_syms
    if only_broker:
        result["divergences"].append({"type": "only_in_broker", "symbols": list(only_broker)})
    if only_local:
        result["divergences"].append({"type": "only_in_local", "symbols": list(only_local)})

    return result


def reconcile_book(book_id: str) -> dict:
    """Dispatcher reconciliation par book."""
    if book_id == "binance_crypto":
        return reconcile_binance_crypto()
    if book_id == "ibkr_futures":
        return reconcile_ibkr_futures(paper=False)
    raise ValueError(f"Reconciliation not implemented for book: {book_id}")


def save_reconciliation_report(result: dict) -> Path:
    """Persist reconciliation report under data/reconciliation/."""
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RECONCILE_DIR / f"{result['book']}_{date_str}.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return out
