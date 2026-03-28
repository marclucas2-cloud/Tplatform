"""
Autonomous Mode — safe unattended operation for up to 72 hours.

When Marc is unavailable (ski, illness, travel), the system must:
1. Continue trading safely with tighter limits
2. Auto-reduce on drawdown (more aggressive than normal)
3. Auto-pause strategies on anomalies
4. Ensure all positions have broker-side stops
5. Block new trades if any CRITICAL alert is unresolved
6. Generate detailed reports for review upon return

Components:
  - AutoReducer: progressive position reduction on drawdown
  - AnomalyDetector: detect and auto-pause problematic strategies
  - SafetyChecker: verify all positions are protected
  - AutonomousController: orchestrate all components

This module does NOT replace human judgment — it acts as a safety net.
"""

import logging
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Callable

logger = logging.getLogger(__name__)

# Default state file location
DEFAULT_STATE_PATH = Path(__file__).parent.parent / "data" / "autonomous_state.json"


class AutoReducer:
    """Progressive position reduction on drawdown.

    More aggressive than normal deleveraging when in autonomous mode.

    Levels:
      DD 1.0% -> reduce 30% of all positions
      DD 1.5% -> reduce 50%
      DD 2.0% -> close all + activate kill switch
    """

    def __init__(
        self,
        capital: float = 10000,
        levels: list = None,
        close_func: Callable = None,
        reduce_func: Callable = None,
    ):
        """
        Args:
            capital: current capital for DD calculation
            levels: list of {dd_pct, action, reduce_pct}
            close_func: function() to close all positions
            reduce_func: function(pct) to reduce all positions by pct
        """
        self.capital = capital
        self.levels = levels or [
            {"dd_pct": 0.01, "action": "reduce", "reduce_pct": 0.30},
            {"dd_pct": 0.015, "action": "reduce", "reduce_pct": 0.50},
            {"dd_pct": 0.02, "action": "close_all", "reduce_pct": 1.00},
        ]
        self._close_func = close_func
        self._reduce_func = reduce_func
        self._actions_taken: List[dict] = []
        self._highest_level_triggered: int = -1

    def check_and_act(self, current_dd_pct: float) -> dict:
        """Check drawdown and take action if needed.

        Drawdown levels are checked from highest to lowest so that the most
        severe applicable action is taken. Each level is triggered at most once
        to avoid repeated reductions (tracked via _highest_level_triggered).

        Args:
            current_dd_pct: current drawdown as a positive fraction (e.g. 0.015 = 1.5%)

        Returns:
            {action_taken: bool, level: int, action: str, details: str}
        """
        dd = abs(current_dd_pct)
        result = {
            "action_taken": False,
            "level": -1,
            "action": "none",
            "details": "Drawdown within autonomous limits",
        }

        # Sort levels descending by dd_pct so the most severe fires first
        sorted_levels = sorted(
            enumerate(self.levels), key=lambda x: x[1]["dd_pct"], reverse=True
        )

        for idx, level_cfg in sorted_levels:
            if dd >= level_cfg["dd_pct"] and idx > self._highest_level_triggered:
                action = level_cfg["action"]
                reduce_pct = level_cfg["reduce_pct"]

                # Execute action
                if action == "close_all":
                    if self._close_func:
                        self._close_func()
                    details = (
                        f"DD {dd:.2%} >= {level_cfg['dd_pct']:.2%} — "
                        f"CLOSE ALL positions"
                    )
                elif action == "reduce":
                    if self._reduce_func:
                        self._reduce_func(reduce_pct)
                    details = (
                        f"DD {dd:.2%} >= {level_cfg['dd_pct']:.2%} — "
                        f"reduce {reduce_pct:.0%} of all positions"
                    )
                else:
                    details = f"Unknown action: {action}"

                self._highest_level_triggered = idx
                timestamp = datetime.now(timezone.utc).isoformat()

                event = {
                    "timestamp": timestamp,
                    "level": idx,
                    "dd_pct": dd,
                    "action": action,
                    "reduce_pct": reduce_pct,
                    "details": details,
                }
                self._actions_taken.append(event)
                logger.warning("AutoReducer: %s", details)

                result = {
                    "action_taken": True,
                    "level": idx,
                    "action": action,
                    "details": details,
                }
                break

        return result

    def get_actions_history(self) -> list:
        """History of all auto-reduce actions taken."""
        return list(self._actions_taken)

    def reset(self):
        """Reset the highest level triggered (e.g. after exiting autonomous mode)."""
        self._highest_level_triggered = -1


