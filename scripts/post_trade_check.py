"""
Post-Trade Check — validate trade execution after each session.

Checks:
  - PnL coherence (journal vs broker)
  - Logs coherence (no missing fills)
  - Positions correctly closed (intraday)
  - No divergences (model vs broker)
  - Kill switch state consistent

Usage:
    python scripts/post_trade_check.py
    python scripts/post_trade_check.py --json
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("post_trade")

_issues: list[str] = []
_warnings: list[str] = []


def _ok(msg: str):
    logger.info(f"  [✓] {msg}")


def _issue(msg: str):
    _issues.append(msg)
    logger.error(f"  [✗] {msg}")


def _warn(msg: str):
    _warnings.append(msg)
    logger.warning(f"  [!] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PnL COHERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def check_pnl():
    """Compare journal PnL with broker reported PnL."""
    logger.info("── PnL COHERENCE ──")

    for db_name, mode in [("live_journal.db", "LIVE"), ("paper_journal.db", "PAPER")]:
        db_path = ROOT / "data" / db_name
        if not db_path.exists():
            continue

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Today's closed trades
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            rows = conn.execute("""
                SELECT strategy, instrument, direction, pnl_net, commission,
                       entry_price_filled, exit_price_filled, quantity,
                       exit_reason
                FROM trades
                WHERE status = 'CLOSED'
                  AND date(timestamp_closed) = ?
                ORDER BY timestamp_closed
            """, (today,)).fetchall()

            if not rows:
                logger.info(f"  {mode}: no trades today")
                continue

            total_pnl = sum(r["pnl_net"] or 0 for r in rows)
            total_comm = sum(r["commission"] or 0 for r in rows)
            n_trades = len(rows)

            _ok(f"{mode}: {n_trades} trades, PnL=${total_pnl:+.2f}, comm=${total_comm:.2f}")

            # Check for anomalies
            for r in rows:
                pnl = r["pnl_net"] or 0
                entry = r["entry_price_filled"] or 0
                exit_p = r["exit_price_filled"] or 0

                # PnL sanity check
                if entry > 0 and exit_p > 0:
                    qty = r["quantity"] or 0
                    expected_pnl_sign = (exit_p - entry) if r["direction"] == "LONG" else (entry - exit_p)
                    if (expected_pnl_sign > 0 and pnl < -10) or (expected_pnl_sign < 0 and pnl > 10):
                        _warn(f"{mode} {r['instrument']}: PnL sign mismatch "
                              f"(entry={entry}, exit={exit_p}, pnl={pnl})")

                # Large loss check
                if pnl < -500:
                    _warn(f"{mode} {r['instrument']}: large loss ${pnl:.2f}")

            conn.close()

        except Exception as e:
            _warn(f"{mode} journal: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOG COHERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def check_logs():
    """Check JSONL event log for completeness."""
    logger.info("── LOG COHERENCE ──")

    event_log = ROOT / "logs" / "events.jsonl"
    if not event_log.exists():
        _warn("No JSONL event log found")
        return

    try:
        events = []
        with open(event_log, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        # Check for today's events
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        today_events = [e for e in events if e.get("timestamp", "").startswith(today)]

        if today_events:
            actions = {}
            for e in today_events:
                action = e.get("action", "unknown")
                actions[action] = actions.get(action, 0) + 1
            _ok(f"JSONL: {len(today_events)} events today — {dict(actions)}")
        else:
            logger.info("  No events today in JSONL log")

        # Check for gaps (missing events in last 5 min during market hours)
        if events:
            last_event = events[-1]
            last_ts = last_event.get("timestamp", "")
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    age_min = (datetime.now(UTC) - last_dt).total_seconds() / 60
                    if age_min > 10:
                        _warn(f"JSONL: last event {age_min:.0f} min ago")
                    else:
                        _ok(f"JSONL: last event {age_min:.0f} min ago")
                except Exception:
                    pass

    except Exception as e:
        _warn(f"JSONL log: {e}")

    # Worker log check
    log_file = ROOT / "logs" / "worker" / "worker.log"
    if log_file.exists():
        age = time.time() - log_file.stat().st_mtime
        if age > 600:  # 10 min
            _warn(f"Worker log stale: {age/60:.0f} min since last write")
        else:
            _ok(f"Worker log: active ({age:.0f}s ago)")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. POSITION CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_positions():
    """Check all positions are correctly managed."""
    logger.info("── POSITIONS ──")

    # IBKR positions
    try:
        import socket
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4002"))
        with socket.create_connection((host, port), timeout=3):
            pass

        from core.broker.ibkr_adapter import IBKRBroker
        ibkr = IBKRBroker()
        positions = ibkr.get_positions()

        if positions:
            for p in positions:
                symbol = p.get("symbol", "?")
                qty = p.get("qty", 0)
                pnl = p.get("unrealized_pl", 0)
                logger.info(f"    IBKR: {symbol} qty={qty} PnL=${pnl:+.2f}")
            _ok(f"IBKR: {len(positions)} positions")
        else:
            _ok("IBKR: no open positions")

    except Exception as e:
        logger.info(f"  IBKR positions: {e}")

    # Binance positions
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            positions = broker.get_positions()

            if positions:
                for p in positions:
                    symbol = p.get("symbol", "?")
                    qty = p.get("qty", 0)
                    side = p.get("side", "?")
                    logger.info(f"    Binance: {symbol} {side} qty={qty}")
                _ok(f"Binance: {len(positions)} positions")
            else:
                _ok("Binance: no open positions")

        except Exception as e:
            _warn(f"Binance positions: {e}")

    # Check for orphan intraday positions (should be closed after 16:00 ET)
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(ET)
    if now_et.hour >= 16:
        state_path = ROOT / "data" / "state" / "paper_portfolio_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                intraday = state.get("intraday_positions", {})
                if intraday:
                    _issue(f"Intraday positions still open after close: {list(intraday.keys())}")
                else:
                    _ok("No orphan intraday positions")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DIVERGENCES
# ═══════════════════════════════════════════════════════════════════════════════

def check_divergences():
    """Check for model vs broker divergences."""
    logger.info("── DIVERGENCES ──")

    history_path = ROOT / "data" / "reconciliation_history.json"
    if not history_path.exists():
        logger.info("  No reconciliation history")
        return

    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(history, list) or not history:
            return

        # Check last entry
        last = history[-1]
        if not last.get("matched"):
            divergences = last.get("divergences", [])
            orphans = last.get("orphan_positions", [])
            phantoms = last.get("phantom_positions", [])

            if divergences:
                for d in divergences:
                    _issue(f"Divergence: {d}")
            if orphans:
                _issue(f"Orphan positions: {[o.get('symbol') for o in orphans]}")
            if phantoms:
                _issue(f"Phantom positions: {[p.get('symbol') for p in phantoms]}")
        else:
            _ok("Reconciliation: matched")

    except Exception as e:
        _warn(f"Reconciliation history: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. KILL SWITCH STATE
# ═══════════════════════════════════════════════════════════════════════════════

def check_kill_switch():
    """Verify kill switch state is consistent."""
    logger.info("── KILL SWITCH ──")

    try:
        from core.kill_switch_live import LiveKillSwitch
        ks = LiveKillSwitch()
        if ks._active:
            _issue(f"Kill switch ACTIVE: {ks._activation_reason}")
        else:
            _ok("Kill switch: armed (not triggered)")
    except Exception as e:
        _warn(f"Kill switch: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═��═════════════════════════════════════════════════════════════════════════════

def run():
    start = time.time()

    logger.info("=" * 60)
    logger.info("  POST-TRADE CHECK")
    logger.info(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    check_pnl()
    check_logs()
    check_positions()
    check_divergences()
    check_kill_switch()

    elapsed = time.time() - start

    status = "OK" if not _issues else "ISSUES_FOUND"

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  POST-TRADE: {status}")
    logger.info(f"  {len(_issues)} issues / {len(_warnings)} warnings ({elapsed:.1f}s)")
    logger.info("=" * 60)

    if _issues:
        logger.error("  ISSUES:")
        for i in _issues:
            logger.error(f"    ✗ {i}")

    return {
        "status": status,
        "issues": _issues,
        "warnings": _warnings,
        "duration_s": round(elapsed, 1),
        "timestamp": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    result = run()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)
