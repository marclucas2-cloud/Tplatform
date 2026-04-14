#!/usr/bin/env python3
"""Weekly walk-forward re-validation for all crypto strategies.

Runs wf_crypto_all.py, parses results, flags drift, and sends Telegram
alerts. Strategies that fail the WF criteria get auto-paused in the
worker via `data/crypto/wf_pauses.json` (read by the crypto cycle at
startup and per-cycle).

WF criteria (per strat):
  - OOS/IS ratio >= 0.5
  - >= 50% profitable OOS windows
  - >= 5 total trades

Scheduled via worker.py scheduler (Sundays 04:00 Paris).
"""
from __future__ import annotations

import json
import logging
import subprocess
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

logger = logging.getLogger(__name__)
_PAUSES_FILE = ROOT / "data" / "crypto" / "wf_pauses.json"
_PAUSES_FILE.parent.mkdir(parents=True, exist_ok=True)


def _telegram_alert(msg: str, level: str = "warning") -> None:
    try:
        import os
        import requests
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "ℹ️")
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": f"{emoji} {msg}"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"telegram alert failed: {e}")


def run_wf() -> dict:
    """Run wf_crypto_all.py and return parsed verdicts."""
    out_dir = ROOT / "output" / "wf"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running wf_crypto_all.py (this may take several minutes)...")
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wf_crypto_all.py"),
             "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=1800,  # 30 min max
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        logger.error("WF run timed out (30min)")
        return {"error": "timeout"}

    if result.returncode != 0:
        logger.error(f"WF run failed: {result.stderr[:500]}")
        return {"error": f"exit {result.returncode}: {result.stderr[:200]}"}

    # Parse the latest JSON output
    json_files = sorted(out_dir.glob("wf_crypto_*.json"), key=lambda p: p.stat().st_mtime)
    if not json_files:
        return {"error": "no output json"}

    latest = json_files[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"parse {latest.name}: {e}"}
    return {"result": data, "file": str(latest)}


def evaluate_and_pause(wf_data: dict) -> tuple[list[str], list[str], list[str]]:
    """Evaluate each strat against WF criteria. Return (pass, borderline, fail)."""
    passed, borderline, failing = [], [], []
    strategies = wf_data.get("strategies", []) if isinstance(wf_data, dict) else []

    for s in strategies:
        name = s.get("strategy") or s.get("name") or "?"
        verdict = s.get("verdict", "UNKNOWN")
        if verdict in ("VALIDATED", "PASS", "PROFITABLE"):
            passed.append(name)
        elif verdict in ("BORDERLINE", "MARGINAL"):
            borderline.append(name)
        elif verdict in ("REJECTED", "FAIL", "LOSING"):
            failing.append(name)

    # Write pauses file for failing strats (until next review)
    now = datetime.now(UTC)
    until = (now + timedelta(days=7)).isoformat()
    existing = {}
    if _PAUSES_FILE.exists():
        try:
            existing = json.loads(_PAUSES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    for name in failing:
        existing[name] = {"paused_until": until, "reason": "WF_WEEKLY_REJECTED"}
    # Clear old pauses that are no longer failing
    existing = {k: v for k, v in existing.items() if k in failing}
    _PAUSES_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    return passed, borderline, failing


def main():
    logger.info("=== WF WEEKLY REVIEW START ===")
    result = run_wf()

    if "error" in result:
        msg = f"WF WEEKLY FAILED: {result['error']}"
        logger.error(msg)
        _telegram_alert(msg, level="critical")
        return 1

    wf_data = result["result"]
    passed, borderline, failing = evaluate_and_pause(wf_data)

    summary = (
        f"WF WEEKLY REVIEW ({datetime.now(UTC).strftime('%Y-%m-%d')})\n"
        f"PASSED: {len(passed)} ({', '.join(passed) or '-'})\n"
        f"BORDERLINE: {len(borderline)} ({', '.join(borderline) or '-'})\n"
        f"REJECTED: {len(failing)} ({', '.join(failing) or '-'})"
    )
    logger.info(summary)

    if failing:
        msg = f"{summary}\n\n{len(failing)} strats auto-paused for 7d until next review."
        _telegram_alert(msg, level="critical")
    else:
        _telegram_alert(summary, level="info")

    logger.info("=== WF WEEKLY REVIEW DONE ===")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