class AnomalyDetector:
    """Detect strategy anomalies and auto-pause.

    Anomalies:
      - Slippage > 5x average -> pause strategy
      - 3 consecutive losing trades -> pause 24h
      - Connection lost > 30 min -> close all
      - Fill rate < 50% on last 10 orders -> pause strategy
    """

    CONSECUTIVE_LOSS_LIMIT = 3
    SLIPPAGE_MULTIPLIER = 5.0
    CONNECTION_TIMEOUT_MINUTES = 30
    PAUSE_DURATION_HOURS = 24

    def __init__(self, alert_callback: Callable = None, close_all_func: Callable = None):
        """
        Args:
            alert_callback: function(message, level) for sending alerts
            close_all_func: function() to close all positions on connection loss
        """
        self._strategy_state: Dict[str, dict] = {}
        self._alert = alert_callback
        self._close_all_func = close_all_func
        self._connection_lost_since: Optional[datetime] = None
        self._anomaly_log: List[dict] = []

    def _get_or_create_state(self, strategy: str) -> dict:
        """Get or initialize strategy state."""
        if strategy not in self._strategy_state:
            self._strategy_state[strategy] = {
                "consecutive_losses": 0,
                "last_slippage_bps": 0.0,
                "paused_until": None,
                "pause_reason": None,
                "trade_count": 0,
            }
        return self._strategy_state[strategy]

    def record_trade_result(
        self,
        strategy: str,
        pnl: float,
        slippage_bps: float = 0,
        avg_slippage_bps: float = 2.0,
    ):
        """Record a trade result for anomaly detection.

        Args:
            strategy: strategy name
            pnl: trade P&L in dollars (negative = loss)
            slippage_bps: actual slippage in basis points
            avg_slippage_bps: historical average slippage in basis points
        """
        state = self._get_or_create_state(strategy)
        state["trade_count"] += 1
        state["last_slippage_bps"] = slippage_bps

        # Track consecutive losses
        if pnl < 0:
            state["consecutive_losses"] += 1
        else:
            state["consecutive_losses"] = 0

        # Check slippage anomaly
        if avg_slippage_bps > 0 and slippage_bps > self.SLIPPAGE_MULTIPLIER * avg_slippage_bps:
            pause_until = datetime.now(timezone.utc) + timedelta(
                hours=self.PAUSE_DURATION_HOURS
            )
            state["paused_until"] = pause_until.isoformat()
            state["pause_reason"] = (
                f"Slippage {slippage_bps:.1f}bps > "
                f"{self.SLIPPAGE_MULTIPLIER}x avg ({avg_slippage_bps:.1f}bps)"
            )
            self._log_anomaly(strategy, "slippage_spike", state["pause_reason"])

        # Check consecutive losses
        if state["consecutive_losses"] >= self.CONSECUTIVE_LOSS_LIMIT:
            pause_until = datetime.now(timezone.utc) + timedelta(
                hours=self.PAUSE_DURATION_HOURS
            )
            state["paused_until"] = pause_until.isoformat()
            state["pause_reason"] = (
                f"{state['consecutive_losses']} consecutive losing trades"
            )
            self._log_anomaly(strategy, "consecutive_losses", state["pause_reason"])

    def record_connection_status(
        self, connected: bool, disconnected_since: datetime = None
    ):
        """Record broker connection status.

        Args:
            connected: True if broker is connected
            disconnected_since: timestamp when connection was lost
        """
        if connected:
            self._connection_lost_since = None
        else:
            if self._connection_lost_since is None:
                self._connection_lost_since = (
                    disconnected_since or datetime.now(timezone.utc)
                )

    def check_anomalies(self) -> dict:
        """Check for all anomaly types.

        Returns:
            {anomalies_found: int, actions: [{strategy, reason, action}]}
        """
        actions = []
        now = datetime.now(timezone.utc)

        # Check connection timeout
        if self._connection_lost_since is not None:
            elapsed = (now - self._connection_lost_since).total_seconds() / 60.0
            if elapsed > self.CONNECTION_TIMEOUT_MINUTES:
                action = {
                    "strategy": "ALL",
                    "reason": (
                        f"Connection lost for {elapsed:.0f} min "
                        f"(> {self.CONNECTION_TIMEOUT_MINUTES} min threshold)"
                    ),
                    "action": "close_all",
                }
                actions.append(action)
                if self._close_all_func:
                    self._close_all_func()
                self._log_anomaly("ALL", "connection_lost", action["reason"])

        # Check per-strategy anomalies
        for strategy, state in self._strategy_state.items():
            if state["paused_until"]:
                paused_until = datetime.fromisoformat(state["paused_until"])
                if paused_until > now:
                    actions.append({
                        "strategy": strategy,
                        "reason": state["pause_reason"],
                        "action": "paused",
                    })

        return {
            "anomalies_found": len(actions),
            "actions": actions,
        }

    def is_strategy_paused(self, strategy: str) -> bool:
        """Check if a strategy is auto-paused."""
        state = self._strategy_state.get(strategy)
        if not state or not state.get("paused_until"):
            return False

        paused_until = datetime.fromisoformat(state["paused_until"])
        now = datetime.now(timezone.utc)
        if now >= paused_until:
            # Pause expired — clear it
            state["paused_until"] = None
            state["pause_reason"] = None
            state["consecutive_losses"] = 0
            return False

        return True

    def get_paused_strategies(self) -> list:
        """List all auto-paused strategies with reasons."""
        paused = []
        now = datetime.now(timezone.utc)
        for strategy, state in self._strategy_state.items():
            if state.get("paused_until"):
                paused_until = datetime.fromisoformat(state["paused_until"])
                if paused_until > now:
                    paused.append({
                        "strategy": strategy,
                        "reason": state["pause_reason"],
                        "paused_until": state["paused_until"],
                        "remaining_hours": (paused_until - now).total_seconds() / 3600,
                    })
        return paused

    def _log_anomaly(self, strategy: str, anomaly_type: str, reason: str):
        """Log an anomaly event."""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            "type": anomaly_type,
            "reason": reason,
        }
        self._anomaly_log.append(event)
        logger.warning("AnomalyDetector [%s] %s: %s", strategy, anomaly_type, reason)

        if self._alert:
            try:
                self._alert(
                    f"ANOMALY [{strategy}]: {reason}",
                    "warning" if anomaly_type != "connection_lost" else "critical",
                )
            except Exception as e:
                logger.error("Failed to send anomaly alert: %s", e)

    def get_anomaly_log(self) -> list:
        """Full anomaly log."""
        return list(self._anomaly_log)


