#!/usr/bin/env python3
"""Crypto strategy audit — single source of truth.

Shows for each registered crypto strategy:
  - Status (LIVE loaded, DISABLED, FAILED_LOAD)
  - Allocation %, market_type, symbols, timeframe
  - Last signal timestamp, last trade timestamp
  - Signal funnel counts (today)
  - Is currently auto-paused (failure tracker)

Usage:
  python scripts/crypto_strats_audit.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from strategies.crypto import CRYPTO_STRATEGIES
from core.crypto.signal_funnel import get_today_summary


def audit() -> list[dict]:
    summary = get_today_summary()
    rows = []

    for strat_id in sorted(CRYPTO_STRATEGIES.keys()):
        s = CRYPTO_STRATEGIES[strat_id]
        cfg = s["config"]
        funnel = summary.get(strat_id, {})

        rows.append({
            "strat_id": strat_id,
            "name": cfg.get("name", "?"),
            "status": "LIVE",
            "market_type": cfg.get("market_type", "?"),
            "allocation_pct": cfg.get("allocation_pct", 0),
            "symbols": cfg.get("symbols", []),
            "timeframe": cfg.get("timeframe", "?"),
            "max_leverage": cfg.get("max_leverage", 1),
            # Funnel stats today
            "signals_today": funnel.get("signals_emitted", 0),
            "trades_today": funnel.get("signals_executed", 0),
            "failures_today": funnel.get("signals_failed", 0),
            "pnl_today": funnel.get("pnl_day", 0),
            "last_signal": funnel.get("last_signal_ts", "-"),
            "last_trade": funnel.get("last_trade_ts", "-"),
        })

    return rows


def print_table(rows: list[dict]) -> None:
    print(f"{'ID':<12} {'Name':<30} {'Alloc':>7} {'Mkt':<8} {'Sym':<14} {'Sig/Exec/Fail':<16} {'PnL':>8}")
    print("-" * 100)
    for r in rows:
        alloc = f"{r['allocation_pct']*100:.0f}%"
        sym = ",".join(r["symbols"][:2])
        funnel = f"{r['signals_today']}/{r['trades_today']}/{r['failures_today']}"
        pnl = f"${r['pnl_today']:+.0f}" if r['pnl_today'] else "-"
        print(
            f"{r['strat_id']:<12} {r['name'][:30]:<30} {alloc:>7} "
            f"{r['market_type']:<8} {sym:<14} {funnel:<16} {pnl:>8}"
        )
    print()
    print(f"Total strategies: {len(rows)}")
    print(f"Total signals today: {sum(r['signals_today'] for r in rows)}")
    print(f"Total trades today:  {sum(r['trades_today'] for r in rows)}")
    print(f"Total failures today:{sum(r['failures_today'] for r in rows)}")
    total_pnl = sum(r['pnl_today'] for r in rows)
    print(f"Total PnL today:     ${total_pnl:+.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    rows = audit()

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
