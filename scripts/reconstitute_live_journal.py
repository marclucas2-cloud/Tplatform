#!/usr/bin/env python3
"""Reconstitute live journal from IBKR executions API.

Context: live_journal.db is empty because of the FrozenInstanceError bug
(fixed commit e6002e7) that blocked the journal INSERT for ~6 weeks.

This script fetches all executions from IBKR via reqExecutions (last 24h
by default, but can be extended with date filters) and reconstructs the
trade history into the live_journal.db.

Run via SSH on VPS where IBKR is running:
  .venv/bin/python scripts/reconstitute_live_journal.py --days 30 --dry-run
  .venv/bin/python scripts/reconstitute_live_journal.py --days 30 --execute

Limitations:
  - IBKR only retains ~7 days of execution history via API (paid TWS clients)
  - For older history: need to use IBKR Flex Web Service (different auth flow)
  - This script works only for recent fills visible via reqExecutions
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reconstitute_journal")


def fetch_executions(ib, days: int = 7) -> list[dict]:
    """Fetch executions from IBKR via reqExecutions."""
    from ib_insync import ExecutionFilter

    logger.info(f"Fetching executions from IBKR (last {days} days)...")
    ef = ExecutionFilter()
    execs = ib.reqExecutions(ef)
    logger.info(f"  raw fills: {len(execs)}")

    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for f in execs:
        try:
            ts = f.execution.time
            if ts < cutoff:
                continue
            out.append({
                "exec_id": f.execution.execId,
                "perm_id": str(f.execution.permId),
                "order_id": str(f.execution.orderId),
                "client_id": f.execution.clientId,
                "symbol": f.contract.symbol,
                "sec_type": f.contract.secType,
                "side": f.execution.side,  # BOT / SLD
                "qty": float(f.execution.shares),
                "price": float(f.execution.price),
                "time": ts.isoformat(),
                "commission": float(f.commissionReport.commission) if f.commissionReport else 0,
            })
        except Exception as e:
            logger.warning(f"  fill parse failed: {e}")
    logger.info(f"  within {days}d window: {len(out)}")
    return out


def pair_entries_exits(fills: list[dict]) -> list[dict]:
    """Pair BOT and SLD fills by symbol to form complete trades.

    Very simple FIFO matching: iterate fills chronologically, track opens
    per symbol, match each opposing fill as a close.
    """
    fills_sorted = sorted(fills, key=lambda f: f["time"])
    open_positions: dict[str, list[dict]] = {}  # symbol -> list of open fills
    trades = []

    for f in fills_sorted:
        sym = f["symbol"]
        side = f["side"]  # BOT or SLD
        qty = f["qty"]

        open_list = open_positions.setdefault(sym, [])
        opposite = "SLD" if (open_list and open_list[0]["side"] == "BOT") else ("BOT" if (open_list and open_list[0]["side"] == "SLD") else None)

        if open_list and side == opposite:
            # This fill closes an existing open position
            entry = open_list.pop(0)
            direction = "LONG" if entry["side"] == "BOT" else "SHORT"
            if direction == "LONG":
                pnl_gross = (f["price"] - entry["price"]) * qty
            else:
                pnl_gross = (entry["price"] - f["price"]) * qty
            trades.append({
                "trade_id": f"RECON_{entry['exec_id']}",
                "symbol": sym,
                "direction": direction,
                "qty": qty,
                "entry_price": entry["price"],
                "exit_price": f["price"],
                "entry_time": entry["time"],
                "exit_time": f["time"],
                "pnl_gross": pnl_gross,
                "commission": entry.get("commission", 0) + f.get("commission", 0),
                "pnl_net": pnl_gross - entry.get("commission", 0) - f.get("commission", 0),
                "exit_reason": "RECONCILED",
                "status": "closed",
            })
        else:
            # This is an opening fill
            open_list.append(f)

    # Any remaining open positions
    still_open = []
    for sym, ol in open_positions.items():
        for o in ol:
            still_open.append({
                "trade_id": f"RECON_OPEN_{o['exec_id']}",
                "symbol": sym,
                "direction": "LONG" if o["side"] == "BOT" else "SHORT",
                "qty": o["qty"],
                "entry_price": o["price"],
                "exit_price": None,
                "entry_time": o["time"],
                "exit_time": None,
                "pnl_gross": 0,
                "commission": o.get("commission", 0),
                "pnl_net": 0,
                "exit_reason": "OPEN",
                "status": "open",
            })

    return trades + still_open


def insert_into_journal(trades: list[dict], db_path: Path, dry_run: bool) -> int:
    if dry_run:
        logger.info(f"DRY-RUN: would insert {len(trades)} trades into {db_path}")
        for t in trades[:10]:
            logger.info(f"  {t['entry_time']} {t['symbol']} {t['direction']} qty={t['qty']} "
                        f"entry={t['entry_price']} exit={t['exit_price']} pnl=${t['pnl_net']:.2f}")
        if len(trades) > 10:
            logger.info(f"  ... and {len(trades) - 10} more")
        return 0

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    inserted = 0
    for t in trades:
        try:
            cur.execute(
                """INSERT OR IGNORE INTO trades (
                    trade_id, strategy, instrument, direction, quantity,
                    entry_price, exit_price, entry_time, exit_time,
                    pnl_gross, commission, pnl_net, exit_reason, status,
                    broker, asset_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IBKR', 'futures')""",
                (
                    t["trade_id"],
                    "RECONCILED",  # strategy unknown for historical reconstitution
                    t["symbol"],
                    t["direction"],
                    t["qty"],
                    t["entry_price"],
                    t["exit_price"],
                    t["entry_time"],
                    t["exit_time"],
                    t["pnl_gross"],
                    t["commission"],
                    t["pnl_net"],
                    t["exit_reason"],
                    t["status"],
                ),
            )
            if cur.rowcount:
                inserted += 1
        except Exception as e:
            logger.warning(f"  insert failed for {t['trade_id']}: {e}")
    con.commit()
    con.close()
    logger.info(f"Inserted {inserted}/{len(trades)} trades into {db_path}")
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7,
                        help="max days back (IBKR API limit typically 7d)")
    parser.add_argument("--execute", action="store_true",
                        help="actually write to live_journal.db")
    parser.add_argument("--port", type=int, default=4002,
                        help="IBKR port (4002 live, 4003 paper)")
    parser.add_argument("--db", default="live_journal.db")
    args = parser.parse_args()

    from ib_insync import IB, util
    util.startLoop()
    ib = IB()
    try:
        ib.connect("127.0.0.1", args.port, clientId=9970, timeout=15, readonly=True)
        logger.info(f"Connected IBKR port {args.port}")
    except Exception as e:
        logger.error(f"IBKR connect failed: {e}")
        return 1

    try:
        fills = fetch_executions(ib, days=args.days)
        if not fills:
            logger.warning("No fills found in window")
            return 0

        trades = pair_entries_exits(fills)
        logger.info(f"Paired into {len(trades)} trades")

        db_path = ROOT / "data" / args.db
        insert_into_journal(trades, db_path, dry_run=not args.execute)
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
