"""
IB Gateway Watchdog — auto-detect disconnect, alert, restart with 2FA flow.

Runs as a daemon alongside the worker. Checks IB Gateway every 60 seconds.

Flow when Gateway goes down:
  1. Detect port 4002 unreachable
  2. Wait 2 min (transient reconnect)
  3. If still down → Telegram alert: "IB Gateway DOWN — restarting in 2 min"
  4. Wait 2 min for user to be ready (2FA push on phone)
  5. Restart ibgateway service
  6. Telegram: "IB Gateway restarting — approve 2FA on IB Key app NOW"
  7. Wait up to 90s for port 4002
  8. If up → Telegram: "IB Gateway BACK UP — equity=$X"
  9. If still down → Telegram: "IB Gateway FAILED to restart — manual intervention needed"

Usage:
    python scripts/ibgateway_watchdog.py          # Run as daemon
    python scripts/ibgateway_watchdog.py --once    # Single check then exit
    python scripts/ibgateway_watchdog.py --restart  # Force restart now
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "ibgateway_watchdog.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("ib_watchdog")

# ── Config ──
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4002"))
CHECK_INTERVAL = 60          # Check every 60s
TRANSIENT_WAIT = 120         # Wait 2 min before declaring down (transient reconnect)
PRE_RESTART_WAIT = 120       # Wait 2 min after alert before restart (user prepares 2FA)
POST_RESTART_TIMEOUT = 90    # Wait 90s for Gateway to come back after restart
COOLDOWN_AFTER_RESTART = 300 # Don't re-trigger for 5 min after restart


def _send_telegram(message: str, level: str = "info") -> bool:
    """Send Telegram alert."""
    try:
        from core.telegram_alert import send_alert
        return send_alert(message, level=level)
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")
        return False


def _check_port(host: str = IBKR_HOST, port: int = IBKR_PORT, timeout: int = 5) -> bool:
    """Check if IB Gateway port is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _check_account() -> dict | None:
    """Try to get IBKR account info. Returns dict or None."""
    try:
        os.environ["IBKR_PORT"] = str(IBKR_PORT)
        os.environ["IBKR_HOST"] = IBKR_HOST
        os.environ["IBKR_PAPER"] = os.getenv("IBKR_PAPER", "false")
        from core.broker.ibkr_adapter import IBKRBroker
        # Clear broker cache to force fresh connection
        from core.broker.factory import _broker_cache
        _broker_cache.pop("ibkr", None)
        # clientId=99 pour eviter conflit avec worker (clientId=1)
        broker = IBKRBroker(client_id=99)
        info = broker.get_account_info()
        return info
    except Exception as e:
        logger.debug(f"Account check failed: {e}")
        return None


