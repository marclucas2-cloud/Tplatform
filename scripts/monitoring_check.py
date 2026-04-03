"""
Monitoring Check — latency, slippage, fill rate, execution errors.

Queries broker APIs and logs to assess execution quality.

Usage:
    python scripts/monitoring_check.py
    python scripts/monitoring_check.py --json
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
logger = logging.getLogger("monitoring_check")

_results: list[dict] = []


def _record(name: str, value, status: str = "OK", detail: str = ""):
    _results.append({"name": name, "value": value, "status": status, "detail": detail})
    icon = {"OK": "✓", "WARN": "!", "FAIL": "✗"}.get(status, "?")
    logger.info(f"  [{icon}] {name}: {value} {detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. API LATENCY
# ═══════════════════════════════════════════════════════════════════════════════

def check_latency():
    """Measure API latency for each broker."""
    logger.info("── API LATENCY ──")

    # IBKR latency
    import socket
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    latencies = []
    for _ in range(3):
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=5):
                pass
            latencies.append((time.time() - t0) * 1000)
        except Exception:
            latencies.append(-1)
    valid = [l for l in latencies if l > 0]
    if valid:
        avg = sum(valid) / len(valid)
        status = "OK" if avg < 100 else "WARN" if avg < 300 else "FAIL"
        _record("IBKR latency", f"{avg:.0f}ms", status, f"(3 pings, {len(valid)}/3 OK)")
    else:
        _record("IBKR latency", "N/A", "FAIL", "all pings failed")

    # Binance API latency
    if os.getenv("BINANCE_API_KEY"):
        import urllib.request
        latencies_bnb = []
        for _ in range(3):
            t0 = time.time()
            try:
                url = "https://api.binance.com/api/v3/ping"
                urllib.request.urlopen(url, timeout=5)
                latencies_bnb.append((time.time() - t0) * 1000)
            except Exception:
                latencies_bnb.append(-1)
        valid_bnb = [l for l in latencies_bnb if l > 0]
        if valid_bnb:
            avg_bnb = sum(valid_bnb) / len(valid_bnb)
            status = "OK" if avg_bnb < 200 else "WARN" if avg_bnb < 500 else "FAIL"
            _record("Binance latency", f"{avg_bnb:.0f}ms", status,
                    f"(3 pings, {len(valid_bnb)}/3 OK)")
        else:
            _record("Binance latency", "N/A", "FAIL", "all pings failed")

    # Alpaca API latency
    if os.getenv("ALPACA_API_KEY"):
        import urllib.request
        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
        base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        latencies_alp = []
        for _ in range(3):
            t0 = time.time()
            try:
                req = urllib.request.Request(f"{base}/v2/clock", headers={
                    "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                    "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", ""),
                })
                urllib.request.urlopen(req, timeout=5)
                latencies_alp.append((time.time() - t0) * 1000)
            except Exception:
                latencies_alp.append(-1)
        valid_alp = [l for l in latencies_alp if l > 0]
        if valid_alp:
            avg_alp = sum(valid_alp) / len(valid_alp)
            status = "OK" if avg_alp < 200 else "WARN" if avg_alp < 500 else "FAIL"
            _record("Alpaca latency", f"{avg_alp:.0f}ms", status,
                    f"(3 pings, {len(valid_alp)}/3 OK)")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SLIPPAGE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def check_slippage():
    """Analyze slippage from trade journal."""
    logger.info("── SLIPPAGE ──")

    for db_name, mode in [("live_journal.db", "LIVE"), ("paper_journal.db", "PAPER")]:
        db_path = ROOT / "data" / db_name
        if not db_path.exists():
            _record(f"Slippage {mode}", "N/A", "OK", "no journal yet")
            continue

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Get recent trades with slippage data
            rows = conn.execute("""
                SELECT strategy, instrument,
                       entry_price_requested, entry_price_filled,
                       slippage_entry_bps,
                       exit_price_requested, exit_price_filled,
                       slippage_exit_bps
                FROM trades
                WHERE status = 'CLOSED'
                ORDER BY timestamp_closed DESC
                LIMIT 50
            """).fetchall()

            if not rows:
                _record(f"Slippage {mode}", "no trades", "OK")
                conn.close()
                continue

            entry_slips = [r["slippage_entry_bps"] for r in rows if r["slippage_entry_bps"] is not None]
            exit_slips = [r["slippage_exit_bps"] for r in rows if r["slippage_exit_bps"] is not None]

            if entry_slips:
                avg_entry = sum(entry_slips) / len(entry_slips)
                status = "OK" if abs(avg_entry) < 5 else "WARN" if abs(avg_entry) < 15 else "FAIL"
                _record(f"Slippage {mode} entry", f"{avg_entry:.1f}bps", status,
                        f"({len(entry_slips)} trades)")
            if exit_slips:
                avg_exit = sum(exit_slips) / len(exit_slips)
                status = "OK" if abs(avg_exit) < 5 else "WARN" if abs(avg_exit) < 15 else "FAIL"
                _record(f"Slippage {mode} exit", f"{avg_exit:.1f}bps", status,
                        f"({len(exit_slips)} trades)")

            conn.close()

        except Exception as e:
            _record(f"Slippage {mode}", "error", "WARN", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FILL RATE
# ═══════════════════════════════════════════════════════════════════════════════

def check_fill_rate():
    """Check order fill rate from journal."""
    logger.info("── FILL RATE ──")

    for db_name, mode in [("live_journal.db", "LIVE"), ("paper_journal.db", "PAPER")]:
        db_path = ROOT / "data" / db_name
        if not db_path.exists():
            continue

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))

            total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            filled = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status IN ('OPEN', 'CLOSED')"
            ).fetchone()[0]
            rejected = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status = 'REJECTED'"
            ).fetchone()[0]

            if total > 0:
                fill_rate = filled / total * 100
                status = "OK" if fill_rate > 95 else "WARN" if fill_rate > 80 else "FAIL"
                _record(f"Fill rate {mode}", f"{fill_rate:.0f}%", status,
                        f"{filled}/{total} filled, {rejected} rejected")
            else:
                _record(f"Fill rate {mode}", "no trades", "OK")

            conn.close()

        except Exception as e:
            _record(f"Fill rate {mode}", "error", "WARN", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EXECUTION ERRORS
# ═══════════════════════════════════════════════════════════════════════════════

def check_execution_errors():
    """Scan logs for recent execution errors."""
    logger.info("── EXECUTION ERRORS ──")

    log_file = ROOT / "logs" / "worker" / "worker.log"
    if not log_file.exists():
        _record("Execution errors", "no log", "WARN")
        return

    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        # Last 10K chars
        recent = content[-10000:] if len(content) > 10000 else content

        error_keywords = [
            "BrokerError", "ConnectionError", "TimeoutError",
            "order rejected", "insufficient", "margin call",
            "KILL SWITCH", "CLOSE_ALL", "permanently down",
        ]

        found_errors = []
        lines = recent.split("\n")
        for line in lines:
            for kw in error_keywords:
                if kw.lower() in line.lower():
                    found_errors.append(line.strip()[:120])
                    break

        if found_errors:
            status = "WARN" if len(found_errors) < 5 else "FAIL"
            _record("Execution errors", f"{len(found_errors)} found", status)
            for err in found_errors[:5]:
                logger.warning(f"    → {err}")
        else:
            _record("Execution errors", "none", "OK", "clean log")

    except Exception as e:
        _record("Execution errors", "error", "WARN", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SYSTEM RESOURCES
# ═══════════════════════════════════════════════════════════════════════════════

def check_system():
    """Check system resources (memory, disk)."""
    logger.info("── SYSTEM ──")

    try:
        import psutil

        # Memory
        mem = psutil.virtual_memory()
        _record("System RAM", f"{mem.percent}%",
                "OK" if mem.percent < 80 else "WARN" if mem.percent < 95 else "FAIL",
                f"{mem.available/1024/1024:.0f}MB available")

        # Disk
        disk = psutil.disk_usage(str(ROOT))
        _record("Disk space", f"{disk.percent}%",
                "OK" if disk.percent < 80 else "WARN" if disk.percent < 95 else "FAIL",
                f"{disk.free/1024/1024/1024:.1f}GB free")

        # Worker process check
        worker_found = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                if any('worker.py' in str(c) for c in cmdline):
                    worker_found = True
                    mem_mb = proc.memory_info().rss / 1024 / 1024
                    _record("Worker process", f"PID {proc.pid}",
                            "OK" if mem_mb < 500 else "WARN",
                            f"RAM={mem_mb:.0f}MB")
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not worker_found:
            _record("Worker process", "not found", "WARN", "worker.py not running")

    except ImportError:
        _record("System check", "psutil not installed", "WARN")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_all():
    start = time.time()

    logger.info("=" * 60)
    logger.info("  MONITORING CHECK")
    logger.info(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    check_latency()
    check_slippage()
    check_fill_rate()
    check_execution_errors()
    check_system()

    elapsed = time.time() - start

    n_ok = sum(1 for r in _results if r["status"] == "OK")
    n_warn = sum(1 for r in _results if r["status"] == "WARN")
    n_fail = sum(1 for r in _results if r["status"] == "FAIL")
    status = "OK" if n_fail == 0 else "DEGRADED"

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  MONITORING: {status}")
    logger.info(f"  {n_ok} OK / {n_warn} WARN / {n_fail} FAIL ({elapsed:.1f}s)")
    logger.info("=" * 60)

    return {
        "status": status,
        "results": _results,
        "ok": n_ok,
        "warn": n_warn,
        "fail": n_fail,
        "duration_s": round(elapsed, 1),
        "timestamp": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    result = run_all()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)
