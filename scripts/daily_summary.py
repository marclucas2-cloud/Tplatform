"""
Daily Summary — end-of-day report of all trading activity.

Generates:
  - Trades executed today (all brokers)
  - PnL by strategy
  - Errors and anomalies
  - Capital utilization
  - Risk metrics

Usage:
    python scripts/daily_summary.py
    python scripts/daily_summary.py --json
    python scripts/daily_summary.py --telegram   # Send summary via Telegram
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "intraday-backtesterV2"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("daily_summary")


def _get_journal_trades(db_path: Path, date_str: str) -> list[dict]:
    """Get today's trades from a journal database."""
    if not db_path.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT trade_id, strategy, instrument, instrument_type,
                   direction, quantity, entry_price_filled, exit_price_filled,
                   pnl_net, commission, exit_reason, status,
                   slippage_entry_bps, slippage_exit_bps,
                   holding_seconds
            FROM trades
            WHERE date(timestamp_filled) = ? OR date(timestamp_closed) = ?
            ORDER BY timestamp_filled
        """, (date_str, date_str)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Journal query error: {e}")
        return []


def _count_log_errors(date_str: str) -> dict:
    """Count errors in worker log for today."""
    log_file = ROOT / "logs" / "worker" / "worker.log"
    if not log_file.exists():
        return {"errors": 0, "warnings": 0, "criticals": 0}

    errors = warnings = criticals = 0
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if date_str not in line:
                    continue
                upper = line.upper()
                if "[CRITICAL]" in upper:
                    criticals += 1
                elif "[ERROR]" in upper:
                    errors += 1
                elif "[WARNING]" in upper:
                    warnings += 1
    except Exception:
        pass

    return {"errors": errors, "warnings": warnings, "criticals": criticals}


def _get_broker_equity() -> dict:
    """Get current equity from all brokers."""
    result = {"ibkr": 0, "binance": 0, "alpaca": 0}

    # IBKR
    try:
        import socket
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4002"))
        with socket.create_connection((host, port), timeout=3):
            pass
        from core.broker.ibkr_adapter import IBKRBroker
        info = IBKRBroker().get_account_info()
        result["ibkr"] = float(info.get("equity", 0))
    except Exception:
        pass

    # Binance
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            info = BinanceBroker().get_account_info()
            result["binance"] = float(info.get("equity", 0))
        except Exception:
            pass

    # Alpaca
    if os.getenv("ALPACA_API_KEY"):
        try:
            from core.alpaca_client.client import AlpacaClient
            info = AlpacaClient.from_env().get_account_info()
            result["alpaca"] = float(info.get("equity", 0))
        except Exception:
            pass

    return result


def generate_summary(date_str: str = None) -> dict:
    """Generate complete daily summary."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"  DAILY SUMMARY — {date_str}")
    logger.info("=" * 60)

    # ── Trades ──
    live_trades = _get_journal_trades(ROOT / "data" / "live_journal.db", date_str)
    paper_trades = _get_journal_trades(ROOT / "data" / "paper_journal.db", date_str)

    logger.info(f"── TRADES ──")
    logger.info(f"  Live: {len(live_trades)} | Paper: {len(paper_trades)}")

    # PnL by strategy
    pnl_by_strategy: dict[str, dict] = {}
    for trades, mode in [(live_trades, "LIVE"), (paper_trades, "PAPER")]:
        for t in trades:
            strat = t.get("strategy", "unknown")
            key = f"{mode}/{strat}"
            if key not in pnl_by_strategy:
                pnl_by_strategy[key] = {"pnl": 0, "trades": 0, "commission": 0}
            pnl_by_strategy[key]["pnl"] += t.get("pnl_net") or 0
            pnl_by_strategy[key]["trades"] += 1
            pnl_by_strategy[key]["commission"] += t.get("commission") or 0

    if pnl_by_strategy:
        logger.info("── PnL BY STRATEGY ──")
        for key, data in sorted(pnl_by_strategy.items(), key=lambda x: x[1]["pnl"], reverse=True):
            logger.info(f"  {key}: PnL=${data['pnl']:+.2f} ({data['trades']} trades, comm=${data['commission']:.2f})")

    total_live_pnl = sum(t.get("pnl_net") or 0 for t in live_trades if t.get("status") == "CLOSED")
    total_paper_pnl = sum(t.get("pnl_net") or 0 for t in paper_trades if t.get("status") == "CLOSED")

    logger.info(f"  Total Live PnL: ${total_live_pnl:+.2f}")
    logger.info(f"  Total Paper PnL: ${total_paper_pnl:+.2f}")

    # ── Errors & Anomalies ──
    log_stats = _count_log_errors(date_str)
    logger.info("── ERRORS & ANOMALIES ──")
    logger.info(f"  Criticals: {log_stats['criticals']} | Errors: {log_stats['errors']} | Warnings: {log_stats['warnings']}")

    anomalies = []
    # Check for large losses
    for t in live_trades + paper_trades:
        pnl = t.get("pnl_net") or 0
        if pnl < -200:
            anomalies.append(f"Large loss: {t.get('instrument')} ${pnl:.2f} ({t.get('strategy')})")
    # Check for high slippage
    for t in live_trades:
        slip = t.get("slippage_entry_bps")
        if slip and abs(slip) > 10:
            anomalies.append(f"High slippage: {t.get('instrument')} {slip:.1f}bps ({t.get('strategy')})")

    if anomalies:
        logger.warning("  Anomalies:")
        for a in anomalies:
            logger.warning(f"    ! {a}")

    # ── Capital ──
    equity = _get_broker_equity()
    total_equity = sum(equity.values())
    logger.info("── CAPITAL ──")
    for broker, eq in equity.items():
        if eq > 0:
            logger.info(f"  {broker.upper()}: ${eq:,.0f}")
    logger.info(f"  TOTAL: ${total_equity:,.0f}")

    # ── Open Positions ──
    open_live = [t for t in live_trades if t.get("status") == "OPEN"]
    open_paper = [t for t in paper_trades if t.get("status") == "OPEN"]
    if open_live or open_paper:
        logger.info("── OPEN POSITIONS ──")
        for t in open_live:
            logger.info(f"  LIVE: {t.get('instrument')} {t.get('direction')} @{t.get('entry_price_filled')}")
        for t in open_paper:
            logger.info(f"  PAPER: {t.get('instrument')} {t.get('direction')} @{t.get('entry_price_filled')}")

    # ── Summary dict ──
    summary = {
        "date": date_str,
        "trades": {
            "live": len(live_trades),
            "paper": len(paper_trades),
        },
        "pnl": {
            "live": round(total_live_pnl, 2),
            "paper": round(total_paper_pnl, 2),
        },
        "pnl_by_strategy": {k: {"pnl": round(v["pnl"], 2), "trades": v["trades"]}
                            for k, v in pnl_by_strategy.items()},
        "errors": log_stats,
        "anomalies": anomalies,
        "equity": {k: round(v, 0) for k, v in equity.items() if v > 0},
        "total_equity": round(total_equity, 0),
        "open_positions": {
            "live": len(open_live),
            "paper": len(open_paper),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Save to file
    summary_dir = ROOT / "logs" / "daily_summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"summary_{date_str}.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info(f"\n  Summary saved to {summary_path}")

    return summary


def _format_telegram(summary: dict) -> str:
    """Format summary for Telegram."""
    d = summary["date"]
    lines = [
        f"<b>DAILY SUMMARY — {d}</b>",
        "",
        f"Trades: {summary['trades']['live']} live / {summary['trades']['paper']} paper",
        f"PnL Live: ${summary['pnl']['live']:+.2f}",
        f"PnL Paper: ${summary['pnl']['paper']:+.2f}",
        "",
    ]

    if summary["pnl_by_strategy"]:
        lines.append("<b>By Strategy:</b>")
        for k, v in sorted(summary["pnl_by_strategy"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            lines.append(f"  {k}: ${v['pnl']:+.2f} ({v['trades']}t)")
        lines.append("")

    eq = summary.get("equity", {})
    if eq:
        lines.append("<b>Capital:</b>")
        for broker, val in eq.items():
            lines.append(f"  {broker.upper()}: ${val:,.0f}")
        lines.append(f"  TOTAL: ${summary['total_equity']:,.0f}")

    errs = summary.get("errors", {})
    if errs.get("criticals", 0) or errs.get("errors", 0):
        lines.append(f"\nErrors: {errs['criticals']}C / {errs['errors']}E / {errs['warnings']}W")

    if summary.get("anomalies"):
        lines.append("\nAnomalies:")
        for a in summary["anomalies"][:3]:
            lines.append(f"  ! {a}")

    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]

    # Allow custom date
    date_str = None
    for a in args:
        if a.startswith("--date="):
            date_str = a.split("=")[1]

    summary = generate_summary(date_str)

    if "--json" in args:
        print(json.dumps(summary, indent=2))

    if "--telegram" in args:
        try:
            from core.telegram_alert import _send_message
            msg = _format_telegram(summary)
            ok = _send_message(msg, parse_mode="HTML")
            if ok:
                logger.info("  Summary sent via Telegram")
            else:
                logger.warning("  Telegram send failed")
        except Exception as e:
            logger.warning(f"  Telegram: {e}")
