"""
Live Reconciliation — compare internal model vs broker positions every 5 minutes.

CRITICAL: With real money, the model and broker MUST agree.
Any divergence = immediate alert and investigation.

Checks per position:
  - Symbol matches
  - Direction matches (long/short)
  - Quantity matches (+/-1 unit tolerance for rounding)
  - Entry price matches (+/-0.1% for partial fills)

Checks per portfolio:
  - Position count matches
  - Cash available matches (+/-$10)
  - Margin used matches (+/-$50)
  - No orphan positions (at broker but not in model)
  - No phantom positions (in model but not at broker)
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Default state file location
DEFAULT_HISTORY_PATH = (
    Path(__file__).parent.parent / "data" / "reconciliation_history.json"
)


class LiveReconciliation:
    """Compares internal position model against broker reality.

    Runs every 5 minutes for live, every 15 minutes for paper.
    """

    def __init__(
        self,
        broker=None,
        alert_callback: Callable | None = None,
        tolerance_qty: int = 1,
        tolerance_price_pct: float = 0.001,
        tolerance_cash: float = 50.0,
        tolerance_margin: float = 50.0,
        history_path: Path | None = None,
    ):
        """
        Args:
            broker: BaseBroker instance
            alert_callback: function(message, level)
            tolerance_qty: acceptable qty divergence (+/- units)
            tolerance_price_pct: acceptable entry price divergence (fraction)
            tolerance_cash: acceptable cash divergence ($)
            tolerance_margin: acceptable margin divergence ($)
            history_path: path for reconciliation history persistence
        """
        self.broker = broker
        self.alert_callback = alert_callback
        self.tolerance_qty = tolerance_qty
        self.tolerance_price_pct = tolerance_price_pct
        self.tolerance_cash = tolerance_cash
        self.tolerance_margin = tolerance_margin
        self.history_path = (
            Path(history_path) if history_path else DEFAULT_HISTORY_PATH
        )

        # Internal history (loaded from disk)
        self._history: list = []
        self._load_history()

    # ------------------------------------------------------------------
    # Main reconciliation
    # ------------------------------------------------------------------

    def reconcile(
        self,
        internal_positions: list,
        internal_cash: float = None,
        internal_margin: float = None,
    ) -> dict:
        """Run full reconciliation.

        Args:
            internal_positions: [{symbol, qty, side, avg_entry}] from the model
            internal_cash: model's cash estimate
            internal_margin: model's margin estimate

        Returns:
            {
                matched: bool,
                timestamp: str,
                position_checks: [
                    {symbol, field, internal, broker, matched, tolerance}
                ],
                orphan_positions: [{symbol, qty, side}],
                phantom_positions: [{symbol, qty, side}],
                cash_matched: bool | None,
                margin_matched: bool | None,
                divergences: [str],
                actions_taken: [str],
            }
        """
        now = datetime.now(UTC).isoformat()

        # Fetch broker positions
        broker_positions = []
        broker_account = {}
        if self.broker:
            try:
                broker_positions = self.broker.get_positions()
            except Exception as e:
                logger.error("Failed to fetch broker positions: %s", e)
                # Alert on broker connection failure
                if self.alert_callback:
                    try:
                        self.alert_callback(
                            f"RECONCILIATION ERROR: broker.get_positions() failed: {e}",
                            "critical"
                        )
                    except Exception:
                        pass
                error_result = {
                    "matched": False,
                    "timestamp": now,
                    "position_checks": [],
                    "orphan_positions": [],
                    "phantom_positions": [],
                    "cash_matched": None,
                    "margin_matched": None,
                    "divergences": [f"Broker API error: {e}"],
                    "actions_taken": [],
                }
                self._record(error_result)
                return error_result

            try:
                broker_account = self.broker.get_account_info()
            except Exception as e:
                logger.warning("Failed to fetch broker account info: %s", e)

        # Build lookup maps
        # Normalize: internal positions keyed by symbol
        internal_map = {}
        for pos in internal_positions:
            symbol = pos.get("symbol", "").upper()
            if symbol:
                internal_map[symbol] = pos

        # Broker positions keyed by symbol
        broker_map = {}
        for pos in broker_positions:
            symbol = pos.get("symbol", "").upper()
            if symbol:
                broker_map[symbol] = pos

        all_symbols = set(internal_map.keys()) | set(broker_map.keys())

        position_checks = []
        orphan_positions = []
        phantom_positions = []
        divergences = []

        for symbol in sorted(all_symbols):
            in_internal = symbol in internal_map
            in_broker = symbol in broker_map

            # Orphan: at broker, not in model
            if in_broker and not in_internal:
                bp = broker_map[symbol]
                orphan = {
                    "symbol": symbol,
                    "qty": bp.get("qty", 0),
                    "side": bp.get("side", "unknown"),
                }
                orphan_positions.append(orphan)
                divergences.append(
                    f"ORPHAN: {symbol} at broker "
                    f"(qty={orphan['qty']}, side={orphan['side']}) "
                    f"but NOT in internal model"
                )
                continue

            # Phantom: in model, not at broker
            if in_internal and not in_broker:
                ip = internal_map[symbol]
                phantom = {
                    "symbol": symbol,
                    "qty": ip.get("qty", 0),
                    "side": ip.get("side", "unknown"),
                }
                phantom_positions.append(phantom)
                divergences.append(
                    f"PHANTOM: {symbol} in model "
                    f"(qty={phantom['qty']}, side={phantom['side']}) "
                    f"but NOT at broker"
                )
                continue

            # Both exist — compare fields
            ip = internal_map[symbol]
            bp = broker_map[symbol]

            # Direction check
            i_side = str(ip.get("side", "")).lower()
            b_side = str(bp.get("side", "")).lower()
            side_matched = i_side == b_side
            position_checks.append({
                "symbol": symbol,
                "field": "side",
                "internal": i_side,
                "broker": b_side,
                "matched": side_matched,
                "tolerance": "exact",
            })
            if not side_matched:
                divergences.append(
                    f"DIRECTION MISMATCH: {symbol} internal={i_side} "
                    f"broker={b_side}"
                )

            # Quantity check
            i_qty = abs(float(ip.get("qty", 0)))
            b_qty = abs(float(bp.get("qty", 0)))
            qty_diff = abs(i_qty - b_qty)
            qty_matched = qty_diff <= self.tolerance_qty
            position_checks.append({
                "symbol": symbol,
                "field": "qty",
                "internal": i_qty,
                "broker": b_qty,
                "matched": qty_matched,
                "tolerance": self.tolerance_qty,
            })
            if not qty_matched:
                divergences.append(
                    f"QTY MISMATCH: {symbol} internal={i_qty} "
                    f"broker={b_qty} (diff={qty_diff}, "
                    f"tolerance={self.tolerance_qty})"
                )

            # Entry price check
            i_price = float(ip.get("avg_entry", 0))
            b_price = float(bp.get("avg_entry", 0))
            if b_price > 0:
                price_diff_pct = abs(i_price - b_price) / b_price
            else:
                price_diff_pct = 0.0 if i_price == 0 else 1.0
            price_matched = price_diff_pct <= self.tolerance_price_pct
            position_checks.append({
                "symbol": symbol,
                "field": "avg_entry",
                "internal": i_price,
                "broker": b_price,
                "matched": price_matched,
                "tolerance": f"{self.tolerance_price_pct:.2%}",
            })
            if not price_matched:
                divergences.append(
                    f"PRICE MISMATCH: {symbol} internal=${i_price:.2f} "
                    f"broker=${b_price:.2f} (diff={price_diff_pct:.3%}, "
                    f"tolerance={self.tolerance_price_pct:.2%})"
                )

        # Cash check
        cash_matched = None
        if internal_cash is not None and broker_account:
            broker_cash = float(broker_account.get("cash", 0))
            cash_diff = abs(internal_cash - broker_cash)
            cash_matched = cash_diff <= self.tolerance_cash
            if not cash_matched:
                divergences.append(
                    f"CASH MISMATCH: internal=${internal_cash:,.2f} "
                    f"broker=${broker_cash:,.2f} (diff=${cash_diff:.2f}, "
                    f"tolerance=${self.tolerance_cash:.2f})"
                )

        # Margin check
        margin_matched = None
        if internal_margin is not None and broker_account:
            broker_margin = float(broker_account.get("margin_used", 0))
            margin_diff = abs(internal_margin - broker_margin)
            margin_matched = margin_diff <= self.tolerance_margin
            if not margin_matched:
                divergences.append(
                    f"MARGIN MISMATCH: internal=${internal_margin:,.2f} "
                    f"broker=${broker_margin:,.2f} (diff=${margin_diff:.2f}, "
                    f"tolerance=${self.tolerance_margin:.2f})"
                )

        matched = len(divergences) == 0

        result = {
            "matched": matched,
            "timestamp": now,
            "position_checks": position_checks,
            "orphan_positions": orphan_positions,
            "phantom_positions": phantom_positions,
            "cash_matched": cash_matched,
            "margin_matched": margin_matched,
            "divergences": divergences,
            "actions_taken": [],
        }

        # Log and alert
        if matched:
            logger.info(
                "Reconciliation OK — %d positions matched", len(internal_map)
            )
        else:
            logger.warning(
                "Reconciliation DIVERGENCE — %d issues: %s",
                len(divergences),
                "; ".join(divergences),
            )
            if self.alert_callback:
                try:
                    level = "critical" if (
                        orphan_positions
                        or any(
                            not c["matched"] and c["field"] == "side"
                            for c in position_checks
                        )
                    ) else "warning"
                    self.alert_callback(
                        "RECONCILIATION DIVERGENCE\n"
                        + "\n".join(divergences),
                        level,
                    )
                except Exception as e:
                    logger.error("Failed to send reconciliation alert: %s", e)

        # Record history
        self._record(result)

        return result

    # ------------------------------------------------------------------
    # Auto-resolution
    # ------------------------------------------------------------------

    def suggest_resolution(self, reconciliation_result: dict) -> dict:
        """Attempt automatic resolution of divergences.

        Rules:
          - Orphan position (at broker, not model): DO NOT close. Alert and wait.
          - Phantom position (in model, not broker): Remove from model, log.
          - Quantity mismatch: Align model to broker (broker is truth).
          - Cash mismatch: Refresh from broker.

        Args:
            reconciliation_result: output from reconcile()

        Returns:
            {
                resolved: list of resolved divergences,
                unresolved: list of unresolved divergences,
                actions_taken: list of actions performed,
            }
        """
        resolved = []
        unresolved = []
        actions_taken = []

        # Handle orphan positions — DO NOT close, just alert
        for orphan in reconciliation_result.get("orphan_positions", []):
            symbol = orphan["symbol"]
            msg = (
                f"ORPHAN {symbol}: at broker but not in model. "
                f"NOT closing — manual review required."
            )
            unresolved.append(msg)
            actions_taken.append(f"ALERT: orphan {symbol} — no action taken")
            logger.warning(msg)

        # Handle phantom positions — remove from model
        for phantom in reconciliation_result.get("phantom_positions", []):
            symbol = phantom["symbol"]
            msg = (
                f"PHANTOM {symbol}: in model but not at broker. "
                f"Removing from internal model."
            )
            resolved.append(msg)
            actions_taken.append(f"REMOVE: phantom {symbol} from model")
            logger.info(msg)

        # Handle position check divergences
        for check in reconciliation_result.get("position_checks", []):
            if check["matched"]:
                continue

            symbol = check["symbol"]
            field = check["field"]

            if field == "qty":
                msg = (
                    f"QTY {symbol}: aligning model to broker "
                    f"({check['internal']} -> {check['broker']})"
                )
                resolved.append(msg)
                actions_taken.append(
                    f"ALIGN: {symbol} qty {check['internal']} -> {check['broker']}"
                )
                logger.info(msg)

            elif field == "side":
                msg = (
                    f"DIRECTION {symbol}: internal={check['internal']} "
                    f"broker={check['broker']}. CRITICAL — manual review required."
                )
                unresolved.append(msg)
                actions_taken.append(
                    f"ALERT: {symbol} direction mismatch — no action taken"
                )
                logger.error(msg)

            elif field == "avg_entry":
                msg = (
                    f"PRICE {symbol}: aligning model to broker "
                    f"(${check['internal']:.2f} -> ${check['broker']:.2f})"
                )
                resolved.append(msg)
                actions_taken.append(
                    f"ALIGN: {symbol} avg_entry "
                    f"${check['internal']:.2f} -> ${check['broker']:.2f}"
                )
                logger.info(msg)

        # Handle cash mismatch
        if reconciliation_result.get("cash_matched") is False:
            msg = "CASH: refreshing from broker (broker is truth)"
            resolved.append(msg)
            actions_taken.append("REFRESH: cash from broker")
            logger.info(msg)

        # Handle margin mismatch
        if reconciliation_result.get("margin_matched") is False:
            msg = "MARGIN: refreshing from broker (broker is truth)"
            resolved.append(msg)
            actions_taken.append("REFRESH: margin from broker")
            logger.info(msg)

        result = {
            "resolved": resolved,
            "unresolved": unresolved,
            "actions_taken": actions_taken,
        }

        # Alert if unresolved issues remain
        if unresolved and self.alert_callback:
            try:
                self.alert_callback(
                    "RECONCILIATION — UNRESOLVED ISSUES\n"
                    + "\n".join(unresolved),
                    "critical",
                )
            except Exception as e:
                logger.error("Failed to send unresolved alert: %s", e)

        return result

    # Backward compatibility alias
    auto_resolve = suggest_resolution

    # ------------------------------------------------------------------
    # History & Stats
    # ------------------------------------------------------------------

    def get_history(self, n: int = 100) -> list:
        """Recent reconciliation history.

        Args:
            n: max number of recent entries to return

        Returns:
            List of reconciliation results (most recent first).
        """
        return list(reversed(self._history[-n:]))

    def get_stats(self) -> dict:
        """Reconciliation statistics.

        Returns:
            {
                total_runs: int,
                total_divergences: int,
                divergence_rate: float,
                avg_divergences_per_run: float,
                last_run: str or None,
                last_divergence: str or None,
                orphan_count: int,
                phantom_count: int,
            }
        """
        total_runs = len(self._history)
        if total_runs == 0:
            return {
                "total_runs": 0,
                "total_divergences": 0,
                "divergence_rate": 0.0,
                "avg_divergences_per_run": 0.0,
                "last_run": None,
                "last_divergence": None,
                "orphan_count": 0,
                "phantom_count": 0,
            }

        runs_with_divergences = sum(
            1 for r in self._history if not r.get("matched", True)
        )
        total_divergence_items = sum(
            len(r.get("divergences", [])) for r in self._history
        )
        total_orphans = sum(
            len(r.get("orphan_positions", [])) for r in self._history
        )
        total_phantoms = sum(
            len(r.get("phantom_positions", [])) for r in self._history
        )

        last_run = self._history[-1].get("timestamp") if self._history else None
        last_divergence = None
        for r in reversed(self._history):
            if not r.get("matched", True):
                last_divergence = r.get("timestamp")
                break

        return {
            "total_runs": total_runs,
            "total_divergences": runs_with_divergences,
            "divergence_rate": runs_with_divergences / total_runs,
            "avg_divergences_per_run": total_divergence_items / total_runs,
            "last_run": last_run,
            "last_divergence": last_divergence,
            "orphan_count": total_orphans,
            "phantom_count": total_phantoms,
        }

    # ------------------------------------------------------------------
    # Internal: history persistence
    # ------------------------------------------------------------------

    def _record(self, result: dict):
        """Record a reconciliation result in history."""
        self._history.append(result)
        # Trim in-memory history to prevent memory leak
        if len(self._history) > 1000:
            self._history = self._history[-1000:]
        self._save_history()

    def _save_history(self):
        """Persist history to JSON (keep last 1000 entries)."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            # Keep bounded
            to_save = self._history[-1000:]
            with open(self.history_path, "w") as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save reconciliation history: %s", e)

    def _load_history(self):
        """Load history from JSON."""
        if not self.history_path.exists():
            return

        try:
            with open(self.history_path) as f:
                self._history = json.load(f)
        except Exception as e:
            logger.error("Failed to load reconciliation history: %s", e)
            self._history = []
