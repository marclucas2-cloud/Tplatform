"""
GO-LIVE Orchestrator — automated pre-live sequence for Day 1.

Runs the full validation pipeline at the right times:
  23:00 CET (Sunday)  → Boot check (IBKR Gateway should be up)
  07:30 CET (Monday)  → Full pre-live validation
  07:45 CET           → Dry-run trade test
  08:00 CET           → Live trade test (minimal sizes)
  08:05 CET           → Monitoring check
  08:10 CET           → Risk validation
  → Telegram notification at each step
  10:00 CET           → FX Carry cycle (worker handles this)

  Then every 30 min    → Monitoring check
  17:30 CET           → Post-trade check
  18:00 CET           → Daily summary + Telegram

Usage:
    python scripts/go_live_orchestrator.py              # Full sequence from now
    python scripts/go_live_orchestrator.py --from=boot  # Start from boot check
    python scripts/go_live_orchestrator.py --from=valid  # Start from validation
    python scripts/go_live_orchestrator.py --now         # Run everything NOW (no waiting)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import zoneinfo

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
        logging.FileHandler(ROOT / "logs" / "go_live_orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
PYTHON = sys.executable
SCRIPTS = ROOT / "scripts"


def _now():
    return datetime.now(PARIS)


def _send_telegram(message: str, level: str = "info"):
    """Send Telegram alert."""
    try:
        from core.telegram_alert import send_alert
        send_alert(message, level=level)
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")


def _wait_until(target_hour: int, target_minute: int = 0, label: str = ""):
    """Wait until a specific time (Paris timezone). Returns immediately if past."""
    now = _now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # If target is in the past today, it might be for tomorrow
    if target <= now:
        # Check if it's more than 1 minute past
        if (now - target).total_seconds() > 60:
            logger.info(f"  {label}: {target_hour:02d}:{target_minute:02d} already passed, skipping wait")
            return True  # Already past, continue

    remaining = (target - now).total_seconds()
    if remaining <= 0:
        return True

    hours = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
    logger.info(f"  ⏳ Waiting for {target_hour:02d}:{target_minute:02d} CET ({label}) — {hours}h{minutes:02d}m remaining")
    _send_telegram(
        f"GO-LIVE ORCHESTRATOR\n"
        f"Next step: {label} at {target_hour:02d}:{target_minute:02d} CET\n"
        f"Wait: {hours}h{minutes:02d}m",
        level="info"
    )

    # Sleep with periodic wake-up (check every 30s for clean shutdown)
    while _now() < target:
        time.sleep(30)

    return True


def _run_script(script_name: str, args: list[str] = None, label: str = ""):
    """Run a script and return (success, output)."""
    args = args or []
    script_path = str(SCRIPTS / script_name)
    cmd = [PYTHON, script_path] + args

    logger.info(f"")
    logger.info(f"{'='*60}")
    logger.info(f"  STEP: {label or script_name}")
    logger.info(f"  Time: {_now().strftime('%H:%M:%S CET')}")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"{'='*60}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max per script
        )

        # Log output
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                logger.info(f"  | {line}")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-10:]:
                logger.warning(f"  | {line}")

        success = result.returncode == 0
        status = "PASS" if success else "FAIL"

        logger.info(f"  → {status} (exit code {result.returncode})")

        # Parse JSON output if available
        output_json = None
        try:
            # Try to find JSON in the last line of stdout
            lines = result.stdout.strip().split("\n")
            for line in reversed(lines):
                if line.strip().startswith("{"):
                    output_json = json.loads(line)
                    break
        except Exception:
            pass

        return success, output_json or {"returncode": result.returncode}

    except subprocess.TimeoutExpired:
        logger.error(f"  → TIMEOUT (5 min)")
        return False, {"error": "timeout"}
    except Exception as e:
        logger.error(f"  → ERROR: {e}")
        return False, {"error": str(e)}


def _abort(reason: str):
    """Abort the go-live sequence."""
    logger.critical(f"")
    logger.critical(f"{'='*60}")
    logger.critical(f"  ⛔ GO-LIVE ABORTED: {reason}")
    logger.critical(f"  Trading INTERDIT")
    logger.critical(f"{'='*60}")
    _send_telegram(
        f"GO-LIVE ABORTED\n"
        f"Reason: {reason}\n"
        f"Trading INTERDIT",
        level="critical"
    )
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# SEQUENCE STEPS
# ═══════════════════════════════════════════════════════════════════════════════

def step_boot_check():
    """23:00 CET — IBKR Gateway boot check."""
    _send_telegram("GO-LIVE: Boot check starting (IBKR Gateway)", level="info")
    ok, result = _run_script("day1_boot_check.py", label="Boot Check (IBKR Gateway)")

    if not ok:
        # IBKR might not be up yet at 23:00, retry in 15 min
        logger.warning("  Boot check failed — retrying in 15 min...")
        _send_telegram("Boot check failed — retry in 15 min", level="warning")
        time.sleep(900)

        ok, result = _run_script("day1_boot_check.py", label="Boot Check RETRY")
        if not ok:
            logger.warning("  Boot check failed again — will retry at 07:30 with full validation")
            _send_telegram("Boot check failed x2 — will retry at 07:30", level="warning")
    else:
        _send_telegram("Boot check PASSED — IBKR Gateway UP", level="info")

    return ok


def step_full_validation():
    """07:30 CET — Full pre-live validation."""
    _send_telegram("GO-LIVE: Full pre-live validation starting", level="info")
    ok, result = _run_script("pre_live_validation.py", ["--json"], label="Full Pre-Live Validation")

    if not ok:
        _abort(f"Pre-live validation FAILED: {result}")

    _send_telegram(
        f"Pre-live validation PASSED\n"
        f"{result.get('pass', '?')} checks OK / {result.get('warn', '?')} warnings",
        level="info"
    )
    return ok


def step_dry_run():
    """07:45 CET — Dry-run trade test."""
    _send_telegram("GO-LIVE: Dry-run trade test", level="info")
    ok, result = _run_script("test_live_trade.py", ["--dry-run"], label="Dry-Run Trade Test")

    if not ok:
        _abort(f"Dry-run trade test FAILED: {result}")

    _send_telegram("Dry-run trade test PASSED", level="info")
    return ok


def step_live_trade_test():
    """08:00 CET — Real trade test (minimal sizes)."""
    _send_telegram(
        "GO-LIVE: Live trade test starting\n"
        "Minimal sizes: 1000 EUR.USD + 0.0001 BTC",
        level="warning"
    )
    ok, result = _run_script("test_live_trade.py", ["--all"], label="Live Trade Test (Real Orders)")

    if not ok:
        _abort(f"Live trade test FAILED: {result}")

    _send_telegram("Live trade test PASSED — execution verified", level="info")
    return ok


def step_monitoring():
    """08:05 CET — Monitoring check."""
    ok, result = _run_script("monitoring_check.py", label="Monitoring Check")
    return ok  # Non-blocking


def step_risk_validation():
    """08:10 CET — Risk engine validation."""
    _send_telegram("GO-LIVE: Risk validation", level="info")

    # Run risk-specific checks from pre_live_validation
    ok, result = _run_script("pre_live_validation.py", ["--json"], label="Risk Re-Validation")

    if not ok:
        _abort("Risk validation FAILED after trade test")

    return ok


def step_monitoring_loop():
    """Every 30 min during the day — monitoring check."""
    logger.info("  Starting periodic monitoring (every 30 min)...")
    _send_telegram(
        "GO-LIVE SEQUENCE COMPLETE ✓\n"
        "All checks passed. Worker active.\n"
        "Monitoring every 30 min.\n"
        "Next: FX Carry at 10:00 CET (worker auto)",
        level="info"
    )

    while True:
        now = _now()

        # Stop monitoring at 22:00
        if now.hour >= 22:
            logger.info("  22:00 CET — stopping monitoring loop")
            break

        # Post-trade check at 17:30
        if now.hour == 17 and 30 <= now.minute < 35:
            _run_script("post_trade_check.py", label="Post-Trade Check (17:30)")

        # Daily summary at 18:00
        if now.hour == 18 and 0 <= now.minute < 5:
            _run_script("daily_summary.py", ["--telegram"], label="Daily Summary (18:00)")

        # Monitoring check
        _run_script("monitoring_check.py", label=f"Monitoring ({now.strftime('%H:%M')})")

        # Sleep 30 min
        time.sleep(1800)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_sequence(start_from: str = "auto", run_now: bool = False):
    """Run the complete go-live sequence."""

    logger.info("=" * 60)
    logger.info("  GO-LIVE ORCHESTRATOR")
    logger.info(f"  Started: {_now().strftime('%Y-%m-%d %H:%M:%S CET')}")
    logger.info(f"  Mode: {'NOW (no waits)' if run_now else f'scheduled (from={start_from})'}")
    logger.info("=" * 60)

    _send_telegram(
        f"GO-LIVE ORCHESTRATOR STARTED\n"
        f"Mode: {'immediate' if run_now else 'scheduled'}\n"
        f"Sequence: boot → validate → dry-run → live test → monitor",
        level="info"
    )

    steps = ["boot", "valid", "dry", "live", "monitor", "risk", "loop"]

    # Determine starting step
    if start_from == "auto":
        now = _now()
        if now.hour >= 18:
            start_from = "boot"  # Evening: start with boot at 23:00
        elif now.hour >= 8:
            start_from = "valid"  # Morning: start with validation
        else:
            start_from = "boot"

    start_idx = 0
    for i, s in enumerate(steps):
        if s.startswith(start_from[:4]):
            start_idx = i
            break

    # ── BOOT CHECK (23:00 CET) ──
    if start_idx <= 0:
        if not run_now:
            _wait_until(23, 0, "Boot check (IBKR Gateway)")
        step_boot_check()

    # ── FULL VALIDATION (07:30 CET) ──
    if start_idx <= 1:
        if not run_now:
            # If it's past midnight, wait for 07:30
            now = _now()
            if now.hour < 7 or (now.hour == 7 and now.minute < 30):
                _wait_until(7, 30, "Full validation")
        step_full_validation()

    # ── DRY-RUN (07:45 CET) ──
    if start_idx <= 2:
        if not run_now:
            _wait_until(7, 45, "Dry-run trade test")
        step_dry_run()

    # ── LIVE TRADE TEST (08:00 CET) ──
    if start_idx <= 3:
        if not run_now:
            _wait_until(8, 0, "Live trade test")
        step_live_trade_test()

    # ── MONITORING (08:05 CET) ──
    if start_idx <= 4:
        if not run_now:
            _wait_until(8, 5, "Monitoring + Risk")
        step_monitoring()

    # ── RISK VALIDATION (08:10 CET) ──
    if start_idx <= 5:
        step_risk_validation()

    # ── MONITORING LOOP (every 30 min) ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("  ✓ ALL PRE-LIVE CHECKS PASSED")
    logger.info("  Trading AUTHORIZED")
    logger.info("  Entering monitoring loop (every 30 min)")
    logger.info("=" * 60)

    if start_idx <= 6:
        try:
            step_monitoring_loop()
        except KeyboardInterrupt:
            logger.info("  Monitoring stopped (Ctrl+C)")

    # End of day
    logger.info("")
    _run_script("post_trade_check.py", label="Final Post-Trade Check")
    _run_script("daily_summary.py", ["--telegram"], label="Final Daily Summary")

    logger.info("=" * 60)
    logger.info("  GO-LIVE DAY 1 COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    args = sys.argv[1:]

    # Ensure log directory exists
    (ROOT / "logs").mkdir(exist_ok=True)

    run_now = "--now" in args
    start_from = "auto"

    for a in args:
        if a.startswith("--from="):
            start_from = a.split("=")[1]

    try:
        run_full_sequence(start_from=start_from, run_now=run_now)
    except KeyboardInterrupt:
        logger.info("\nOrchestrator stopped by user")
        _send_telegram("GO-LIVE ORCHESTRATOR stopped by user", level="warning")
    except SystemExit:
        raise
    except Exception as e:
        logger.critical(f"ORCHESTRATOR CRASH: {e}", exc_info=True)
        _send_telegram(f"ORCHESTRATOR CRASH: {e}", level="critical")
        sys.exit(1)
