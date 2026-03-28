"""
Live Kill Switch — emergency position closure for live trading.

3 activation methods:
  1. AUTOMATIC: When calibrated MC thresholds are breached
  2. MANUAL: Via Telegram /kill CONFIRM command
  3. DIRECT: Positions closed manually in TWS (backup)

Sequence:
  1. Cancel ALL open orders (live only)
  2. Close ALL positions (market orders)
  3. Disable ALL live strategies
  4. Send Telegram alert
  5. Log everything
  6. Paper trading continues unaffected
  7. Wait for /resume to reactivate

CRITICAL: This module handles REAL MONEY. Every path must be tested.
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Default thresholds — conservative for real money
DEFAULT_THRESHOLDS = {
    "daily_loss_pct": 0.015,        # -1.5% daily
    "hourly_loss_pct": 0.01,        # -1.0% hourly
    "trailing_5d_loss_pct": 0.03,   # -3.0% rolling 5d
    "monthly_loss_pct": 0.05,       # -5.0% monthly
    "strategy_loss_pct": 0.02,      # -2.0% per strategy (MC override available)
}

# Default state file location
DEFAULT_STATE_PATH = Path(__file__).parent.parent / "data" / "kill_switch_state.json"


class LiveKillSwitch:
    """Kill switch for live trading with multiple trigger methods.

    State persisted in data/kill_switch_state.json.
    """

    def __init__(
        self,
        broker=None,
        alert_callback: Optional[Callable] = None,
        state_path: Optional[Path] = None,
        thresholds: Optional[dict] = None,
        mc_overrides: Optional[dict] = None,
    ):
        """
        Args:
            broker: BaseBroker instance for the LIVE account
            alert_callback: function(message, level) for alerts
            state_path: path for state persistence
            thresholds: override default thresholds
            mc_overrides: {strategy_name: threshold} from MC calibration
        """
        self.broker = broker
        self.alert_callback = alert_callback
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.mc_overrides = mc_overrides or {}

        # Thread-safety lock for activate
        self._activate_lock = threading.Lock()

        # Internal state
        self._active = False
        self._armed = True
        self._activated_at: Optional[str] = None
        self._activation_reason: Optional[str] = None
        self._activation_trigger: Optional[str] = None
        self._history: list = []
        self._disabled_strategies: set = set()

        # Load persisted state if exists
        self._load_state()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True if kill switch is currently activated (trading disabled)."""
        return self._active

    @property
    def is_armed(self) -> bool:
        """True if kill switch is armed (monitoring for triggers)."""
        return self._armed

    # ------------------------------------------------------------------
    # Trigger checks
    # ------------------------------------------------------------------

    def check_automatic_triggers(
        self,
        daily_pnl: float,
        capital: float,
        hourly_pnl: float = None,
        rolling_5d_pnl: float = None,
        monthly_pnl: float = None,
        strategy_pnls: dict = None,
    ) -> dict:
        """Check all automatic kill switch triggers.

        Triggers:
          - Daily loss > circuit_breaker threshold
          - 5-day rolling loss > trailing threshold
          - Monthly loss > monthly threshold
          - Per-strategy loss > strategy threshold (MC calibrated)

        Args:
            daily_pnl: today's P&L in dollars
            capital: current capital (must be > 0)
            rolling_5d_pnl: sum of last 5 days P&L
            monthly_pnl: month-to-date P&L
            strategy_pnls: {strategy_name: rolling_5d_pnl}

        Returns:
            {triggered: bool, reason: str, trigger_type: str, details: dict}
        """
        if capital <= 0:
            return {
                "triggered": True,
                "reason": "Capital is zero or negative",
                "trigger_type": "CAPITAL_ZERO",
                "details": {"capital": capital},
            }

        result = {
            "triggered": False,
            "reason": "",
            "trigger_type": "",
            "details": {},
        }

        # 1. Daily loss check
        daily_loss_pct = daily_pnl / capital
        daily_threshold = self.thresholds["daily_loss_pct"]
        if daily_loss_pct < -daily_threshold:
            result.update({
                "triggered": True,
                "reason": (
                    f"Daily loss {daily_loss_pct:.2%} exceeds "
                    f"-{daily_threshold:.2%} threshold"
                ),
                "trigger_type": "DAILY_LOSS",
                "details": {
                    "daily_pnl": daily_pnl,
                    "daily_loss_pct": daily_loss_pct,
                    "threshold": daily_threshold,
                    "capital": capital,
                },
            })
            return result

        # 1.5. Hourly loss check
        if hourly_pnl is not None:
            hourly_loss_pct = hourly_pnl / capital
            hourly_threshold = self.thresholds["hourly_loss_pct"]
            if hourly_loss_pct < -hourly_threshold:
                result.update({
                    "triggered": True,
                    "reason": (
                        f"Hourly loss {hourly_loss_pct:.2%} exceeds "
                        f"-{hourly_threshold:.2%} threshold"
                    ),
                    "trigger_type": "HOURLY_LOSS",
                    "details": {
                        "hourly_pnl": hourly_pnl,
                        "hourly_loss_pct": hourly_loss_pct,
                        "threshold": hourly_threshold,
                        "capital": capital,
                    },
                })
                return result

        # 2. Rolling 5-day loss check
        if rolling_5d_pnl is not None:
            rolling_loss_pct = rolling_5d_pnl / capital
            trailing_threshold = self.thresholds["trailing_5d_loss_pct"]
            if rolling_loss_pct < -trailing_threshold:
                result.update({
                    "triggered": True,
                    "reason": (
                        f"5-day rolling loss {rolling_loss_pct:.2%} exceeds "
                        f"-{trailing_threshold:.2%} threshold"
                    ),
                    "trigger_type": "ROLLING_5D_LOSS",
                    "details": {
                        "rolling_5d_pnl": rolling_5d_pnl,
                        "rolling_loss_pct": rolling_loss_pct,
                        "threshold": trailing_threshold,
                        "capital": capital,
                    },
                })
                return result

        # 3. Monthly loss check
        if monthly_pnl is not None:
            monthly_loss_pct = monthly_pnl / capital
            monthly_threshold = self.thresholds["monthly_loss_pct"]
            if monthly_loss_pct < -monthly_threshold:
                result.update({
                    "triggered": True,
                    "reason": (
                        f"Monthly loss {monthly_loss_pct:.2%} exceeds "
                        f"-{monthly_threshold:.2%} threshold"
                    ),
                    "trigger_type": "MONTHLY_LOSS",
                    "details": {
                        "monthly_pnl": monthly_pnl,
                        "monthly_loss_pct": monthly_loss_pct,
                        "threshold": monthly_threshold,
                        "capital": capital,
                    },
                })
                return result

        # 4. Per-strategy loss check
        if strategy_pnls:
            default_strat_threshold = self.thresholds["strategy_loss_pct"]
            for strat_name, strat_pnl in strategy_pnls.items():
                # Use MC-calibrated threshold if available, else default
                strat_threshold = abs(
                    self.mc_overrides.get(strat_name, -default_strat_threshold)
                )
                strat_loss_pct = strat_pnl / capital
                if strat_loss_pct < -strat_threshold:
                    result.update({
                        "triggered": True,
                        "reason": (
                            f"Strategy '{strat_name}' loss {strat_loss_pct:.2%} "
                            f"exceeds -{strat_threshold:.2%} threshold"
                        ),
                        "trigger_type": "STRATEGY_LOSS",
                        "details": {
                            "strategy": strat_name,
                            "strategy_pnl": strat_pnl,
                            "strategy_loss_pct": strat_loss_pct,
                            "threshold": strat_threshold,
                            "mc_calibrated": strat_name in self.mc_overrides,
                            "capital": capital,
                        },
                    })
                    return result

        return result

    # ------------------------------------------------------------------
    # Per-strategy calibrated thresholds
    # ------------------------------------------------------------------

    def check_strategy_thresholds(self, strategy_pnls: dict, capital: float) -> dict:
        """Check per-strategy kill switch thresholds from config.

        Uses calibrated thresholds per strategy type instead of a single default.

        Args:
            strategy_pnls: {strategy_name: rolling_pnl_dollars}
            capital: current capital

        Returns:
            {triggered: bool, disabled_strategies: list, reason: str, details: dict}
        """
        if capital <= 0:
            return {"triggered": False, "disabled_strategies": [], "reason": "Capital <= 0", "details": {}}

        # Load calibrated thresholds
        thresholds_path = Path(__file__).parent.parent / "config" / "kill_switch_thresholds.yaml"
        try:
            import yaml
            with open(thresholds_path) as f:
                config = yaml.safe_load(f)
        except Exception:
            config = {}

        strategy_thresholds = config.get("strategy_thresholds", {})

        # Build strategy -> threshold mapping
        strat_to_threshold = {}
        for group_name, group_cfg in strategy_thresholds.items():
            threshold = group_cfg.get("per_strategy_max_loss_pct", 2.0) / 100.0
            for strat in group_cfg.get("strategies", []):
                strat_to_threshold[strat] = threshold

        disabled = []
        details = {}

        for strat_name, pnl in strategy_pnls.items():
            threshold = strat_to_threshold.get(strat_name, self.thresholds.get("strategy_loss_pct", 0.02))
            loss_pct = pnl / capital

            details[strat_name] = {
                "pnl": pnl,
                "loss_pct": loss_pct,
                "threshold": threshold,
                "triggered": loss_pct < -threshold,
            }

            if loss_pct < -threshold:
                disabled.append(strat_name)
                logger.warning(
                    f"STRATEGY KILL SWITCH: {strat_name} loss {loss_pct:.2%} "
                    f"exceeds calibrated threshold -{threshold:.2%}"
                )

        return {
            "triggered": len(disabled) > 0,
            "disabled_strategies": disabled,
            "reason": f"Strategies disabled: {', '.join(disabled)}" if disabled else "OK",
            "details": details,
        }

    # ------------------------------------------------------------------
    # Activation / Deactivation
    # ------------------------------------------------------------------

    def activate(self, reason: str, trigger_type: str = "AUTOMATIC") -> dict:
        """ACTIVATE the kill switch — close everything.

        Idempotent: calling when already active returns the existing state
        without attempting to close positions again.

        Returns:
            {success: bool, positions_closed: int, orders_cancelled: int,
             pnl_at_close: float, timestamp: str, reason: str}
        """
        with self._activate_lock:
            now = datetime.now(timezone.utc).isoformat()

            # Idempotent — already active
            if self._active:
                logger.warning(
                    "Kill switch already active (since %s). Reason: %s",
                    self._activated_at,
                    self._activation_reason,
                )
                return {
                    "success": True,
                    "already_active": True,
                    "positions_closed": 0,
                    "orders_cancelled": 0,
                    "pnl_at_close": 0.0,
                    "timestamp": now,
                    "reason": self._activation_reason,
                }

            logger.critical(
                "KILL SWITCH ACTIVATED — trigger=%s reason=%s",
                trigger_type,
                reason,
            )

            orders_cancelled = 0
            positions_closed = 0
            pnl_at_close = 0.0
            errors = []

            # Step 1: Cancel all open orders
            try:
                orders_cancelled = self._cancel_all_orders()
                logger.info("Cancelled %d open orders", orders_cancelled)
            except Exception as e:
                err = f"Failed to cancel orders: {e}"
                logger.error(err)
                errors.append(err)

            # Step 2: Close all positions
            try:
                closed = self._close_all_positions()
                positions_closed = len(closed)
                pnl_at_close = sum(p.get("unrealized_pl", 0.0) for p in closed)
                logger.info(
                    "Closed %d positions, unrealized P&L at close: $%.2f",
                    positions_closed,
                    pnl_at_close,
                )
            except Exception as e:
                err = f"Failed to close positions: {e}"
                logger.error(err)
                errors.append(err)

            # Retry cancel if first attempt failed — prevent orphan orders reopening positions
            if orders_cancelled == 0 and errors:
                try:
                    orders_cancelled = self._cancel_all_orders()
                    logger.info("Retry cancel succeeded: %d orders cancelled", orders_cancelled)
                    errors = [e for e in errors if "cancel" not in e.lower()]
                except Exception as e2:
                    logger.error("Retry cancel also failed: %s", e2)

            # Step 3: Update internal state
            self._active = True
            self._activated_at = now
            self._activation_reason = reason
            self._activation_trigger = trigger_type

            # Step 4: Record in history
            event = {
                "action": "ACTIVATE",
                "timestamp": now,
                "trigger_type": trigger_type,
                "reason": reason,
                "positions_closed": positions_closed,
                "orders_cancelled": orders_cancelled,
                "pnl_at_close": pnl_at_close,
                "errors": errors,
            }
            self._history.append(event)

            # Step 5: Persist state
            self._save_state()

            # Step 6: Send alert
            if self.alert_callback:
                try:
                    alert_msg = (
                        f"KILL SWITCH ACTIVATED\n"
                        f"Trigger: {trigger_type}\n"
                        f"Reason: {reason}\n"
                        f"Positions closed: {positions_closed}\n"
                        f"Orders cancelled: {orders_cancelled}\n"
                        f"P&L at close: ${pnl_at_close:+,.2f}"
                    )
                    self.alert_callback(alert_msg, "critical")
                except Exception as e:
                    logger.error("Failed to send kill switch alert: %s", e)

            return {
                "success": len(errors) == 0,
                "already_active": False,
                "positions_closed": positions_closed,
                "orders_cancelled": orders_cancelled,
                "pnl_at_close": pnl_at_close,
                "timestamp": now,
                "reason": reason,
                "errors": errors,
            }

    def deactivate(self, authorized_by: str = "MANUAL") -> dict:
        """Deactivate the kill switch — allow trading again.

        Requires explicit authorization (who deactivated and why).

        Returns:
            {success: bool, downtime_minutes: float, timestamp: str}
        """
        now = datetime.now(timezone.utc).isoformat()

        if not self._active:
            logger.info("Kill switch is not active — nothing to deactivate.")
            return {
                "success": True,
                "was_active": False,
                "downtime_minutes": 0.0,
                "timestamp": now,
            }

        # Calculate downtime
        downtime_minutes = 0.0
        if self._activated_at:
            activated = datetime.fromisoformat(self._activated_at)
            now_dt = datetime.fromisoformat(now)
            downtime_minutes = (now_dt - activated).total_seconds() / 60.0

        logger.info(
            "Kill switch DEACTIVATED by %s after %.1f minutes",
            authorized_by,
            downtime_minutes,
        )

        # Record in history
        event = {
            "action": "DEACTIVATE",
            "timestamp": now,
            "authorized_by": authorized_by,
            "downtime_minutes": downtime_minutes,
            "previous_reason": self._activation_reason,
        }
        self._history.append(event)

        # Reset state
        self._active = False
        self._activated_at = None
        self._activation_reason = None
        self._activation_trigger = None
        self._disabled_strategies.clear()

        # Persist
        self._save_state()

        # Alert
        if self.alert_callback:
            try:
                self.alert_callback(
                    f"Kill switch DEACTIVATED by {authorized_by}. "
                    f"Downtime: {downtime_minutes:.1f} min.",
                    "warning",
                )
            except Exception as e:
                logger.error("Failed to send deactivation alert: %s", e)

        return {
            "success": True,
            "was_active": True,
            "downtime_minutes": downtime_minutes,
            "timestamp": now,
            "authorized_by": authorized_by,
        }

    # ------------------------------------------------------------------
    # Status & History
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Current kill switch status with full details."""
        return {
            "is_active": self._active,
            "is_armed": self._armed,
            "activated_at": self._activated_at,
            "activation_reason": self._activation_reason,
            "activation_trigger": self._activation_trigger,
            "thresholds": dict(self.thresholds),
            "mc_overrides": dict(self.mc_overrides),
            "disabled_strategies": list(self._disabled_strategies),
            "total_activations": sum(
                1 for e in self._history if e["action"] == "ACTIVATE"
            ),
        }

    def get_history(self) -> list:
        """History of all kill switch activations/deactivations."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal: broker operations
    # ------------------------------------------------------------------

    def _close_all_positions(self) -> list:
        """Close ALL live positions via market orders.

        Returns list of closed position dicts with unrealized_pl.
        """
        if not self.broker:
            logger.warning("No broker configured — cannot close positions")
            return []

        positions = self.broker.get_positions()
        closed = []
        for pos in positions:
            symbol = pos.get("symbol", "UNKNOWN")
            try:
                self.broker.close_position(
                    symbol, _authorized_by="KILL_SWITCH"
                )
                closed.append(pos)
                logger.info("Closed position: %s", symbol)
            except Exception as e:
                logger.error("Failed to close %s: %s", symbol, e)
        return closed

    def _cancel_all_orders(self) -> int:
        """Cancel ALL open live orders.

        Returns number of orders cancelled.
        """
        if not self.broker:
            logger.warning("No broker configured — cannot cancel orders")
            return 0

        try:
            count = self.broker.cancel_all_orders(
                _authorized_by="KILL_SWITCH"
            )
            return count
        except Exception as e:
            logger.error("Failed to cancel all orders: %s", e)
            raise

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self):
        """Persist state to JSON."""
        state = {
            "active": self._active,
            "armed": self._armed,
            "activated_at": self._activated_at,
            "activation_reason": self._activation_reason,
            "activation_trigger": self._activation_trigger,
            "disabled_strategies": list(self._disabled_strategies),
            "history": self._history,
            "thresholds": self.thresholds,
            "mc_overrides": self.mc_overrides,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save kill switch state: %s", e)

    def _load_state(self):
        """Load state from JSON."""
        if not self.state_path.exists():
            return

        try:
            with open(self.state_path, "r") as f:
                state = json.load(f)

            self._active = state.get("active", False)
            self._armed = state.get("armed", True)
            self._activated_at = state.get("activated_at")
            self._activation_reason = state.get("activation_reason")
            self._activation_trigger = state.get("activation_trigger")
            self._disabled_strategies = set(
                state.get("disabled_strategies", [])
            )
            self._history = state.get("history", [])

            if self._active:
                logger.warning(
                    "Kill switch LOADED in ACTIVE state (since %s): %s",
                    self._activated_at,
                    self._activation_reason,
                )
        except Exception as e:
            logger.error("Failed to load kill switch state: %s", e)
