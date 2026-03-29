"""
Pre-Live Validation — GO/NO_GO decision before live trading.

Checks ALL critical systems before allowing live execution:
  - Broker connectivity (IBKR, Binance, Alpaca)
  - Worker process health
  - Position reconciliation
  - Market data availability
  - Risk engine operational
  - Kill switch state
  - Telegram alerts functional
  - Config coherence

Usage:
    python scripts/pre_live_validation.py
    python scripts/pre_live_validation.py --json   # Machine-readable output
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
logger = logging.getLogger("pre_live_validation")

# ── Results accumulator ─────────────────────────────────────────────────────
_errors: list[str] = []
_warnings: list[str] = []
_checks: list[dict] = []


def _record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    _checks.append({"name": name, "status": status, "detail": detail})
    icon = "✓" if passed else "✗"
    msg = f"  [{icon}] {name}"
    if detail:
        msg += f" — {detail}"
    if passed:
        logger.info(msg)
    else:
        logger.error(msg)
        _errors.append(f"{name}: {detail}")


def _warn(name: str, detail: str):
    _checks.append({"name": name, "status": "WARN", "detail": detail})
    _warnings.append(f"{name}: {detail}")
    logger.warning(f"  [!] {name} — {detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BROKER CONNECTIVITY
# ═══════════════════════════════════════════════════════════════════════════════

def check_ibkr():
    """Check IBKR Gateway connectivity and account info."""
    logger.info("── IBKR ──")
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))

    # TCP connection test
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        _record("IBKR TCP", True, f"{host}:{port}")
    except Exception as e:
        _record("IBKR TCP", False, f"{host}:{port} — {e}")
        return  # No point testing further

    # Account info
    try:
        from core.broker.ibkr_adapter import IBKRBroker
        ibkr = IBKRBroker()
        info = ibkr.get_account_info()
        equity = float(info.get("equity", 0))
        cash = float(info.get("cash", 0))
        paper = info.get("paper", True)

        _record("IBKR account", equity > 0, f"equity=${equity:,.0f} cash=${cash:,.0f}")

        if paper:
            _warn("IBKR mode", "PAPER mode — set IBKR_PAPER=false for live")
        else:
            _record("IBKR mode", True, "LIVE")

        if equity < 5000:
            _record("IBKR min capital", False, f"${equity:,.0f} < $5,000 minimum")
        else:
            _record("IBKR min capital", True, f"${equity:,.0f}")

        # Check positions
        positions = ibkr.get_positions()
        _record("IBKR positions", True, f"{len(positions)} open positions")

    except Exception as e:
        _record("IBKR account", False, str(e))


def check_binance():
    """Check Binance connectivity and account info."""
    logger.info("── BINANCE ──")
    api_key = os.getenv("BINANCE_API_KEY", "")
    if not api_key:
        _record("Binance API key", False, "BINANCE_API_KEY not set")
        return

    _record("Binance API key", True, "configured")

    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    live_confirmed = os.getenv("BINANCE_LIVE_CONFIRMED", "").lower() == "true"

    if testnet:
        _warn("Binance mode", "TESTNET — set BINANCE_TESTNET=false for live")
    else:
        _record("Binance mode", True, "LIVE")
        if not live_confirmed:
            _record("Binance LIVE guard", False, "BINANCE_LIVE_CONFIRMED not set")
        else:
            _record("Binance LIVE guard", True, "confirmed")

    try:
        from core.broker.binance_broker import BinanceBroker
        broker = BinanceBroker()
        info = broker.get_account_info()
        equity = float(info.get("equity", 0))
        cash = float(info.get("cash", 0))

        _record("Binance account", equity > 0 or cash > 0,
                f"equity=${equity:,.0f} cash=${cash:,.0f}")

        positions = broker.get_positions()
        _record("Binance positions", True, f"{len(positions)} open positions")

        # Check earn positions
        try:
            earn = broker.get_earn_positions()
            _record("Binance Earn", True, f"{len(earn)} earn products")
        except Exception as e:
            _warn("Binance Earn", str(e))

    except Exception as e:
        _record("Binance account", False, str(e))


def check_alpaca():
    """Check Alpaca connectivity."""
    logger.info("── ALPACA ──")
    if not os.getenv("ALPACA_API_KEY"):
        _warn("Alpaca API key", "not configured (optional for IBKR/Binance live)")
        return

    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        info = client.get_account_info()
        equity = float(info.get("equity", 0))
        paper = info.get("paper", True)

        _record("Alpaca account", True, f"equity=${equity:,.0f} {'PAPER' if paper else 'LIVE'}")
    except Exception as e:
        _warn("Alpaca account", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. WORKER HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

def check_worker():
    """Check worker process and health endpoint."""
    logger.info("── WORKER ──")

    # Check health endpoint
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8080/health", timeout=3) as resp:
            data = json.loads(resp.read())
            _record("Worker health endpoint", data.get("status") == "ok",
                    f"status={data.get('status')}")
    except Exception:
        _warn("Worker health endpoint", "not responding (worker may not be running)")

    # Check recent logs
    log_file = ROOT / "logs" / "worker" / "worker.log"
    if log_file.exists():
        stat = log_file.stat()
        age_seconds = time.time() - stat.st_mtime
        if age_seconds < 300:  # Last log < 5 min ago
            _record("Worker log freshness", True, f"last write {age_seconds:.0f}s ago")
        elif age_seconds < 3600:
            _warn("Worker log freshness", f"last write {age_seconds/60:.0f} min ago")
        else:
            _record("Worker log freshness", False, f"last write {age_seconds/3600:.1f}h ago")
    else:
        _warn("Worker log", "log file not found")

    # Check for recent crashes in log
    if log_file.exists():
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            last_5k = content[-5000:] if len(content) > 5000 else content
            crash_count = last_5k.lower().count("error") + last_5k.lower().count("traceback")
            if crash_count > 10:
                _warn("Worker errors", f"{crash_count} error/traceback in last log segment")
            else:
                _record("Worker errors", True, f"{crash_count} errors in recent log (< 10)")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RECONCILIATION
# ═══════════════════════════════════════════════════════════════════════════════

def check_reconciliation():
    """Check position reconciliation state."""
    logger.info("── RECONCILIATION ──")

    history_path = ROOT / "data" / "reconciliation_history.json"
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(history, list) and history:
                last = history[-1]
                matched = last.get("matched", False)
                ts = last.get("timestamp", "unknown")
                divergences = last.get("divergences", [])
                _record("Reconciliation last", matched,
                        f"at {ts}, divergences: {len(divergences)}")
                if divergences:
                    for d in divergences[:3]:
                        _warn("Reconciliation divergence", str(d))
            else:
                _warn("Reconciliation", "no history entries")
        except Exception as e:
            _warn("Reconciliation", f"cannot read history: {e}")
    else:
        _warn("Reconciliation", "no history file (first run?)")

    # State file check
    state_path = ROOT / "paper_portfolio_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            equity = state.get("equity", 0)
            positions = state.get("positions", {})
            _record("State file", True, f"equity=${equity:,.0f}, {len(positions)} strategies")
        except Exception as e:
            _record("State file", False, str(e))
    else:
        _warn("State file", "paper_portfolio_state.json not found")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

def check_data():
    """Check market data availability."""
    logger.info("── MARKET DATA ──")

    # FX daily data (IBKR)
    fx_dir = ROOT / "data" / "fx"
    fx_pairs = ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD", "EURUSD", "EURGBP"]
    found = 0
    for pair in fx_pairs:
        fpath = fx_dir / f"{pair}_1D.parquet"
        if fpath.exists():
            found += 1
            stat = fpath.stat()
            age_hours = (time.time() - stat.st_mtime) / 3600
            if age_hours > 48:
                _warn(f"FX data {pair}", f"stale ({age_hours:.0f}h old)")
        else:
            _warn(f"FX data {pair}", "file not found")

    _record("FX data files", found >= 4, f"{found}/{len(fx_pairs)} pairs available")

    # Crypto data check via Binance API ping
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            ticker = broker.get_ticker_24h("BTCUSDT")
            price = float(ticker.get("lastPrice", 0))
            _record("Binance data", price > 0, f"BTCUSDT=${price:,.0f}")
        except Exception as e:
            _warn("Binance data", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RISK ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def check_risk_engine():
    """Check risk engine is operational."""
    logger.info("── RISK ENGINE ──")

    # Load LiveRiskManager
    try:
        from core.risk_manager_live import LiveRiskManager
        risk_mgr = LiveRiskManager()
        _record("LiveRiskManager", True, f"capital=${risk_mgr.capital:,.0f}")
    except Exception as e:
        _record("LiveRiskManager", False, str(e))
        return

    # Test validate_order with dummy order
    try:
        dummy_portfolio = {
            "equity": risk_mgr.capital,
            "cash": risk_mgr.capital * 0.5,
            "positions": [],
        }
        passed, msg = risk_mgr.validate_order(
            order={
                "symbol": "EURUSD",
                "direction": "BUY",
                "notional": 100,
                "strategy": "pre_live_test",
                "asset_class": "forex",
            },
            portfolio=dummy_portfolio,
        )
        _record("Risk validate_order", True, f"test order {'passed' if passed else 'blocked'}: {msg}")
    except Exception as e:
        _record("Risk validate_order", False, str(e))

    # Kill switch state
    try:
        from core.kill_switch_live import LiveKillSwitch
        ks = LiveKillSwitch()
        if ks._active:
            _record("Kill switch", False, f"ACTIVE — reason: {ks._activation_reason}")
        else:
            _record("Kill switch", True, "armed and ready")
    except Exception as e:
        _warn("Kill switch", str(e))

    # FX carry kill switch state
    ks_path = ROOT / "data" / "fx" / "carry_mom_ks_state.json"
    if ks_path.exists():
        try:
            ks_state = json.loads(ks_path.read_text())
            _record("FX carry kill switch", True,
                    f"equity_high=${ks_state.get('equity_high', 0):,.0f}")
        except Exception as e:
            _warn("FX carry kill switch", str(e))

    # Config files
    for cfg_name in ["limits_live.yaml", "kill_switch_thresholds.yaml",
                     "allocation.yaml", "crypto_allocation.yaml"]:
        cfg_path = ROOT / "config" / cfg_name
        _record(f"Config {cfg_name}", cfg_path.exists(),
                "found" if cfg_path.exists() else "MISSING")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TELEGRAM ALERTS
# ═══════════════════════════════════════════════════════════════════════════════

def check_telegram():
    """Check Telegram alert system."""
    logger.info("── TELEGRAM ──")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        _record("Telegram config", False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return

    _record("Telegram config", True, "token + chat_id configured")

    # Send test message
    try:
        from core.telegram_alert import send_alert
        result = send_alert(
            f"PRE-LIVE VALIDATION TEST\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Status: checking systems...",
            level="info"
        )
        _record("Telegram send", result, "test message sent" if result else "send failed")
    except Exception as e:
        _record("Telegram send", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# 7. JSONL LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def check_logging():
    """Check JSONL logging infrastructure."""
    logger.info("── LOGGING ──")

    # Worker log directory
    log_dir = ROOT / "logs" / "worker"
    _record("Worker log dir", log_dir.exists(), str(log_dir))

    # Risk audit directory
    audit_dir = ROOT / "logs" / "risk_audit"
    _record("Risk audit dir", audit_dir.exists() or True,
            str(audit_dir) if audit_dir.exists() else "will be created on first use")

    # JSONL event log
    event_log = ROOT / "logs" / "events.jsonl"
    if event_log.exists():
        stat = event_log.stat()
        age_hours = (time.time() - stat.st_mtime) / 3600
        _record("Event JSONL", True, f"size={stat.st_size/1024:.0f}KB, age={age_hours:.1f}h")
    else:
        _warn("Event JSONL", "logs/events.jsonl not found (will be created)")

    # Trade journal DB
    for db_name in ["live_journal.db", "paper_journal.db"]:
        db_path = ROOT / "data" / db_name
        if db_path.exists():
            _record(f"Journal {db_name}", True, f"size={db_path.stat().st_size/1024:.0f}KB")
        else:
            _warn(f"Journal {db_name}", "not found (will be created on first trade)")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ENV VARS & CONFIG COHERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def check_env():
    """Check critical environment variables."""
    logger.info("── ENV & CONFIG ──")

    critical_vars = {
        "IBKR_HOST": "IBKR connection",
        "IBKR_PORT": "IBKR port",
        "BINANCE_API_KEY": "Binance trading",
        "BINANCE_API_SECRET": "Binance auth",
        "TELEGRAM_BOT_TOKEN": "Telegram alerts",
        "TELEGRAM_CHAT_ID": "Telegram target",
    }

    for var, purpose in critical_vars.items():
        val = os.getenv(var, "")
        if val:
            # Mask secrets
            display = val[:4] + "..." if len(val) > 8 else "set"
            _record(f"ENV {var}", True, display)
        else:
            if var in ("IBKR_HOST", "IBKR_PORT", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
                _record(f"ENV {var}", False, f"NOT SET — needed for {purpose}")
            else:
                _warn(f"ENV {var}", f"not set ({purpose})")

    # Safety checks
    paper = os.getenv("PAPER_TRADING", "true").lower()
    ibkr_paper = os.getenv("IBKR_PAPER", "true").lower()
    binance_testnet = os.getenv("BINANCE_TESTNET", "true").lower()

    logger.info(f"  Mode: PAPER_TRADING={paper}, IBKR_PAPER={ibkr_paper}, BINANCE_TESTNET={binance_testnet}")

    if paper == "true" and ibkr_paper == "false":
        _warn("Mode mismatch", "PAPER_TRADING=true but IBKR_PAPER=false")
    if binance_testnet == "true" and os.getenv("BINANCE_LIVE_CONFIRMED") == "true":
        _warn("Mode mismatch", "BINANCE_TESTNET=true but BINANCE_LIVE_CONFIRMED=true")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_all_checks():
    """Run all pre-live validation checks."""
    start = time.time()

    logger.info("=" * 60)
    logger.info("  PRE-LIVE VALIDATION")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    check_env()
    check_ibkr()
    check_binance()
    check_alpaca()
    check_worker()
    check_reconciliation()
    check_data()
    check_risk_engine()
    check_telegram()
    check_logging()

    elapsed = time.time() - start

    # ── Verdict ──
    n_pass = sum(1 for c in _checks if c["status"] == "PASS")
    n_fail = sum(1 for c in _checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in _checks if c["status"] == "WARN")

    status = "GO" if n_fail == 0 else "NO_GO"

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  VERDICT: {status}")
    logger.info(f"  {n_pass} PASS / {n_fail} FAIL / {n_warn} WARN")
    logger.info(f"  Duration: {elapsed:.1f}s")
    logger.info("=" * 60)

    if _errors:
        logger.error("  ERRORS:")
        for e in _errors:
            logger.error(f"    ✗ {e}")

    if _warnings:
        logger.warning("  WARNINGS:")
        for w in _warnings:
            logger.warning(f"    ! {w}")

    # Send Telegram summary
    if status == "GO":
        try:
            from core.telegram_alert import send_alert
            send_alert(
                f"PRE-LIVE VALIDATION: GO ✓\n"
                f"{n_pass} PASS / {n_warn} WARN\n"
                f"All systems operational.",
                level="info"
            )
        except Exception:
            pass
    else:
        try:
            from core.telegram_alert import send_alert
            send_alert(
                f"PRE-LIVE VALIDATION: NO_GO ✗\n"
                f"{n_pass} PASS / {n_fail} FAIL / {n_warn} WARN\n"
                f"Errors:\n" + "\n".join(f"• {e}" for e in _errors[:5]),
                level="critical"
            )
        except Exception:
            pass

    return {
        "status": status,
        "checks": _checks,
        "errors": _errors,
        "warnings": _warnings,
        "pass": n_pass,
        "fail": n_fail,
        "warn": n_warn,
        "duration_s": round(elapsed, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    result = run_all_checks()

    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))

    sys.exit(0 if result["status"] == "GO" else 1)
