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

    # Local positions source: canonical positions.json only.
    # IMPORTANT: crypto_dd_state.json was historically listed here but it is NOT
    # a positions file — it contains DDBaselines metadata (peak_equity, session_id,
    # daily_anchor, etc.). Reading its keys as "positions" generated false CRITICAL
    # reconciliation alerts (2026-04-19 incident).
    positions_path = ROOT / "data" / "state" / "binance_crypto" / "positions.json"
    local_positions_dict: dict = {}
    if positions_path.exists():
        try:
            data = json.loads(positions_path.read_text(encoding="utf-8"))
            # Accept 2 formats: {"positions": {...}} or {SYMBOL: {...}} directly
            local_positions_dict = data.get("positions", data) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            result["divergences"].append({"type": "state_file_corrupted", "err": str(e)})
            return result
    result["local_positions"] = sorted(local_positions_dict.keys())

    # Detect divergences (informational, broker is source of truth)
    broker_syms = {p["symbol"] for p in result["broker_positions"]}
    local_syms = set(local_positions_dict.keys())
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

    state_paths = [
        ROOT / "data" / "state" / "ibkr_futures" /
        ("positions_paper.json" if paper else "positions_live.json"),
        ROOT / "data" / "state" /
        ("futures_positions_paper.json" if paper else "futures_positions_live.json"),
    ]
    state_path = next((p for p in state_paths if p.exists()), state_paths[0])
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


def reconcile_alpaca_us() -> dict:
    """Reconcile Alpaca: positions broker vs state file. Paper-only par doctrine."""
    result = {
        "book": "alpaca_us", "ts": datetime.now(timezone.utc).isoformat(),
        "divergences": [], "broker_positions": [], "local_positions": [],
        "broker_equity": None,
    }
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        info = client.get_account_info()
        result["broker_equity"] = info.get("equity")
        broker_pos = client.get_positions()
        result["broker_positions"] = [
            {"symbol": p.get("symbol"), "qty": float(p.get("qty", 0)),
             "market_val": float(p.get("market_val", 0))}
            for p in broker_pos
            if abs(float(p.get("market_val", 0))) > 1
        ]
    except Exception as e:
        result["error"] = f"alpaca broker query failed: {e}"
        return result

    # Local positions source.
    # IMPORTANT: paper_portfolio_state.json uses strategy_id as key (e.g. "vrp_rotation"),
    # not broker ticker. Extract the nested `symbols[]` list for proper ticker-level
    # comparison (2026-04-19 incident: false CRITICAL on "vrp_rotation" only_in_local).
    local_syms: set[str] = set()
    alpaca_positions = ROOT / "data" / "state" / "alpaca_us" / "positions.json"
    paper_portfolio = ROOT / "data" / "state" / "paper_portfolio_state.json"

    if alpaca_positions.exists():
        try:
            data = json.loads(alpaca_positions.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                pos = data.get("positions", data)
                if isinstance(pos, dict):
                    local_syms.update(pos.keys())
            elif isinstance(data, list):
                local_syms.update(item.get("symbol") for item in data if "symbol" in item)
        except Exception as e:
            logger.debug(f"reconcile_alpaca_us: positions.json read error: {e}")
    elif paper_portfolio.exists():
        # Legacy fallback: paper_portfolio_state.json is strategy-level state. Extract
        # actual tickers from each strategy's `symbols` list. Skip strategies without
        # symbols (typically strat_id mapping only, not open position).
        try:
            data = json.loads(paper_portfolio.read_text(encoding="utf-8"))
            positions_by_strat = data.get("positions", {}) if isinstance(data, dict) else {}
            for strat_id, meta in positions_by_strat.items():
                if isinstance(meta, dict):
                    for sym in meta.get("symbols", []):
                        if sym:
                            local_syms.add(sym)
        except Exception as e:
            logger.debug(f"reconcile_alpaca_us: paper_portfolio read error: {e}")

    result["local_positions"] = sorted(local_syms)

    broker_syms = {p["symbol"] for p in result["broker_positions"]}
    only_broker = broker_syms - local_syms
    only_local = local_syms - broker_syms
    if only_broker:
        result["divergences"].append({"type": "only_in_broker", "symbols": list(only_broker)})
    if only_local:
        result["divergences"].append({"type": "only_in_local", "symbols": list(only_local)})

    return result


def reconcile_ibkr_eu() -> dict:
    """Reconcile IBKR EU: actuellement paper_only (capital_budget=0).

    Le book est paper_only par doctrine (config/books_registry.yaml). Le seul
    runtime actif est mib_estx50_spread (paper runner isole, journal JSONL).
    Pas de positions broker IBKR EU live a reconcilier (capital live = 0 EUR).
    """
    result = {
        "book": "ibkr_eu", "ts": datetime.now(timezone.utc).isoformat(),
        "divergences": [], "broker_positions": [], "local_positions": [],
        "mode": "paper_only",
        "note": "Book paper_only, capital_budget_usd=0. mib_estx50_spread paper "
                "runner journal: data/state/mib_estx50_spread/paper_trades.jsonl",
    }
    # mib_estx50_spread spread state (open spread tracking, paper)
    spread_state = ROOT / "data" / "state" / "mib_estx50_spread" / "spread_state.json"
    if spread_state.exists():
        try:
            data = json.loads(spread_state.read_text(encoding="utf-8"))
            pos = data.get("position")
            if pos:
                result["local_positions"] = [
                    f"{pos.get('sym_a')}/{pos.get('sym_b')} {pos.get('direction')} "
                    f"({pos.get('n_a')}+{pos.get('n_b')})"
                ]
        except Exception as e:
            result["error"] = f"spread state read error: {e}"
    return result


def reconcile_book(book_id: str) -> dict:
    """Dispatcher reconciliation par book."""
    if book_id == "binance_crypto":
        return reconcile_binance_crypto()
    if book_id == "ibkr_futures":
        return reconcile_ibkr_futures(paper=False)
    if book_id == "alpaca_us":
        return reconcile_alpaca_us()
    if book_id == "ibkr_eu":
        return reconcile_ibkr_eu()
    raise ValueError(f"Reconciliation not implemented for book: {book_id}")


def save_reconciliation_report(result: dict) -> Path:
    """Persist reconciliation report under data/reconciliation/."""
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RECONCILE_DIR / f"{result['book']}_{date_str}.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return out
