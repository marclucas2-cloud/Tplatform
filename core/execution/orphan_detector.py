"""Orphan Order Detector — detects and cleans up orphan orders.

An orphan order is an order that no longer has a matching open position.
Common causes:
  - Position closed manually via TWS or web portal (SL/TP remain active)
  - Worker crash mid-execution leaving hanging orders
  - Bracket OCA group partially filled (SL hit but TP still active)

Works for both IBKR (bracket OCA groups) and Binance (open orders).

Safety:
  - Never cancels an order that has a matching open position
  - Logs every cancellation to data/orphan_cleanup_log.jsonl
  - Alerts via callback for orphans found during market hours
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DEFAULT_BRACKETS_FILE = _DATA_DIR / "active_brackets.json"
_CLEANUP_LOG_PATH = _DATA_DIR / "orphan_cleanup_log.jsonl"

# Orders older than this (seconds) are more likely true orphans
_STALE_THRESHOLD_SECONDS = 300  # 5 minutes


class OrphanDetector:
    """Detects and cleans up orphan orders across brokers.

    An order is considered orphan if:
      a) Its ticker has no matching open position
      b) Its direction conflicts with position (SL for a closed position)
      c) Its OCA group references a non-existent parent order
    """

    def __init__(self, alert_callback: Optional[Callable[[str], None]] = None):
        """
        Args:
            alert_callback: Optional function called with a message string
                when orphans are detected. Typically sends a Telegram alert.
        """
        self._alert_callback = alert_callback
        _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core scan: open orders vs positions
    # ------------------------------------------------------------------

    def scan_orphans(
        self,
        open_orders: List[Dict[str, Any]],
        positions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compare open orders against current positions to find orphans.

        Args:
            open_orders: List of dicts, each with at minimum:
                {order_id, ticker, side, order_type, qty, price}
                Optional: {oca_group, parent_order_id, timestamp}
            positions: List of dicts, each with at minimum:
                {ticker, qty, side}
                Optional: {strategy}

        Returns:
            List of orphan order dicts in the standard orphan format.
        """
        now = time.time()

        # Build position index: ticker -> list of position dicts
        position_map: Dict[str, List[Dict[str, Any]]] = {}
        for pos in positions:
            ticker = pos.get("ticker", "")
            if ticker:
                position_map.setdefault(ticker, []).append(pos)

        # Build set of all known order IDs (for OCA parent checks)
        known_order_ids = {
            str(o.get("order_id", "")) for o in open_orders
        }

        orphans: List[Dict[str, Any]] = []

        for order in open_orders:
            order_id = str(order.get("order_id", ""))
            ticker = order.get("ticker", "")
            side = order.get("side", "").upper()
            order_type = order.get("order_type", "")
            qty = float(order.get("qty", 0))
            price = float(order.get("price", 0))
            oca_group = order.get("oca_group")
            parent_order_id = order.get("parent_order_id")
            timestamp = order.get("timestamp")

            # Calculate order age
            age_seconds = 0.0
            if timestamp:
                try:
                    if isinstance(timestamp, (int, float)):
                        age_seconds = now - timestamp
                    elif isinstance(timestamp, str):
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        age_seconds = now - dt.timestamp()
                    elif isinstance(timestamp, datetime):
                        age_seconds = now - timestamp.timestamp()
                except (ValueError, TypeError):
                    age_seconds = 0.0

            ticker_positions = position_map.get(ticker, [])

            # Case (a): No matching open position at all
            if not ticker_positions:
                orphans.append(self._make_orphan(
                    order_id=order_id,
                    ticker=ticker,
                    side=side,
                    order_type=order_type,
                    qty=qty,
                    price=price,
                    reason="NO_MATCHING_POSITION",
                    recommended_action="CANCEL",
                    age_seconds=age_seconds,
                ))
                continue

            # Case (b): Direction conflict
            # A SL for a BUY position should be a SELL. If position is already
            # closed or flipped, the SL side conflicts.
            if self._is_direction_conflict(side, ticker_positions):
                orphans.append(self._make_orphan(
                    order_id=order_id,
                    ticker=ticker,
                    side=side,
                    order_type=order_type,
                    qty=qty,
                    price=price,
                    reason="CONFLICT",
                    recommended_action="CANCEL",
                    age_seconds=age_seconds,
                ))
                continue

            # Case (c): OCA group references a non-existent parent order
            if parent_order_id and str(parent_order_id) not in known_order_ids:
                # Parent order is gone (filled or cancelled).
                # If position still exists, the child order might still be
                # protecting it -- flag for REVIEW, not auto-cancel.
                if ticker_positions:
                    # Position exists but parent is gone -- size mismatch check
                    total_pos_qty = sum(
                        abs(float(p.get("qty", 0))) for p in ticker_positions
                    )
                    if abs(qty - total_pos_qty) > 0.01:
                        orphans.append(self._make_orphan(
                            order_id=order_id,
                            ticker=ticker,
                            side=side,
                            order_type=order_type,
                            qty=qty,
                            price=price,
                            reason="STALE_BRACKET",
                            recommended_action="REVIEW",
                            age_seconds=age_seconds,
                        ))
                    # else: size matches, order is likely still valid protection
                    # -> not an orphan

        return orphans

    # ------------------------------------------------------------------
    # Bracket state scan
    # ------------------------------------------------------------------

    def scan_bracket_state(
        self,
        brackets_file: Path,
        positions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compare persisted bracket state against live positions.

        Detect brackets whose parent position no longer exists.

        Args:
            brackets_file: Path to active_brackets.json
            positions: Current live positions [{ticker, qty, side}, ...]

        Returns:
            List of orphan bracket dicts.
        """
        try:
            if not brackets_file.exists():
                logger.debug("No brackets file found at %s", brackets_file)
                return []

            with open(brackets_file, "r", encoding="utf-8") as f:
                brackets = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read brackets file %s: %s", brackets_file, e)
            return []

        # Build position set (tickers with nonzero qty)
        position_tickers: Dict[str, float] = {}
        for pos in positions:
            ticker = pos.get("ticker", "")
            qty = float(pos.get("qty", 0))
            if ticker and qty != 0:
                position_tickers[ticker] = position_tickers.get(ticker, 0) + abs(qty)

        orphans: List[Dict[str, Any]] = []
        now = time.time()

        for oca_group, info in brackets.items():
            status = info.get("status", "")
            if status in ("CANCELLED", "FILLED"):
                continue

            symbol = info.get("symbol", "")
            if symbol not in position_tickers:
                # Bracket exists but no position -- stale bracket
                sl_id = str(info.get("sl_order_id", ""))
                tp_id = str(info.get("tp_order_id", ""))

                for order_id, label, order_type in [
                    (sl_id, "SL", "STP"),
                    (tp_id, "TP", "LMT"),
                ]:
                    direction = info.get("direction", "BUY")
                    # SL/TP side is opposite of entry direction
                    child_side = "SELL" if direction == "BUY" else "BUY"
                    price = (
                        info.get("stop_loss_price", 0)
                        if label == "SL"
                        else info.get("take_profit_price", 0)
                    )

                    orphans.append(self._make_orphan(
                        order_id=order_id,
                        ticker=symbol,
                        side=child_side,
                        order_type=order_type,
                        qty=float(info.get("quantity", 0)),
                        price=float(price),
                        reason="STALE_BRACKET",
                        recommended_action="CANCEL",
                        age_seconds=0.0,
                    ))

        return orphans

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_orphans(
        self,
        orphans: List[Dict[str, Any]],
        broker: Any,
    ) -> Dict[str, Any]:
        """Cancel all orphan orders via broker API.

        Only cancels orders with recommended_action == "CANCEL".
        Orders flagged as "REVIEW" or "KEEP" are skipped.

        Args:
            orphans: List of orphan dicts from scan_orphans / scan_bracket_state
            broker: Broker instance with cancel_order(order_id) method

        Returns:
            {cancelled: int, failed: int, skipped: int, errors: list}
        """
        cancelled = 0
        failed = 0
        skipped = 0
        errors: List[str] = []

        for orphan in orphans:
            if orphan.get("recommended_action") != "CANCEL":
                skipped += 1
                logger.info(
                    "Orphan %s (%s) skipped — action=%s",
                    orphan["order_id"],
                    orphan["ticker"],
                    orphan["recommended_action"],
                )
                continue

            order_id = orphan["order_id"]
            try:
                broker.cancel_order(order_id)
                cancelled += 1
                logger.info(
                    "Cancelled orphan order %s (%s) — reason=%s",
                    order_id,
                    orphan["ticker"],
                    orphan["reason"],
                )
                self._log_cleanup(orphan, success=True)
            except Exception as e:
                failed += 1
                error_msg = f"Failed to cancel {order_id} ({orphan['ticker']}): {e}"
                errors.append(error_msg)
                logger.error(error_msg)
                self._log_cleanup(orphan, success=False, error=str(e))

        result = {
            "cancelled": cancelled,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
        }

        # Alert if any orphans were processed
        if cancelled > 0 or failed > 0:
            self._alert(
                f"Orphan cleanup: {cancelled} cancelled, {failed} failed, "
                f"{skipped} skipped"
            )

        return result

    # ------------------------------------------------------------------
    # End-of-day cleanup
    # ------------------------------------------------------------------

    def run_eod_cleanup(
        self,
        broker: Any,
        market_close_time: datetime,
    ) -> Dict[str, Any]:
        """End-of-day cleanup: cancel orphan orders after market close.

        Should be called ~5 minutes after market close.
        Scans for all orphan orders and cancels them.

        Args:
            broker: Broker instance with get_open_orders(), get_positions(),
                    and cancel_order() methods
            market_close_time: The market close datetime (used for logging)

        Returns:
            {orphans_found: int, cancelled: int, failed: int,
             positions_open: int, errors: list}
        """
        logger.info(
            "EOD cleanup started — market close: %s",
            market_close_time.isoformat(),
        )

        try:
            open_orders = broker.get_open_orders()
        except Exception as e:
            logger.error("EOD cleanup: failed to get open orders: %s", e)
            return {
                "orphans_found": 0,
                "cancelled": 0,
                "failed": 0,
                "positions_open": 0,
                "errors": [f"get_open_orders failed: {e}"],
            }

        try:
            positions = broker.get_positions()
        except Exception as e:
            logger.error("EOD cleanup: failed to get positions: %s", e)
            return {
                "orphans_found": 0,
                "cancelled": 0,
                "failed": 0,
                "positions_open": 0,
                "errors": [f"get_positions failed: {e}"],
            }

        # Scan for orphans
        orphans = self.scan_orphans(open_orders, positions)

        # Also scan bracket state file
        bracket_orphans = self.scan_bracket_state(
            _DEFAULT_BRACKETS_FILE, positions,
        )

        # Merge, dedup by order_id
        seen_ids = {o["order_id"] for o in orphans}
        for bo in bracket_orphans:
            if bo["order_id"] not in seen_ids:
                orphans.append(bo)
                seen_ids.add(bo["order_id"])

        orphans_found = len(orphans)

        # Cleanup
        cleanup_result = self.cleanup_orphans(orphans, broker)

        result = {
            "orphans_found": orphans_found,
            "cancelled": cleanup_result["cancelled"],
            "failed": cleanup_result["failed"],
            "positions_open": len(positions),
            "errors": cleanup_result["errors"],
        }

        logger.info(
            "EOD cleanup complete: %d orphans found, %d cancelled, "
            "%d positions still open",
            result["orphans_found"],
            result["cancelled"],
            result["positions_open"],
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_orphan(
        order_id: str,
        ticker: str,
        side: str,
        order_type: str,
        qty: float,
        price: float,
        reason: str,
        recommended_action: str,
        age_seconds: float,
    ) -> Dict[str, Any]:
        """Create a standardized orphan order dict."""
        return {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "order_type": order_type,
            "qty": qty,
            "price": price,
            "reason": reason,
            "recommended_action": recommended_action,
            "age_seconds": age_seconds,
        }

    @staticmethod
    def _is_direction_conflict(
        order_side: str,
        positions: List[Dict[str, Any]],
    ) -> bool:
        """Check if an order's side conflicts with all open positions.

        A SELL order conflicts if all positions on that ticker are short.
        A BUY order conflicts if all positions on that ticker are long.
        This would mean the order is trying to close a position that is
        already in the opposite direction (e.g., leftover SL from a long
        that flipped to short).
        """
        if not positions:
            return False

        for pos in positions:
            pos_side = pos.get("side", "").upper()
            pos_qty = float(pos.get("qty", 0))

            # Infer side from qty if side field not explicit
            if not pos_side and pos_qty != 0:
                pos_side = "LONG" if pos_qty > 0 else "SHORT"

            # A SELL order is normal for a LONG position (closing/SL/TP)
            if order_side == "SELL" and pos_side in ("LONG", "BUY"):
                return False
            # A BUY order is normal for a SHORT position (closing/SL/TP)
            if order_side == "BUY" and pos_side in ("SHORT", "SELL"):
                return False

        # All positions conflict with the order side
        return True

    def _log_cleanup(
        self,
        orphan: Dict[str, Any],
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Append a cleanup event to the JSONL log file."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "order_id": orphan.get("order_id"),
            "ticker": orphan.get("ticker"),
            "side": orphan.get("side"),
            "order_type": orphan.get("order_type"),
            "qty": orphan.get("qty"),
            "reason": orphan.get("reason"),
            "action": orphan.get("recommended_action"),
            "success": success,
        }
        if error:
            entry["error"] = error

        try:
            _CLEANUP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_CLEANUP_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.warning("Failed to write orphan cleanup log: %s", e)

    def _alert(self, message: str) -> None:
        """Send alert via callback if configured."""
        if self._alert_callback:
            try:
                self._alert_callback(message)
            except Exception as e:
                logger.warning("Alert callback failed: %s", e)
