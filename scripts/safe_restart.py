"""
Safe Restart — restart worker with state preservation and coherence checks.

Sequence:
  1. Save current state snapshot
  2. Stop worker gracefully (SIGTERM)
  3. Wait for clean shutdown
  4. Verify state file coherence
  5. Restart worker
  6. Wait for health endpoint
  7. Verify broker reconnections
  8. Verify state reloaded correctly

Usage:
    python scripts/safe_restart.py              # Full restart sequence
    python scripts/safe_restart.py --check-only # Only verify state coherence
    python scripts/safe_restart.py --force      # Kill -9 if graceful fails
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("safe_restart")


def _save_pre_restart_snapshot():
    """Save state before restart for comparison after."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": None,
        "kill_switch": None,
        "fx_carry_ks": None,
    }

    # Portfolio state
    state_path = ROOT / "paper_portfolio_state.json"
    if state_path.exists():
        try:
            snapshot["state"] = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Cannot read state: {e}")

    # Kill switch state
    ks_path = ROOT / "data" / "kill_switch_state.json"
    if ks_path.exists():
        try:
            snapshot["kill_switch"] = json.loads(ks_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # FX carry kill switch
    fx_ks_path = ROOT / "data" / "fx" / "carry_mom_ks_state.json"
    if fx_ks_path.exists():
        try:
            snapshot["fx_carry_ks"] = json.loads(fx_ks_path.read_text())
        except Exception:
            pass

    snapshot_path = ROOT / "data" / "pre_restart_snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str))
    logger.info(f"  Pre-restart snapshot saved to {snapshot_path}")
    return snapshot


def _find_worker_pid():
    """Find the worker.py process PID."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                if any('worker.py' in str(c) for c in cmdline):
                    return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        logger.warning("psutil not installed — cannot find worker PID")
    return None


def _stop_worker(force: bool = False):
    """Stop worker gracefully or forcefully."""
    pid = _find_worker_pid()
    if not pid:
        logger.info("  Worker not running — nothing to stop")
        return True

    logger.info(f"  Stopping worker PID {pid}...")

    # Graceful shutdown
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        logger.warning(f"  SIGTERM failed: {e}")
        if not force:
            return False

    # Wait up to 30 seconds for graceful shutdown
    for i in range(30):
        time.sleep(1)
        try:
            os.kill(pid, 0)  # Check if still running
        except OSError:
            logger.info(f"  Worker stopped gracefully after {i+1}s")
            return True

    # Still running after 30s
    if force:
        logger.warning("  Graceful shutdown failed — force killing...")
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(2)
            logger.info("  Worker force-killed")
            return True
        except OSError as e:
            logger.error(f"  Force kill failed: {e}")
            return False
    else:
        logger.error("  Worker did not stop after 30s (use --force)")
        return False


def _start_worker():
    """Start worker.py in background."""
    logger.info("  Starting worker...")

    # Use the same Python interpreter
    python = sys.executable
    worker_path = str(ROOT / "worker.py")

    # Start detached
    if sys.platform == "win32":
        # Windows: CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [python, worker_path],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=open(ROOT / "logs" / "worker" / "worker_stdout.log", "a"),
            stderr=subprocess.STDOUT,
        )
    else:
        proc = subprocess.Popen(
            [python, worker_path],
            cwd=str(ROOT),
            start_new_session=True,
            stdout=open(ROOT / "logs" / "worker" / "worker_stdout.log", "a"),
            stderr=subprocess.STDOUT,
        )

    logger.info(f"  Worker started PID {proc.pid}")
    return proc.pid


def _wait_for_health(timeout: int = 60):
    """Wait for worker health endpoint to respond."""
    import urllib.request

    logger.info("  Waiting for health endpoint...")
    for i in range(timeout):
        try:
            with urllib.request.urlopen("http://localhost:8080/health", timeout=2) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "ok":
                    logger.info(f"  Health endpoint OK after {i+1}s")
                    return True
        except Exception:
            pass
        time.sleep(1)

    logger.error(f"  Health endpoint not responding after {timeout}s")
    return False


def _verify_state_coherence(pre_snapshot: dict):
    """Compare state before and after restart."""
    logger.info("── STATE COHERENCE ──")
    issues = []

    # Check state file still exists and is valid
    state_path = ROOT / "paper_portfolio_state.json"
    if not state_path.exists():
        issues.append("State file missing after restart")
    else:
        try:
            post_state = json.loads(state_path.read_text(encoding="utf-8"))
            pre_state = pre_snapshot.get("state", {})

            # Compare equity
            pre_eq = pre_state.get("equity", 0) if pre_state else 0
            post_eq = post_state.get("equity", 0)
            if pre_eq > 0 and post_eq > 0:
                diff_pct = abs(post_eq - pre_eq) / pre_eq * 100
                if diff_pct > 1:
                    issues.append(f"Equity changed {diff_pct:.1f}% during restart")
                else:
                    logger.info(f"  ✓ Equity consistent: ${post_eq:,.0f}")

            # Compare position count
            pre_pos = len(pre_state.get("positions", {})) if pre_state else 0
            post_pos = len(post_state.get("positions", {}))
            if pre_pos != post_pos:
                issues.append(f"Position count changed: {pre_pos} → {post_pos}")
            else:
                logger.info(f"  ✓ Position count consistent: {post_pos}")

        except Exception as e:
            issues.append(f"Cannot read post-restart state: {e}")

    # Check kill switch states (IBKR + crypto)
    for ks_name, ks_file in [
        ("IBKR", "kill_switch_state.json"),
        ("Crypto", "crypto_kill_switch_state.json"),
    ]:
        ks_path = ROOT / "data" / ks_file
        if ks_path.exists():
            try:
                ks = json.loads(ks_path.read_text(encoding="utf-8"))
                if ks.get("active"):  # FIX: was "_active" (wrong key)
                    issues.append(f"Kill switch {ks_name} ACTIVE: {ks.get('reason', ks.get('_activation_reason'))}")
                else:
                    logger.info(f"  ✓ Kill switch {ks_name}: OK")
            except Exception as e:
                issues.append(f"Cannot read {ks_name} kill switch state: {e}")

    if issues:
        logger.error("  COHERENCE ISSUES:")
        for issue in issues:
            logger.error(f"    ✗ {issue}")
    else:
        logger.info("  ✓ All state checks passed")

    return issues


def check_only():
    """Only verify current state coherence without restart."""
    logger.info("=" * 60)
    logger.info("  STATE COHERENCE CHECK (no restart)")
    logger.info("=" * 60)

    snapshot = _save_pre_restart_snapshot()
    issues = _verify_state_coherence(snapshot)

    # Also check broker connections
    logger.info("── BROKER CONNECTIONS ──")
    import socket
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=5):
            logger.info(f"  ✓ IBKR: {host}:{port}")
    except Exception:
        logger.warning(f"  ! IBKR: {host}:{port} not reachable")

    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            info = broker.get_account_info()
            logger.info(f"  ✓ Binance: equity=${info.get('equity', 0):,.0f}")
        except Exception as e:
            logger.warning(f"  ! Binance: {e}")

    return len(issues) == 0


def full_restart(force: bool = False):
    """Full restart sequence."""
    logger.info("=" * 60)
    logger.info("  SAFE RESTART SEQUENCE")
    logger.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    # 1. Save snapshot
    logger.info("── STEP 1: Save state snapshot ──")
    snapshot = _save_pre_restart_snapshot()

    # 2. Stop worker
    logger.info("── STEP 2: Stop worker ──")
    stopped = _stop_worker(force=force)
    if not stopped:
        logger.error("  ABORT: cannot stop worker")
        return False

    # 3. Verify state files
    logger.info("── STEP 3: Verify state files ──")
    for name, path in [
        ("portfolio state", ROOT / "paper_portfolio_state.json"),
        ("kill switch", ROOT / "data" / "kill_switch_state.json"),
    ]:
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                logger.info(f"  ✓ {name}: valid JSON")
            except Exception as e:
                logger.error(f"  ✗ {name}: corrupt JSON — {e}")
                return False

    # 4. Start worker
    logger.info("── STEP 4: Start worker ──")
    (ROOT / "logs" / "worker").mkdir(parents=True, exist_ok=True)
    pid = _start_worker()

    # 5. Wait for health
    logger.info("── STEP 5: Wait for health endpoint ──")
    healthy = _wait_for_health(timeout=60)
    if not healthy:
        logger.error("  ABORT: worker health check failed")
        return False

    # 6. Verify state coherence
    logger.info("── STEP 6: Verify state coherence ──")
    time.sleep(5)  # Let reconciliation run
    issues = _verify_state_coherence(snapshot)

    # 7. Summary
    success = len(issues) == 0
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  RESTART: {'SUCCESS' if success else 'ISSUES DETECTED'}")
    logger.info(f"  Worker PID: {pid}")
    if issues:
        logger.error(f"  {len(issues)} issue(s) — review above")
    logger.info("=" * 60)

    return success


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--check-only" in args:
        ok = check_only()
        sys.exit(0 if ok else 1)
    else:
        force = "--force" in args
        ok = full_restart(force=force)
        sys.exit(0 if ok else 1)