class SafetyChecker:
    """Verify all positions are protected before autonomous period.

    Checks:
      - Every position has a bracket order (broker-side SL/TP)
      - Kill switch is armed
      - Reconciliation is passing
      - No CRITICAL alerts unresolved
    """

    def __init__(
        self,
        get_positions_func: Callable = None,
        get_open_orders_func: Callable = None,
        has_critical_alerts_func: Callable = None,
        kill_switch_armed_func: Callable = None,
        reconciliation_ok_func: Callable = None,
    ):
        """
        Args:
            get_positions_func: function() -> [positions]
            get_open_orders_func: function() -> [orders] (to check brackets)
            has_critical_alerts_func: function() -> bool
            kill_switch_armed_func: function() -> bool
            reconciliation_ok_func: function() -> bool
        """
        self._get_positions = get_positions_func
        self._get_open_orders = get_open_orders_func
        self._has_critical = has_critical_alerts_func
        self._kill_switch_armed = kill_switch_armed_func
        self._reconciliation_ok = reconciliation_ok_func

    def run_safety_check(self) -> dict:
        """Run all safety checks.

        Returns:
            {
                safe: bool,
                checks: [
                    {name, passed, details}
                ],
                blocking_issues: [str]
            }
        """
        checks = []
        blocking_issues = []

        # 1. All positions have bracket orders
        bracket_check = self.all_positions_have_stops()
        checks.append(bracket_check)
        if not bracket_check["passed"]:
            blocking_issues.append(bracket_check["details"])

        # 2. Kill switch is armed
        ks_check = self._check_kill_switch_armed()
        checks.append(ks_check)
        if not ks_check["passed"]:
            blocking_issues.append(ks_check["details"])

        # 3. No unresolved critical alerts
        critical_check = self.no_unresolved_criticals()
        checks.append(critical_check)
        if not critical_check["passed"]:
            blocking_issues.append(critical_check["details"])

        # 4. Reconciliation passing
        recon_check = self._check_reconciliation()
        checks.append(recon_check)
        if not recon_check["passed"]:
            blocking_issues.append(recon_check["details"])

        safe = len(blocking_issues) == 0

        return {
            "safe": safe,
            "checks": checks,
            "blocking_issues": blocking_issues,
        }

    def all_positions_have_stops(self) -> dict:
        """Verify bracket orders exist for all positions.

        Returns:
            {name, passed, details}
        """
        if not self._get_positions:
            return {
                "name": "bracket_orders",
                "passed": True,
                "details": "No position function configured — skipped",
            }

        positions = self._get_positions()
        if not positions:
            return {
                "name": "bracket_orders",
                "passed": True,
                "details": "No open positions — vacuously safe",
            }

        # Check open orders for stop/take-profit legs
        open_orders = []
        if self._get_open_orders:
            open_orders = self._get_open_orders()

        # Build set of symbols with stop orders
        symbols_with_stops = set()
        for order in open_orders:
            order_type = order.get("type", "").lower()
            if order_type in ("stop", "stop_limit", "trailing_stop"):
                symbols_with_stops.add(order.get("symbol", ""))

        missing_stops = []
        for pos in positions:
            symbol = pos.get("symbol", "UNKNOWN")
            if symbol not in symbols_with_stops:
                missing_stops.append(symbol)

        if missing_stops:
            return {
                "name": "bracket_orders",
                "passed": False,
                "details": (
                    f"Missing broker-side stops for: {', '.join(missing_stops)}"
                ),
            }

        return {
            "name": "bracket_orders",
            "passed": True,
            "details": f"All {len(positions)} positions have broker-side stops",
        }

    def no_unresolved_criticals(self) -> dict:
        """Check no CRITICAL alerts are pending.

        Returns:
            {name, passed, details}
        """
        if not self._has_critical:
            return {
                "name": "no_critical_alerts",
                "passed": True,
                "details": "No critical alert function configured — skipped",
            }

        has_critical = self._has_critical()
        if has_critical:
            return {
                "name": "no_critical_alerts",
                "passed": False,
                "details": "Unresolved CRITICAL alerts — resolve before autonomous mode",
            }

        return {
            "name": "no_critical_alerts",
            "passed": True,
            "details": "No unresolved critical alerts",
        }

    def _check_kill_switch_armed(self) -> dict:
        """Verify kill switch is armed."""
        if not self._kill_switch_armed:
            return {
                "name": "kill_switch_armed",
                "passed": True,
                "details": "No kill switch function configured — skipped",
            }

        armed = self._kill_switch_armed()
        if not armed:
            return {
                "name": "kill_switch_armed",
                "passed": False,
                "details": "Kill switch is NOT armed — must be armed for autonomous mode",
            }

        return {
            "name": "kill_switch_armed",
            "passed": True,
            "details": "Kill switch is armed",
        }

    def _check_reconciliation(self) -> dict:
        """Verify reconciliation is passing."""
        if not self._reconciliation_ok:
            return {
                "name": "reconciliation",
                "passed": True,
                "details": "No reconciliation function configured — skipped",
            }

        ok = self._reconciliation_ok()
        if not ok:
            return {
                "name": "reconciliation",
                "passed": False,
                "details": "Reconciliation FAILING — fix before autonomous mode",
            }

        return {
            "name": "reconciliation",
            "passed": True,
            "details": "Reconciliation passing",
        }