def _restart_gateway() -> bool:
    """Restart IB Gateway via systemctl."""
    logger.info("Restarting ibgateway service...")
    try:
        result = subprocess.run(
            ["systemctl", "restart", "ibgateway"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("ibgateway service restart command sent")
            return True
        else:
            logger.error(f"ibgateway restart failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Cannot restart ibgateway: {e}")
        return False


def _wait_for_gateway(timeout: int = POST_RESTART_TIMEOUT) -> bool:
    """Wait for Gateway port to become available."""
    logger.info(f"Waiting up to {timeout}s for port {IBKR_PORT}...")
    for i in range(timeout):
        if _check_port():
            logger.info(f"Port {IBKR_PORT} reachable after {i+1}s")
            return True
        time.sleep(1)
    return False


def handle_gateway_down():
    """Full recovery sequence when Gateway is detected down."""

    # ── Step 1: Wait for transient reconnect ──
    logger.warning(f"IB Gateway port {IBKR_PORT} unreachable — waiting {TRANSIENT_WAIT}s for transient reconnect...")

    for i in range(TRANSIENT_WAIT):
        time.sleep(1)
        if _check_port():
            logger.info(f"IB Gateway reconnected after {i+1}s (transient)")
            return True

    # ── Step 2: Confirmed down — alert user ──
    logger.critical("IB Gateway confirmed DOWN after transient wait")
    _send_telegram(
        f"IB Gateway DOWN\n"
        f"Port {IBKR_PORT} unreachable depuis {TRANSIENT_WAIT}s\n\n"
        f"Restart automatique dans {PRE_RESTART_WAIT // 60} min\n"
        f"Prepare ton telephone — tu recevras un push 2FA IB Key",
        level="critical"
    )

    # ── Step 3: Wait for user to prepare ──
    logger.info(f"Waiting {PRE_RESTART_WAIT}s for user to prepare 2FA...")
    time.sleep(PRE_RESTART_WAIT)

    # Check one more time — maybe it recovered
    if _check_port():
        logger.info("IB Gateway recovered during pre-restart wait")
        _send_telegram("IB Gateway recovered — restart cancelled", level="info")
        return True

    # ── Step 4: Restart ──
    _send_telegram(
        "IB Gateway: RESTART NOW\n"
        "Approuve le push 2FA sur IB Key maintenant !",
        level="critical"
    )

    ok = _restart_gateway()
    if not ok:
        _send_telegram("IB Gateway restart FAILED — intervention manuelle requise", level="critical")
        return False

    # ── Step 5: Wait for Gateway to come back ──
    came_back = _wait_for_gateway(POST_RESTART_TIMEOUT)

    if came_back:
        # Try to get account info
        time.sleep(5)  # Let it stabilize
        info = _check_account()
        equity = info.get("equity", "?") if info else "?"

        logger.info(f"IB Gateway BACK UP — equity=${equity}")
        _send_telegram(
            f"IB Gateway BACK UP\n"
            f"Port {IBKR_PORT} OK\n"
            f"Equity: ${equity}",
            level="info"
        )
        return True
    else:
        logger.critical("IB Gateway FAILED to restart within timeout")
        _send_telegram(
            f"IB Gateway FAILED to restart\n"
            f"Port {IBKR_PORT} still unreachable after {POST_RESTART_TIMEOUT}s\n"
            f"Intervention manuelle requise:\n"
            f"  ssh root@178.104.125.74\n"
            f"  systemctl restart ibgateway\n"
            f"  # + approuver 2FA sur IB Key",
            level="critical"
        )
        return False


def _check_worker_alive():
    """Check if trading-worker systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "trading-worker"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def _check_paper_gateway():
    """Check if paper gateway port 4003 is reachable."""
    paper_port = int(os.getenv("IBKR_PAPER_PORT", "4003"))
    return _check_port(IBKR_HOST, paper_port, timeout=3)


def run_daemon():
    """Main watchdog loop — monitors live gateway, paper gateway, and worker."""
    logger.info("=" * 50)
    logger.info("  PLATFORM WATCHDOG started")
    logger.info(f"  Live gateway: {IBKR_HOST}:{IBKR_PORT}")
    logger.info(f"  Paper gateway: {IBKR_HOST}:{os.getenv('IBKR_PAPER_PORT', '4003')}")
    logger.info(f"  Worker: trading-worker.service")
    logger.info(f"  Check interval: {CHECK_INTERVAL}s")
    logger.info("=" * 50)

    _send_telegram(
        f"PLATFORM WATCHDOG started\n"
        f"Live: {IBKR_HOST}:{IBKR_PORT}\n"
        f"Paper: {IBKR_HOST}:{os.getenv('IBKR_PAPER_PORT', '4003')}\n"
        f"Worker: trading-worker.service\n"
        f"Check: every {CHECK_INTERVAL}s",
        level="info"
    )

    last_restart = 0
    consecutive_fails = 0
    last_worker_alert = 0
    last_paper_alert = 0

    while True:
        try:
            # ── 1. Live gateway ──
            up = _check_port()
            if up:
                if consecutive_fails > 0:
                    logger.info(f"IB Gateway LIVE OK (recovered after {consecutive_fails} fails)")
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                logger.warning(f"IB Gateway LIVE check FAIL ({consecutive_fails})")
                if consecutive_fails >= 2 and (time.time() - last_restart) > COOLDOWN_AFTER_RESTART:
                    handle_gateway_down()
                    last_restart = time.time()
                    consecutive_fails = 0

            # ── 2. Paper gateway ──
            paper_up = _check_paper_gateway()
            if not paper_up and (time.time() - last_paper_alert) > 600:
                logger.warning("IB Gateway PAPER port 4003 DOWN — restarting service")
                _send_telegram("IB Gateway PAPER DOWN — auto-restart", level="warning")
                try:
                    subprocess.run(["systemctl", "restart", "ibgateway-paper"],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
                last_paper_alert = time.time()

            # ── 3. Worker process ──
            worker_alive = _check_worker_alive()
            if not worker_alive and (time.time() - last_worker_alert) > 300:
                logger.critical("WORKER DOWN — systemd should auto-restart")
                _send_telegram("WORKER DOWN — systemd auto-restart", level="critical")
                last_worker_alert = time.time()

            # ── 4. Dead man's switch (CRO H-2) ──
            _heartbeat_path = ROOT / "data" / "monitoring" / "heartbeat.json"
            if _heartbeat_path.exists():
                try:
                    import json as _json
                    _hb = _json.loads(_heartbeat_path.read_text(encoding="utf-8"))
                    _hb_ts = datetime.fromisoformat(_hb["timestamp"])
                    _hb_age = (datetime.now(timezone.utc) - _hb_ts).total_seconds()
                    if _hb_age > 3600:  # No heartbeat for 1 hour
                        logger.critical(f"DEAD MAN'S SWITCH: no heartbeat for {_hb_age/60:.0f} min")
                        _send_telegram(
                            f"DEAD MAN'S SWITCH: no heartbeat for {_hb_age/60:.0f} min\n"
                            f"Last: {_hb['timestamp']}",
                            level="critical",
                        )
                except Exception as _dms_err:
                    logger.warning(f"Dead man's switch check: {_dms_err}")

        except Exception as e:
            logger.error(f"Watchdog error: {e}", exc_info=True)

        time.sleep(CHECK_INTERVAL)


def run_once():
    """Single check and report."""
    up = _check_port()
    if up:
        info = _check_account()
        if info:
            print(f"IB Gateway UP — equity=${info.get('equity', '?')}, cash=${info.get('cash', '?')}")
        else:
            print(f"IB Gateway port {IBKR_PORT} reachable but account query failed")
    else:
        print(f"IB Gateway DOWN — port {IBKR_PORT} unreachable")
    return up


def force_restart():
    """Force restart with full Telegram flow."""
    logger.info("Force restart requested")
    _send_telegram(
        "IB Gateway: FORCE RESTART\n"
        "Approuve le push 2FA sur IB Key !",
        level="critical"
    )
    ok = _restart_gateway()
    if ok:
        came_back = _wait_for_gateway()
        if came_back:
            time.sleep(5)
            info = _check_account()
            equity = info.get("equity", "?") if info else "?"
            _send_telegram(f"IB Gateway BACK UP — equity=${equity}", level="info")
            print(f"IB Gateway restarted successfully — equity=${equity}")
        else:
            _send_telegram("IB Gateway restart FAILED — 2FA non approuve ?", level="critical")
            print("IB Gateway restart FAILED — port still unreachable")
    else:
        print("Cannot restart ibgateway service")


if __name__ == "__main__":
    (ROOT / "logs").mkdir(exist_ok=True)

    if "--once" in sys.argv:
        ok = run_once()
        sys.exit(0 if ok else 1)
    elif "--restart" in sys.argv:
        force_restart()
    else:
        try:
            run_daemon()
        except KeyboardInterrupt:
            logger.info("Watchdog stopped")
            _send_telegram("IB Gateway Watchdog stopped", level="warning")
