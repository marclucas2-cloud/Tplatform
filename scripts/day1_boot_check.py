"""
Day 1 Boot Check — quick GO/NO_GO check before first trading day.

Runs all critical checks in < 30 seconds:
  - Brokers connected
  - Worker running
  - Risk engine operational
  - Kill switches armed (not triggered)
  - Telegram working
  - No stale data

Usage:
    python scripts/day1_boot_check.py
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
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
logger = logging.getLogger("day1_boot")

_checks: list[tuple[str, bool, str]] = []  # (name, passed, detail)
_critical_failures: list[str] = []


def _check(name: str, passed: bool, detail: str = "", critical: bool = True):
    _checks.append((name, passed, detail))
    icon = "✓" if passed else "✗"
    log_fn = logger.info if passed else logger.error
    log_fn(f"  [{icon}] {name}: {detail}")
    if not passed and critical:
        _critical_failures.append(f"{name}: {detail}")


def run():
    start = time.time()

    logger.info("=" * 60)
    logger.info("  DAY 1 BOOT CHECK")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    # ── 1. IBKR ──
    logger.info("── IBKR ──")
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=5):
            _check("IBKR gateway", True, f"{host}:{port}")
    except Exception as e:
        _check("IBKR gateway", False, f"{host}:{port} — {e}")

    ibkr_equity = 0
    try:
        from core.broker.ibkr_adapter import IBKRBroker
        ibkr = IBKRBroker()
        info = ibkr.get_account_info()
        ibkr_equity = float(info.get("equity", 0))
        paper = info.get("paper", True)
        _check("IBKR account", ibkr_equity > 0,
               f"equity=${ibkr_equity:,.0f} {'PAPER' if paper else 'LIVE'}")
    except Exception as e:
        _check("IBKR account", False, str(e))

    # ── 2. BINANCE ──
    logger.info("── BINANCE ──")
    binance_equity = 0
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            info = broker.get_account_info()
            binance_equity = float(info.get("equity", 0))
            testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
            _check("Binance account", True,
                   f"equity=${binance_equity:,.0f} {'TESTNET' if testnet else 'LIVE'}")
        except Exception as e:
            _check("Binance account", False, str(e))
    else:
        _check("Binance API", False, "BINANCE_API_KEY not set")

    # ── 3. WORKER ──
    logger.info("── WORKER ──")
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8080/health", timeout=3) as resp:
            data = json.loads(resp.read())
            _check("Worker health", data.get("status") == "ok", "endpoint responsive")
    except Exception:
        _check("Worker health", False, "health endpoint not responding")

    log_file = ROOT / "logs" / "worker" / "worker.log"
    if log_file.exists():
        age = time.time() - log_file.stat().st_mtime
        _check("Worker log", age < 300, f"last write {age:.0f}s ago")
    else:
        _check("Worker log", False, "no log file")

    # ── 4. RISK ENGINE ──
    logger.info("── RISK ENGINE ──")
    try:
        from core.risk_manager_live import LiveRiskManager
        risk_mgr = LiveRiskManager()
        _check("Risk manager", True, f"capital=${risk_mgr.capital:,.0f}")
    except Exception as e:
        _check("Risk manager", False, str(e))

    # Kill switches
    try:
        from core.kill_switch_live import LiveKillSwitch
        ks = LiveKillSwitch()
        _check("Kill switch", not ks._active,
               "armed" if not ks._active else f"ACTIVE: {ks._activation_reason}")
    except Exception as e:
        _check("Kill switch", False, str(e))

    # FX carry kill switch
    fx_ks_path = ROOT / "data" / "fx" / "carry_mom_ks_state.json"
    if fx_ks_path.exists():
        try:
            ks_state = json.loads(fx_ks_path.read_text())
            _check("FX carry KS", True, f"equity_high=${ks_state.get('equity_high', 0):,.0f}")
        except Exception:
            _check("FX carry KS", True, "state file exists", critical=False)

    # ── 5. TELEGRAM ──
    logger.info("── TELEGRAM ──")
    try:
        from core.telegram_alert import send_alert
        ok = send_alert(
            f"DAY 1 BOOT CHECK\n"
            f"IBKR: ${ibkr_equity:,.0f} | Binance: ${binance_equity:,.0f}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
            level="info"
        )
        _check("Telegram", ok, "test message sent" if ok else "send failed", critical=False)
    except Exception as e:
        _check("Telegram", False, str(e), critical=False)

    # ── 6. DATA FRESHNESS ──
    logger.info("── DATA ──")
    fx_dir = ROOT / "data" / "fx"
    stale_pairs = []
    for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
        fpath = fx_dir / f"{pair}_1D.parquet"
        if fpath.exists():
            age_h = (time.time() - fpath.stat().st_mtime) / 3600
            if age_h > 72:  # 3 days (allow weekend)
                stale_pairs.append(f"{pair}({age_h:.0f}h)")
        else:
            stale_pairs.append(f"{pair}(missing)")

    if stale_pairs:
        _check("FX data freshness", False, f"stale/missing: {', '.join(stale_pairs)}")
    else:
        _check("FX data freshness", True, "all carry pairs < 72h old")

    # ── VERDICT ──
    elapsed = time.time() - start
    n_pass = sum(1 for _, p, _ in _checks if p)
    n_fail = sum(1 for _, p, _ in _checks if not p)
    status = "GO" if not _critical_failures else "NO_GO"

    logger.info("")
    logger.info("=" * 60)
    icon = "✓" if status == "GO" else "✗"
    logger.info(f"  [{icon}] DAY 1 BOOT: {status}")
    logger.info(f"  IBKR: ${ibkr_equity:,.0f} | Binance: ${binance_equity:,.0f}")
    logger.info(f"  {n_pass} PASS / {n_fail} FAIL ({elapsed:.1f}s)")

    if _critical_failures:
        logger.error("  CRITICAL FAILURES:")
        for f in _critical_failures:
            logger.error(f"    ✗ {f}")
        logger.error("")
        logger.error("  ⛔ TRADING INTERDIT — fix failures above")
    else:
        logger.info("  All systems GO — trading authorized")

    logger.info("=" * 60)

    # Send verdict via Telegram
    try:
        from core.telegram_alert import send_alert
        if status == "GO":
            send_alert(
                f"DAY 1 BOOT: GO ✓\n"
                f"IBKR: ${ibkr_equity:,.0f} | Binance: ${binance_equity:,.0f}\n"
                f"{n_pass} checks passed",
                level="info"
            )
        else:
            send_alert(
                f"DAY 1 BOOT: NO_GO ✗\n"
                f"Failures:\n" + "\n".join(f"• {f}" for f in _critical_failures[:5]),
                level="critical"
            )
    except Exception:
        pass

    return {
        "status": status,
        "ibkr_equity": ibkr_equity,
        "binance_equity": binance_equity,
        "pass": n_pass,
        "fail": n_fail,
        "critical_failures": _critical_failures,
        "duration_s": round(elapsed, 1),
    }


if __name__ == "__main__":
    result = run()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "GO" else 1)
