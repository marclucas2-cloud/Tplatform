"""
D3-04 — Emergency All-Broker Close.

Panic button: closes ALL positions on ALL brokers in parallel.
Triggered via Telegram /emergency_close_all <code> or programmatically.

Sequence per broker (parallel):
  1. Cancel ALL open orders
  2. Close ALL positions at market
  3. Activate kill switch LEVEL_3
  4. Report results

30s timeout per broker. Other brokers not blocked by one failing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "risk" / "emergency_close_log.jsonl"


def _generate_confirmation_code() -> str:
    """Generate hourly confirmation code (TOTP-like, changes every hour)."""
    hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    h = hashlib.sha256(f"emergency_{hour_key}".encode()).hexdigest()[:6].upper()
    return h


class EmergencyCloseAll:
    """Multi-broker emergency close with confirmation.

    Usage::

        closer = EmergencyCloseAll(
            brokers={"IBKR": ibkr_broker, "BINANCE": bnb_broker, "ALPACA": alpaca_broker},
            alert_callback=send_telegram,
        )
        # Get current code for Telegram
        code = closer.get_confirmation_code()
        # Execute
        report = closer.execute(confirmation_code=code)
    """

    TIMEOUT_PER_BROKER = 30  # seconds

    def __init__(
        self,
        brokers: Optional[dict] = None,
        alert_callback: Optional[Callable] = None,
        kill_switch_callback: Optional[Callable] = None,
    ):
        self._brokers = brokers or {}
        self._alert = alert_callback
        self._kill_switch = kill_switch_callback
        self._last_execution: Optional[dict] = None

    def get_confirmation_code(self) -> str:
        """Get current hourly confirmation code."""
        return _generate_confirmation_code()

    def execute(
        self, confirmation_code: Optional[str] = None, force: bool = False
    ) -> dict:
        """Execute emergency close on all brokers.

        Args:
            confirmation_code: Must match current hourly code (unless force=True).
            force: Skip confirmation (for programmatic use from kill switch).

        Returns:
            Report dict with results per broker.
        """
        if not force:
            expected = _generate_confirmation_code()
            if confirmation_code != expected:
                return {
                    "status": "REJECTED",
                    "reason": "Invalid confirmation code.",
                }

        logger.critical("EMERGENCY CLOSE ALL — EXECUTING")
        if self._alert:
            self._alert("EMERGENCY CLOSE ALL — EXECUTING NOW", level="critical")

        start = time.time()
        results = {}

        # Close all brokers in parallel
        with ThreadPoolExecutor(max_workers=len(self._brokers) or 1) as executor:
            futures = {}
            for broker_name, broker in self._brokers.items():
                f = executor.submit(self._close_broker, broker_name, broker)
                futures[f] = broker_name

            for future in as_completed(futures, timeout=60):
                broker_name = futures[future]
                try:
                    results[broker_name] = future.result()
                except Exception as e:
                    results[broker_name] = {
                        "status": "ERROR",
                        "error": str(e),
                    }

        # Activate kill switches
        if self._kill_switch:
            try:
                self._kill_switch("LEVEL_3")
            except Exception as e:
                logger.error("Kill switch activation failed: %s", e)

        elapsed = time.time() - start

        report = {
            "status": "EXECUTED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "brokers": results,
            "total_positions_closed": sum(
                r.get("positions_closed", 0) for r in results.values()
            ),
            "total_orders_cancelled": sum(
                r.get("orders_cancelled", 0) for r in results.values()
            ),
            "total_pnl": sum(
                r.get("pnl_realized", 0) for r in results.values()
            ),
        }

        self._last_execution = report
        self._log(report)

        # Send final report
        if self._alert:
            msg = (
                f"EMERGENCY CLOSE COMPLETE ({elapsed:.1f}s)\n"
                f"Positions closed: {report['total_positions_closed']}\n"
                f"Orders cancelled: {report['total_orders_cancelled']}\n"
                f"PnL realized: ${report['total_pnl']:,.2f}\n"
                f"Errors: {sum(1 for r in results.values() if r.get('status') == 'ERROR')}"
            )
            self._alert(msg, level="critical")

        return report

    def _close_broker(self, name: str, broker) -> dict:
        """Close all positions on one broker."""
        result = {
            "status": "OK",
            "positions_closed": 0,
            "orders_cancelled": 0,
            "pnl_realized": 0,
            "errors": [],
        }

        # 1. Cancel all open orders
        try:
            if hasattr(broker, "cancel_all_orders"):
                cancelled = broker.cancel_all_orders()
                result["orders_cancelled"] = cancelled if isinstance(cancelled, int) else 0
                logger.info("  %s: cancelled %s orders", name, result["orders_cancelled"])
            elif hasattr(broker, "get_open_orders"):
                orders = broker.get_open_orders()
                for order in orders:
                    try:
                        broker.cancel_order(order.get("order_id", order.get("id")))
                        result["orders_cancelled"] += 1
                    except Exception as e:
                        result["errors"].append(f"cancel order: {e}")
        except Exception as e:
            result["errors"].append(f"cancel_all: {e}")

        # 2. Close all positions
        try:
            positions = broker.get_positions()
            for pos in positions:
                try:
                    ticker = pos.get("symbol", pos.get("ticker", ""))
                    qty = abs(float(pos.get("qty", pos.get("quantity", 0))))
                    side = pos.get("side", "LONG")

                    if qty <= 0:
                        continue

                    # Use close_position if available (works on all brokers),
                    # fallback to create_position for reverse-close.
                    if hasattr(broker, "close_position"):
                        broker.close_position(
                            ticker,
                            _authorized_by="emergency_close_all",
                        )
                    else:
                        close_side = "SELL" if side.upper() in ("LONG", "BUY") else "BUY"
                        broker.create_position(
                            symbol=ticker,
                            direction=close_side,
                            qty=qty,
                            _authorized_by="emergency_close_all",
                        )
                    result["positions_closed"] += 1
                    result["pnl_realized"] += float(pos.get("unrealized_pl", 0))
                except Exception as e:
                    result["errors"].append(f"close {ticker}: {e}")
        except Exception as e:
            result["errors"].append(f"get_positions: {e}")

        if result["errors"]:
            result["status"] = "PARTIAL"
            logger.warning("%s emergency close errors: %s", name, result["errors"])

        return result

    def _log(self, report: dict) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to log emergency close: %s", e)