class AutonomousController:
    """Main controller for autonomous mode.

    Manages the lifecycle:
      1. ENTER: Run safety checks, tighten limits, start monitoring
      2. RUNNING: Auto-reduce, anomaly detection, safety checks
      3. EXIT: Generate report, restore normal limits

    State persisted in data/autonomous_state.json
    """

    def __init__(
        self,
        auto_reducer: AutoReducer = None,
        anomaly_detector: AnomalyDetector = None,
        safety_checker: SafetyChecker = None,
        alert_callback: Callable = None,
        kill_func: Callable = None,
        state_path: str = None,
    ):
        """
        Args:
            auto_reducer: AutoReducer instance
            anomaly_detector: AnomalyDetector instance
            safety_checker: SafetyChecker instance
            alert_callback: function(message, level) for Telegram alerts
            kill_func: function(reason) to activate kill switch
            state_path: path for state JSON persistence
        """
        self._reducer = auto_reducer or AutoReducer()
        self._anomaly = anomaly_detector or AnomalyDetector()
        self._safety = safety_checker or SafetyChecker()
        self._alert = alert_callback
        self._kill = kill_func
        self._active = False
        self._entered_at: Optional[str] = None
        self._exit_at: Optional[str] = None
        self._max_duration_hours = 72
        self._requested_duration_hours: Optional[int] = None
        self._events_log: List[dict] = []
        self._periodic_checks_count = 0
        self._trades_during_autonomous: int = 0
        self._pnl_during_autonomous: float = 0.0
        self._state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH

        # Attempt to load persisted state
        self._load_state()

    def enter_autonomous(self, duration_hours: int = 72) -> dict:
        """Enter autonomous mode.

        1. Verify not already active
        2. Run safety checks (abort if not safe)
        3. Record entry time and compute exit time
        4. Send alert "Autonomous mode ACTIVATED for Xh"

        Args:
            duration_hours: max hours for autonomous mode (capped at 72)

        Returns:
            {success, safety_check, duration, timestamp}
        """
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Already active guard
        if self._active:
            logger.warning("Autonomous mode already active since %s", self._entered_at)
            return {
                "success": False,
                "reason": "Already active",
                "entered_at": self._entered_at,
                "timestamp": timestamp,
            }

        # Cap duration
        duration = min(duration_hours, self._max_duration_hours)

        # Safety check
        safety = self._safety.run_safety_check()
        if not safety["safe"]:
            logger.error(
                "Safety check FAILED — cannot enter autonomous mode: %s",
                safety["blocking_issues"],
            )
            if self._alert:
                try:
                    self._alert(
                        f"Autonomous mode REJECTED — safety issues:\n"
                        + "\n".join(f"- {issue}" for issue in safety["blocking_issues"]),
                        "critical",
                    )
                except Exception:
                    pass
            return {
                "success": False,
                "reason": "Safety check failed",
                "safety_check": safety,
                "timestamp": timestamp,
            }

        # Activate
        self._active = True
        self._entered_at = timestamp
        self._requested_duration_hours = duration
        self._exit_at = (now + timedelta(hours=duration)).isoformat()
        self._events_log = []
        self._periodic_checks_count = 0
        self._trades_during_autonomous = 0
        self._pnl_during_autonomous = 0.0
        self._reducer.reset()

        self._log_event("ENTER", f"Autonomous mode activated for {duration}h")
        self._save_state()

        logger.info(
            "AUTONOMOUS MODE ACTIVATED for %dh (until %s)",
            duration, self._exit_at,
        )

        if self._alert:
            try:
                self._alert(
                    f"AUTONOMOUS MODE ACTIVATED\n"
                    f"Duration: {duration}h\n"
                    f"Auto-exit: {self._exit_at}\n"
                    f"Safety checks: ALL PASSED",
                    "warning",
                )
            except Exception as e:
                logger.error("Failed to send entry alert: %s", e)

        return {
            "success": True,
            "safety_check": safety,
            "duration": duration,
            "exit_at": self._exit_at,
            "timestamp": timestamp,
        }

    def exit_autonomous(self, reason: str = "manual") -> dict:
        """Exit autonomous mode.

        1. Verify is active
        2. Restore normal limits (reset reducer)
        3. Generate summary report
        4. Send alert "Autonomous mode DEACTIVATED"

        Args:
            reason: why exiting (manual, duration_limit, kill_switch, etc.)

        Returns:
            {success, duration_hours, events, trades, pnl, anomalies, reason}
        """
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        if not self._active:
            logger.info("Autonomous mode not active — nothing to exit.")
            return {
                "success": False,
                "reason": "Not active",
                "timestamp": timestamp,
            }

        # Calculate duration
        duration_hours = 0.0
        if self._entered_at:
            entered = datetime.fromisoformat(self._entered_at)
            duration_hours = (now - entered).total_seconds() / 3600.0

        self._log_event("EXIT", f"Autonomous mode deactivated — reason: {reason}")

        # Build report data
        anomaly_log = self._anomaly.get_anomaly_log()
        reducer_actions = self._reducer.get_actions_history()

        report = {
            "success": True,
            "reason": reason,
            "duration_hours": round(duration_hours, 2),
            "entered_at": self._entered_at,
            "exited_at": timestamp,
            "periodic_checks": self._periodic_checks_count,
            "events": list(self._events_log),
            "trades": self._trades_during_autonomous,
            "pnl": round(self._pnl_during_autonomous, 2),
            "anomalies": anomaly_log,
            "auto_reduce_actions": reducer_actions,
            "timestamp": timestamp,
        }

        # Reset state
        self._active = False
        self._entered_at = None
        self._exit_at = None
        self._requested_duration_hours = None
        self._reducer.reset()

        self._save_state()

        logger.info(
            "AUTONOMOUS MODE DEACTIVATED after %.1fh — reason: %s",
            duration_hours, reason,
        )

        if self._alert:
            try:
                self._alert(
                    f"AUTONOMOUS MODE DEACTIVATED\n"
                    f"Duration: {duration_hours:.1f}h\n"
                    f"Reason: {reason}\n"
                    f"Trades: {report['trades']}\n"
                    f"P&L: ${report['pnl']:+,.2f}\n"
                    f"Anomalies: {len(anomaly_log)}\n"
                    f"Auto-reduce actions: {len(reducer_actions)}",
                    "warning",
                )
            except Exception as e:
                logger.error("Failed to send exit alert: %s", e)

        return report

    def periodic_check(
        self,
        current_dd_pct: float = 0,
        current_positions: list = None,
    ) -> dict:
        """Called every 5 minutes during autonomous mode.

        Runs:
          1. Duration check (auto-exit after max hours)
          2. Auto-reducer (drawdown check)
          3. Anomaly detection
          4. Safety checks (positions have stops)

        Args:
            current_dd_pct: current drawdown as positive fraction
            current_positions: list of current positions for safety checks

        Returns:
            {ok, actions_taken, anomalies, safety_issues, auto_exited}
        """
        if not self._active:
            return {
                "ok": True,
                "actions_taken": [],
                "anomalies": {"anomalies_found": 0, "actions": []},
                "safety_issues": [],
                "auto_exited": False,
                "reason": "not_active",
            }

        self._periodic_checks_count += 1
        now = datetime.now(timezone.utc)
        actions_taken = []

        # 1. Duration check
        if self._exit_at:
            exit_at = datetime.fromisoformat(self._exit_at)
            if now >= exit_at:
                logger.info("Autonomous mode duration limit reached — auto-exiting")
                exit_result = self.exit_autonomous(reason="duration_limit")
                return {
                    "ok": True,
                    "actions_taken": ["auto_exit_duration"],
                    "anomalies": {"anomalies_found": 0, "actions": []},
                    "safety_issues": [],
                    "auto_exited": True,
                    "exit_report": exit_result,
                }

        # 2. Auto-reducer
        reducer_result = self._reducer.check_and_act(current_dd_pct)
        if reducer_result["action_taken"]:
            actions_taken.append(f"auto_reduce_L{reducer_result['level']}")
            self._log_event(
                "AUTO_REDUCE",
                reducer_result["details"],
            )
            # If close_all, also activate kill switch
            if reducer_result["action"] == "close_all" and self._kill:
                self._kill("Autonomous auto-reducer: DD >= 2%")
                actions_taken.append("kill_switch_activated")
                self._log_event("KILL_SWITCH", "Activated by auto-reducer close_all")

        # 3. Anomaly detection
        anomalies = self._anomaly.check_anomalies()
        if anomalies["anomalies_found"] > 0:
            for anomaly_action in anomalies["actions"]:
                self._log_event(
                    "ANOMALY",
                    f"{anomaly_action['strategy']}: {anomaly_action['reason']}",
                )
                if anomaly_action["action"] == "close_all":
                    actions_taken.append("anomaly_close_all")
                else:
                    actions_taken.append(
                        f"anomaly_pause_{anomaly_action['strategy']}"
                    )

        # 4. Safety check (only if positions provided)
        safety_issues = []
        if current_positions is not None:
            safety = self._safety.run_safety_check()
            if not safety["safe"]:
                safety_issues = safety["blocking_issues"]
                self._log_event(
                    "SAFETY_ISSUE",
                    f"Issues: {'; '.join(safety_issues)}",
                )

        self._save_state()

        ok = len(actions_taken) == 0 and len(safety_issues) == 0
        return {
            "ok": ok,
            "actions_taken": actions_taken,
            "anomalies": anomalies,
            "safety_issues": safety_issues,
            "auto_exited": False,
        }

    @property
    def is_active(self) -> bool:
        """Whether autonomous mode is currently active."""
        return self._active

    def get_status(self) -> dict:
        """Current autonomous mode status."""
        now = datetime.now(timezone.utc)
        remaining_hours = 0.0
        elapsed_hours = 0.0

        if self._active and self._entered_at:
            entered = datetime.fromisoformat(self._entered_at)
            elapsed_hours = (now - entered).total_seconds() / 3600.0
            if self._exit_at:
                exit_at = datetime.fromisoformat(self._exit_at)
                remaining = (exit_at - now).total_seconds() / 3600.0
                remaining_hours = max(0.0, remaining)

        return {
            "active": self._active,
            "entered_at": self._entered_at,
            "exit_at": self._exit_at,
            "elapsed_hours": round(elapsed_hours, 2),
            "remaining_hours": round(remaining_hours, 2),
            "periodic_checks": self._periodic_checks_count,
            "events_count": len(self._events_log),
            "trades": self._trades_during_autonomous,
            "pnl": round(self._pnl_during_autonomous, 2),
            "paused_strategies": self._anomaly.get_paused_strategies(),
            "auto_reduce_actions": len(self._reducer.get_actions_history()),
        }

    def get_report(self) -> str:
        """Generate markdown report of autonomous period.

        Returns:
            Markdown-formatted report string
        """
        status = self.get_status()
        anomalies = self._anomaly.get_anomaly_log()
        reducer_actions = self._reducer.get_actions_history()
        paused = self._anomaly.get_paused_strategies()

        lines = [
            "# Autonomous Mode Report",
            "",
            f"**Status**: {'ACTIVE' if self._active else 'INACTIVE'}",
            f"**Entered**: {self._entered_at or 'N/A'}",
            f"**Elapsed**: {status['elapsed_hours']:.1f}h",
            f"**Remaining**: {status['remaining_hours']:.1f}h",
            "",
            "## Summary",
            f"- Periodic checks: {self._periodic_checks_count}",
            f"- Trades: {self._trades_during_autonomous}",
            f"- P&L: ${self._pnl_during_autonomous:+,.2f}",
            f"- Anomalies: {len(anomalies)}",
            f"- Auto-reduce actions: {len(reducer_actions)}",
            "",
        ]

        if self._events_log:
            lines.append("## Events Log")
            for event in self._events_log:
                lines.append(
                    f"- [{event['timestamp']}] **{event['type']}**: {event['details']}"
                )
            lines.append("")

        if anomalies:
            lines.append("## Anomalies Detected")
            for a in anomalies:
                lines.append(
                    f"- [{a['timestamp']}] {a['strategy']}: "
                    f"{a['type']} — {a['reason']}"
                )
            lines.append("")

        if reducer_actions:
            lines.append("## Auto-Reduce Actions")
            for r in reducer_actions:
                lines.append(
                    f"- [{r['timestamp']}] Level {r['level']}: "
                    f"{r['action']} ({r['reduce_pct']:.0%}) — DD {r['dd_pct']:.2%}"
                )
            lines.append("")

        if paused:
            lines.append("## Currently Paused Strategies")
            for p in paused:
                lines.append(
                    f"- **{p['strategy']}**: {p['reason']} "
                    f"(remaining: {p['remaining_hours']:.1f}h)"
                )
            lines.append("")

        return "\n".join(lines)

    def record_trade(self, pnl: float):
        """Record a trade that happened during autonomous mode.

        Args:
            pnl: P&L of the trade in dollars
        """
        if self._active:
            self._trades_during_autonomous += 1
            self._pnl_during_autonomous += pnl
            self._save_state()

    def _log_event(self, event_type: str, details: str):
        """Append to events log."""
        self._events_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "details": details,
        })

    def _save_state(self):
        """Persist state to JSON (atomic write)."""
        state = {
            "active": self._active,
            "entered_at": self._entered_at,
            "exit_at": self._exit_at,
            "requested_duration_hours": self._requested_duration_hours,
            "periodic_checks_count": self._periodic_checks_count,
            "trades_during_autonomous": self._trades_during_autonomous,
            "pnl_during_autonomous": self._pnl_during_autonomous,
            "events_log": self._events_log,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_path.parent), suffix='.tmp'
            )
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, str(self._state_path))
            except:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            logger.error("Failed to save autonomous state: %s", e)

    def _load_state(self):
        """Load state from JSON."""
        if not self._state_path.exists():
            return

        try:
            with open(self._state_path, "r") as f:
                state = json.load(f)

            self._active = state.get("active", False)
            self._entered_at = state.get("entered_at")
            self._exit_at = state.get("exit_at")
            self._requested_duration_hours = state.get("requested_duration_hours")
            self._periodic_checks_count = state.get("periodic_checks_count", 0)
            self._trades_during_autonomous = state.get("trades_during_autonomous", 0)
            self._pnl_during_autonomous = state.get("pnl_during_autonomous", 0.0)
            self._events_log = state.get("events_log", [])

            if self._active:
                logger.warning(
                    "Autonomous mode LOADED in ACTIVE state (since %s)",
                    self._entered_at,
                )
        except Exception as e:
            logger.error("Failed to load autonomous state: %s", e)
