"""
Partial Fill Handler -- adjusts SL/TP orders to match actually filled quantity.

When a bracket order is partially filled (e.g., 40/100 shares), the stop-loss
MUST be adjusted immediately to cover the filled portion. A position without
proper SL coverage is an unacceptable risk.

Flow:
  1. on_fill() receives fill events from broker callbacks
  2. If partial: compute new SL qty, return ADJUST_SL action
  3. check_pending_fills() monitors orders stuck in partial state
  4. get_exposure_gap() audits SL coverage vs actual position size

Logging: all events appended to data/partial_fills_log.jsonl

Supports all instrument types:
  - Equities (integer shares)
  - FX (lot-based, min 25000 on IBKR)
  - Futures (integer contracts)
  - Crypto (fractional, arbitrary precision)
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_LOG_FILE = _LOG_DIR / "partial_fills_log.jsonl"

# Alert threshold: if uncovered for more than this many seconds, trigger alert
UNCOVERED_ALERT_SECONDS = 60

# Instrument types that require integer quantities
INTEGER_QTY_INSTRUMENTS = {"EQUITY", "FUTURES"}

# FX minimum lot size on IBKR
FX_MIN_LOT = 25_000


class PartialFillHandler:
    """Handles partial fills by adjusting SL/TP orders to match filled quantity.

    Thread-safe: internal state protected by a lock for use in async broker
    callbacks.
    """

    def __init__(
        self,
        alert_callback: Callable[[str, dict], None] | None = None,
        timeout_seconds: int = 300,
    ):
        """
        Args:
            alert_callback: called with (message, context_dict) when a position
                            is uncovered for too long. If None, only logs a
                            CRITICAL warning.
            timeout_seconds: seconds after which a partial fill is considered
                             stale and eligible for cancellation.
        """
        self._alert_callback = alert_callback
        self._timeout_seconds = timeout_seconds

        # Pending fills: order_id -> fill_event (most recent)
        self._pending: dict[str, dict] = {}
        # Timestamps of first partial detection: order_id -> datetime
        self._first_partial_ts: dict[str, datetime] = {}
        self._lock = threading.Lock()

        # Ensure log directory exists
        _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_fill(self, fill_event: dict) -> dict:
        """Process a fill event and determine required SL/TP adjustments.

        Args:
            fill_event: dict following the fill event schema:
                {order_id, ticker, side, requested_qty, filled_qty,
                 avg_fill_price, remaining_qty, status, timestamp,
                 broker, sl_order_id, tp_order_id}

        Returns:
            Action dict:
              - {action: "COMPLETE"} if fully filled or no action needed
              - {action: "ADJUST_SL", order_id, ticker, side, filled_qty,
                 remaining_qty, sl_order_id, tp_order_id, new_sl_qty,
                 new_tp_qty, broker}
              - {action: "NO_SL", ...} if partial fill but no SL order to adjust
              - {action: "CANCELLED", ...} if order was cancelled with partial fill
              - {action: "INVALID", reason: str} on bad input

        Raises:
            Nothing -- all errors are caught, logged, and returned as INVALID.
        """
        try:
            validation = self._validate_fill_event(fill_event)
            if validation is not None:
                return validation

            order_id = str(fill_event["order_id"])
            status = fill_event["status"].upper()
            filled_qty = float(fill_event["filled_qty"])
            requested_qty = float(fill_event["requested_qty"])
            remaining_qty = float(fill_event["remaining_qty"])
            broker = fill_event.get("broker", "UNKNOWN")

            # Log every fill event
            self._log_event("FILL_RECEIVED", fill_event)

            # FILLED -- order fully completed
            if status == "FILLED" or remaining_qty <= 0:
                self._remove_pending(order_id)
                self._log_event("FILL_COMPLETE", fill_event)
                return {"action": "COMPLETE", "order_id": order_id}

            # CANCELLED -- order cancelled, but may have partial fill
            if status == "CANCELLED":
                self._remove_pending(order_id)
                if filled_qty > 0:
                    # Position exists but order is done -- SL still needed
                    self._log_event("CANCELLED_WITH_PARTIAL", fill_event)
                    return self._build_adjust_action(fill_event)
                return {
                    "action": "CANCELLED",
                    "order_id": order_id,
                    "filled_qty": 0.0,
                }

            # PARTIAL -- the critical case
            if status == "PARTIAL":
                with self._lock:
                    self._pending[order_id] = fill_event
                    if order_id not in self._first_partial_ts:
                        self._first_partial_ts[order_id] = datetime.now(UTC)

                self._log_event("PARTIAL_FILL", fill_event)
                return self._build_adjust_action(fill_event)

            # Unknown status
            logger.warning(
                "Unexpected fill status '%s' for order %s", status, order_id
            )
            return {"action": "COMPLETE", "order_id": order_id}

        except Exception as exc:
            logger.error("Error processing fill event: %s", exc, exc_info=True)
            return {"action": "INVALID", "reason": str(exc)}

    def check_pending_fills(self) -> list[dict]:
        """Check for orders stuck in partial fill state beyond timeout.

        Returns:
            List of dicts:
              {order_id, filled_qty, remaining_qty, elapsed_seconds,
               action: "CANCEL_REMAINING" | "WAIT", broker, ticker}
        """
        now = datetime.now(UTC)
        results = []

        with self._lock:
            stale_ids = []
            for order_id, event in self._pending.items():
                first_ts = self._first_partial_ts.get(order_id, now)
                elapsed = (now - first_ts).total_seconds()
                filled_qty = float(event.get("filled_qty", 0))
                remaining_qty = float(event.get("remaining_qty", 0))
                broker = event.get("broker", "UNKNOWN")
                ticker = event.get("ticker", "")

                if elapsed >= self._timeout_seconds:
                    action = "CANCEL_REMAINING"
                    stale_ids.append(order_id)
                    self._log_event(
                        "TIMEOUT_CANCEL",
                        {
                            "order_id": order_id,
                            "elapsed_seconds": round(elapsed, 1),
                            "filled_qty": filled_qty,
                            "remaining_qty": remaining_qty,
                        },
                    )
                else:
                    action = "WAIT"

                # Alert if uncovered too long
                if elapsed >= UNCOVERED_ALERT_SECONDS:
                    self._trigger_alert(
                        f"Partial fill stuck for {elapsed:.0f}s: "
                        f"{ticker} order {order_id} "
                        f"({filled_qty}/{filled_qty + remaining_qty} filled)",
                        {
                            "order_id": order_id,
                            "ticker": ticker,
                            "elapsed_seconds": round(elapsed, 1),
                            "filled_qty": filled_qty,
                            "remaining_qty": remaining_qty,
                            "broker": broker,
                        },
                    )

                results.append(
                    {
                        "order_id": order_id,
                        "filled_qty": filled_qty,
                        "remaining_qty": remaining_qty,
                        "elapsed_seconds": round(elapsed, 1),
                        "action": action,
                        "broker": broker,
                        "ticker": ticker,
                    }
                )

            # Remove stale orders from pending tracker
            for oid in stale_ids:
                self._pending.pop(oid, None)
                self._first_partial_ts.pop(oid, None)

        return results

    def compute_sl_adjustment(
        self,
        original_sl_order: dict,
        filled_qty: float,
        total_qty: float,
    ) -> dict:
        """Compute adjusted SL order parameters to match the filled quantity.

        Handles instrument-specific rounding:
          - Equities: integer shares
          - FX: round to nearest lot (min 25,000 on IBKR)
          - Futures: integer contracts
          - Crypto: preserve fractional precision (up to 8 decimals)

        Args:
            original_sl_order: dict with at least {order_id, qty, price,
                               instrument_type?, ticker?}
            filled_qty: actually filled quantity of the parent order
            total_qty: total requested quantity of the parent order

        Returns:
            dict with adjusted SL parameters:
              {order_id, original_qty, new_qty, price, adjustment_ratio,
               instrument_type, needs_update: bool}
        """
        sl_order_id = original_sl_order.get("order_id", "")
        original_sl_qty = float(original_sl_order.get("qty", total_qty))
        sl_price = original_sl_order.get("price", 0.0)
        instrument_type = original_sl_order.get(
            "instrument_type", "EQUITY"
        ).upper()

        # Ratio of fill to total
        if total_qty <= 0:
            logger.error("compute_sl_adjustment called with total_qty <= 0")
            return {
                "order_id": sl_order_id,
                "original_qty": original_sl_qty,
                "new_qty": 0.0,
                "price": sl_price,
                "adjustment_ratio": 0.0,
                "instrument_type": instrument_type,
                "needs_update": False,
            }

        adjustment_ratio = filled_qty / total_qty
        raw_new_qty = original_sl_qty * adjustment_ratio

        # Instrument-specific rounding
        new_qty = self._round_qty(raw_new_qty, instrument_type)

        # Safety: new_qty must not exceed filled_qty
        rounded_filled = self._round_qty(filled_qty, instrument_type)
        if new_qty > rounded_filled and instrument_type != "FX":
            new_qty = rounded_filled

        needs_update = abs(new_qty - original_sl_qty) > 1e-10

        return {
            "order_id": sl_order_id,
            "original_qty": original_sl_qty,
            "new_qty": new_qty,
            "price": sl_price,
            "adjustment_ratio": round(adjustment_ratio, 6),
            "instrument_type": instrument_type,
            "needs_update": needs_update,
        }

    def get_exposure_gap(
        self,
        position: dict,
        sl_orders: list[dict],
    ) -> dict:
        """Calculate the gap between actual position size and SL coverage.

        Args:
            position: {symbol, qty, side, ...}
            sl_orders: list of {order_id, qty, status?, ...}
                       Only orders with status in (None, "open", "active",
                       "pending") are counted.

        Returns:
            {symbol, position_qty, covered_qty, uncovered_qty,
             coverage_pct, is_fully_covered: bool}
        """
        symbol = position.get("symbol", position.get("ticker", "UNKNOWN"))
        position_qty = abs(float(position.get("qty", 0)))

        # Sum SL coverage from active orders
        active_statuses = {None, "", "open", "active", "pending", "presubmitted"}
        covered_qty = 0.0
        for sl in sl_orders:
            sl_status = sl.get("status")
            if isinstance(sl_status, str):
                sl_status = sl_status.lower()
            if sl_status in active_statuses:
                covered_qty += abs(float(sl.get("qty", 0)))

        uncovered_qty = max(0.0, position_qty - covered_qty)
        coverage_pct = (covered_qty / position_qty * 100.0) if position_qty > 0 else 100.0
        is_fully_covered = uncovered_qty < 1e-10

        result = {
            "symbol": symbol,
            "position_qty": position_qty,
            "covered_qty": covered_qty,
            "uncovered_qty": round(uncovered_qty, 8),
            "coverage_pct": round(coverage_pct, 2),
            "is_fully_covered": is_fully_covered,
        }

        if not is_fully_covered:
            logger.warning(
                "EXPOSURE GAP: %s has %.4f uncovered (%.1f%% covered)",
                symbol,
                uncovered_qty,
                coverage_pct,
            )
            self._trigger_alert(
                f"Exposure gap on {symbol}: "
                f"{uncovered_qty:.4f} uncovered ({coverage_pct:.1f}% covered)",
                result,
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_adjust_action(self, fill_event: dict) -> dict:
        """Build the ADJUST_SL or NO_SL action dict from a partial fill event."""
        order_id = str(fill_event["order_id"])
        filled_qty = float(fill_event["filled_qty"])
        remaining_qty = float(fill_event["remaining_qty"])
        sl_order_id = fill_event.get("sl_order_id")
        tp_order_id = fill_event.get("tp_order_id")
        broker = fill_event.get("broker", "UNKNOWN")
        ticker = fill_event.get("ticker", "")
        side = fill_event.get("side", "")

        if not sl_order_id:
            self._trigger_alert(
                f"Partial fill on {ticker} (order {order_id}) with NO SL order!",
                {"order_id": order_id, "ticker": ticker, "filled_qty": filled_qty},
            )
            return {
                "action": "NO_SL",
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "filled_qty": filled_qty,
                "remaining_qty": remaining_qty,
                "broker": broker,
            }

        return {
            "action": "ADJUST_SL",
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "new_sl_qty": filled_qty,
            "new_tp_qty": filled_qty,
            "broker": broker,
        }

    def _validate_fill_event(self, fill_event: dict) -> dict | None:
        """Validate fill event fields. Returns an INVALID action dict or None."""
        if not isinstance(fill_event, dict):
            return {"action": "INVALID", "reason": "fill_event must be a dict"}

        required = ("order_id", "filled_qty", "requested_qty", "status")
        missing = [f for f in required if f not in fill_event]
        if missing:
            return {
                "action": "INVALID",
                "reason": f"Missing required fields: {', '.join(missing)}",
            }

        filled_qty = fill_event.get("filled_qty")
        requested_qty = fill_event.get("requested_qty")

        # Type checks
        try:
            filled_qty = float(filled_qty)
            requested_qty = float(requested_qty)
        except (TypeError, ValueError):
            return {
                "action": "INVALID",
                "reason": "filled_qty and requested_qty must be numeric",
            }

        if filled_qty < 0:
            return {
                "action": "INVALID",
                "reason": f"filled_qty cannot be negative: {filled_qty}",
            }

        if requested_qty <= 0:
            return {
                "action": "INVALID",
                "reason": f"requested_qty must be positive: {requested_qty}",
            }

        if filled_qty > requested_qty:
            # Overfill: log warning but still process as complete
            logger.warning(
                "Overfill detected: order %s filled %s > requested %s",
                fill_event.get("order_id"),
                filled_qty,
                requested_qty,
            )
            # Do not reject -- broker overfills happen in practice

        # Default remaining_qty if not provided
        if "remaining_qty" not in fill_event:
            fill_event["remaining_qty"] = max(0.0, requested_qty - filled_qty)

        return None

    @staticmethod
    def _round_qty(qty: float, instrument_type: str) -> float:
        """Round quantity according to instrument type rules.

        - EQUITY: integer shares
        - FUTURES: integer contracts
        - FX: round to nearest lot (min 25,000 on IBKR), floor to int
        - CRYPTO: up to 8 decimal places
        """
        instrument_type = instrument_type.upper()

        if instrument_type in INTEGER_QTY_INSTRUMENTS:
            # Floor to int: never overshoot the filled qty
            return float(max(0, int(qty)))

        if instrument_type == "FX":
            # IBKR FX: round to nearest integer (lots are in units of currency)
            # Minimum lot is 25,000 -- caller must enforce
            rounded = float(max(0, int(round(qty))))
            return rounded

        if instrument_type == "CRYPTO":
            # Preserve fractional precision up to 8 decimals
            return round(max(0.0, qty), 8)

        # Default: round to 2 decimals
        return round(max(0.0, qty), 2)

    def _remove_pending(self, order_id: str) -> None:
        """Remove an order from the pending tracker."""
        with self._lock:
            self._pending.pop(order_id, None)
            self._first_partial_ts.pop(order_id, None)

    def _trigger_alert(self, message: str, context: dict) -> None:
        """Trigger an alert via callback or log as CRITICAL."""
        logger.critical("ALERT: %s", message)
        if self._alert_callback:
            try:
                self._alert_callback(message, context)
            except Exception as exc:
                logger.error("Alert callback failed: %s", exc)

    def _log_event(self, event_type: str, data: dict) -> None:
        """Append an event to the JSONL log file."""
        try:
            record = {
                "ts": datetime.now(UTC).isoformat(),
                "event": event_type,
                **self._serialize_event_data(data),
            }
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            logger.error("Failed to write partial fill log: %s", exc)

    @staticmethod
    def _serialize_event_data(data: dict) -> dict:
        """Make event data JSON-serializable."""
        out = {}
        for k, v in data.items():
            if isinstance(v, datetime):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out
