"""Audit reconciliation broker-side vs local state pour les paper books.

Usage:
    python scripts/audit/audit_paper_books_reconciliation.py

Ecrit un rapport markdown sur stdout, et code de retour :
    0 = aucune divergence actionable
    1 = divergences detectees (lire le rapport)

Couverture :
    - IBKR paper (port 4003) : positions broker vs futures_positions_paper.json
    - Alpaca paper : positions broker vs paper_portfolio_state.json
    - Binance live (1 seul book live, mais inclus pour info — pas besoin
      de detection drift puisque c'est la source de truth)

Note 2026-05-10 : le reconciliation_cycle.py existant log les divergences
dans worker.log mais c'est noye dans les autres alertes. Ce script donne
un snapshot one-shot lisible pour les checkup operateur.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Charger .env si present (script standalone, pas de systemd EnvironmentFile)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _ibkr_paper_positions() -> tuple[list[dict], str | None]:
    """Read IBKR paper positions via ib_insync (port 4003).

    Returns (positions_list, error_str). positions_list = [{symbol, qty, ...}].
    """
    import asyncio
    try:
        from ib_insync import IB
    except ImportError:
        return [], "ib_insync not installed"
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    ib = IB()
    ib.RequestTimeout = 15
    out: list[dict] = []
    err = None
    try:
        ib.connect("127.0.0.1", 4003, clientId=92, timeout=10)
        for item in ib.portfolio():
            if item.position == 0:
                continue
            c = item.contract
            sym = getattr(c, "localSymbol", None) or c.symbol
            out.append({
                "symbol": sym,
                "qty": float(item.position),
                "avg_cost": float(item.averageCost),
                "market_value": float(item.marketValue),
            })
    except Exception as e:
        err = f"connect/query failed: {e}"
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return out, err


def _ibkr_paper_local_positions() -> dict[str, dict]:
    """Read local state futures_positions_paper.json -> dict by local_symbol."""
    p = ROOT / "data" / "state" / "futures_positions_paper.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out: dict[str, dict] = {}
        # Format observed: {"MNQ": {local_symbol, qty, entry, ...}, ...}
        for sym, info in data.items():
            ls = info.get("local_symbol", sym)
            out[ls] = info
        return out
    except Exception:
        return {}


def _alpaca_paper_positions() -> tuple[list[dict], str | None]:
    try:
        from core.alpaca_client.client import AlpacaClient
        positions = AlpacaClient.from_env().get_positions()
        return positions or [], None
    except Exception as e:
        return [], f"AlpacaClient error: {e}"


def _alpaca_local_positions() -> dict[str, dict]:
    """Read paper_portfolio_state.json positions (simulation_local source of truth).

    Format observed: {"positions": {symbol: {qty, entry, ...}, ...}, ...}
    """
    p = ROOT / "data" / "state" / "paper_portfolio_state.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        positions = data.get("positions", {}) or {}
        return {sym: info for sym, info in positions.items() if isinstance(info, dict)}
    except Exception:
        return {}


def _section_ibkr_paper() -> tuple[str, int]:
    """Return (markdown_section, n_drifts)."""
    broker_pos, err = _ibkr_paper_positions()
    if err:
        return f"## IBKR paper (4003)\n\n[ERROR] {err}\n\n", 0
    local = _ibkr_paper_local_positions()
    broker_by_sym = {p["symbol"]: p for p in broker_pos}

    only_broker = sorted(set(broker_by_sym) - set(local))
    only_local = sorted(set(local) - set(broker_by_sym))
    common = sorted(set(broker_by_sym) & set(local))

    drift_count = 0
    lines = ["## IBKR paper (port 4003)", ""]
    if not broker_by_sym and not local:
        lines.append("Aucune position cote broker ou local. RAS.")
        lines.append("")
        return "\n".join(lines), 0

    lines.append(f"Broker-side: {len(broker_by_sym)} position(s). Local: {len(local)}.")
    lines.append("")
    if common:
        lines.append("### Communes (qty match check)")
        lines.append("| Symbol | Broker qty | Local qty | Drift |")
        lines.append("|--------|-----------:|----------:|-------|")
        for sym in common:
            bq = broker_by_sym[sym]["qty"]
            lq = float(local[sym].get("qty", 0))
            drift = "OK" if abs(bq - lq) < 0.001 else f"⚠️ {bq - lq:+.0f}"
            if "⚠️" in drift:
                drift_count += 1
            lines.append(f"| {sym} | {bq:.0f} | {lq:.0f} | {drift} |")
        lines.append("")
    if only_broker:
        lines.append("### Broker-only (PAS dans local — fantomes broker)")
        for sym in only_broker:
            p = broker_by_sym[sym]
            lines.append(f"- `{sym}` qty={p['qty']:.0f} @cost {p['avg_cost']:.2f} mkt=${p['market_value']:,.0f}")
        lines.append("")
        drift_count += len(only_broker)
    if only_local:
        lines.append("### Local-only (broker n'a plus la position — closed silencieusement)")
        for sym in only_local:
            lines.append(f"- `{sym}` qty={local[sym].get('qty', '?')}")
        lines.append("")
        drift_count += len(only_local)
    return "\n".join(lines), drift_count


def _section_alpaca_paper() -> tuple[str, int]:
    broker_pos, err = _alpaca_paper_positions()
    if err:
        return f"## Alpaca paper\n\n[ERROR] {err}\n\n", 0
    broker_by_sym = {p.get("symbol", "?"): p for p in broker_pos if p.get("symbol")}
    local = _alpaca_local_positions()

    only_broker = sorted(set(broker_by_sym) - set(local))
    only_local = sorted(set(local) - set(broker_by_sym))

    lines = ["## Alpaca paper", ""]
    lines.append(f"Source of truth = `paper_portfolio_state.json` (simulation locale).")
    lines.append(f"Broker-side: {len(broker_by_sym)} position(s). Local: {len(local)}.")
    lines.append("")
    drift_count = 0
    if only_broker:
        lines.append("### Broker-only (orphelines, broker positions ignored by design)")
        for sym in only_broker[:20]:
            p = broker_by_sym[sym]
            qty = float(p.get("qty", 0))
            mv = float(p.get("market_value", 0))
            lines.append(f"- `{sym}` qty={qty:+.0f} ${mv:,.0f}")
        lines.append("")
        # Drifts orphelins broker-side : informatif mais pas critique (simulation locale est canonique)
    if only_local:
        lines.append("### Local-only (simulation seulement, normal)")
        lines.append(f"({len(only_local)} symbols : {', '.join(only_local[:10])}{'...' if len(only_local) > 10 else ''})")
        lines.append("")
    return "\n".join(lines), drift_count


def main():
    sections = []
    total_drifts = 0

    sec_ibkr, n1 = _section_ibkr_paper()
    sections.append(sec_ibkr)
    total_drifts += n1

    sec_alpaca, n2 = _section_alpaca_paper()
    sections.append(sec_alpaca)
    total_drifts += n2

    print("# Audit paper books reconciliation\n")
    print(f"_Generated: {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()}_\n")
    print(f"**Total drifts critiques** : {total_drifts}\n")
    print("---\n")
    for s in sections:
        print(s)

    print("\n---\n")
    print("Drifts critiques = qty mismatch IBKR paper OU symbols broker-side absents du local.")
    print("Drifts informatifs = orphelins Alpaca (broker non canonique pour paper portfolio).")

    sys.exit(1 if total_drifts > 0 else 0)


if __name__ == "__main__":
    main()
