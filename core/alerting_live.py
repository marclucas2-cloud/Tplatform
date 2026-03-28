"""
Live Trading Alert System — enhanced alerting for real money.

3 alert levels:
  INFO (Telegram, no sound):
    - Trade opened/closed
    - Daily report

  WARNING (Telegram with sound):
    - Slippage > 2x average
    - Margin > 70%
    - Drawdown > 1% daily
    - Strategy signal skipped (risk filter)

  CRITICAL (Telegram + backup channel):
    - Kill switch activated
    - Broker disconnected
    - Reconciliation mismatch
    - Drawdown > 2% daily
    - Margin > 85%
    - Worker crash

All live alerts are prefixed with [LIVE] to distinguish from paper.
Throttling: max 1 alert per type per 5 minutes (prevent spam).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional, Callable, List, Dict

logger = logging.getLogger(__name__)

# Alert level constants
INFO = "INFO"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

# Emojis per level
_LEVEL_EMOJI = {
    INFO: "\u2139\ufe0f",         # ℹ️
    WARNING: "\u26a0\ufe0f",      # ⚠️
    CRITICAL: "\U0001f6a8",       # 🚨
}


class LiveAlertManager:
    """Enhanced alert system for live trading.

    Features:
    - 3 levels: INFO, WARNING, CRITICAL
    - [LIVE] prefix on all messages
    - Throttling per alert type (5 min default)
    - Alert history for audit
    - Backup alert channel for CRITICAL
    - Daily summary of all alerts
    """

    def __init__(
        self,
        mode: str = "LIVE",
        throttle_seconds: int = 300,
        send_func: Optional[Callable[[str], bool]] = None,
        backup_send_func: Optional[Callable[[str], bool]] = None,
    ):
        """
        Args:
            mode: "LIVE" or "PAPER" (changes prefix)
            throttle_seconds: minimum seconds between same-type alerts
            send_func: function(text) -> bool for primary channel
            backup_send_func: function(text) -> bool for backup (SMS/Pushover)
        """
        self.mode = mode
        self.throttle_seconds = throttle_seconds
        self._send = send_func
        self._backup_send = backup_send_func
        self._last_alert_times: Dict[str, float] = {}
        self._alert_history: List[Dict] = []
        self._unresolved_criticals: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_send_func(self) -> Callable[[str], bool]:
        """Return primary send function, lazy-importing telegram_alert as fallback."""
        if self._send is not None:
            return self._send
        try:
            from core.telegram_alert import _send_message
            return _send_message
        except ImportError:
            logger.warning("telegram_alert not available, alerts will be logged only")
            return lambda text: False

    def _should_throttle(self, alert_type: str) -> bool:
        """Check if this alert type was sent recently."""
        last_time = self._last_alert_times.get(alert_type)
        if last_time is None:
            return False
        return (time.monotonic() - last_time) < self.throttle_seconds

    def _record_alert(self, level: str, alert_type: str, message: str) -> None:
        """Record alert in history and update throttle timestamp."""
        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "level": level,
            "type": alert_type,
            "message": message,
        }
        self._alert_history.append(entry)
        self._last_alert_times[alert_type] = time.monotonic()

        if level == CRITICAL:
            self._unresolved_criticals[alert_type] = entry

        logger.info("Alert [%s] %s: %s", level, alert_type, message[:120])

    def _format_message(self, level: str, message: str) -> str:
        """Format with [LIVE]/[PAPER] prefix and emoji."""
        emoji = _LEVEL_EMOJI.get(level, "")
        prefix = f"[{self.mode}]"
        return f"{emoji} <b>{prefix} {level}</b>\n\n{message}"

    def _dispatch(self, level: str, alert_type: str, message: str) -> bool:
        """Format, throttle, record and send an alert. Returns True if sent."""
        # Skip throttle for CRITICAL alerts — never silence critical alerts
        if level != CRITICAL and self._should_throttle(alert_type):
            logger.debug("Throttled alert %s", alert_type)
            return False

        formatted = self._format_message(level, message)
        self._record_alert(level, alert_type, message)

        send_fn = self._get_send_func()
        sent = send_fn(formatted)

        # CRITICAL -> also send via backup channel
        if level == CRITICAL and self._backup_send is not None:
            try:
                self._backup_send(formatted)
            except Exception as exc:
                logger.error("Backup send failed: %s", exc)

        return sent

    # ==================================================================
    # INFO alerts
    # ==================================================================

    def trade_opened(
        self,
        strategy: str,
        instrument: str,
        direction: str,
        quantity: float,
        price: float,
        stop_loss: float = None,
        take_profit: float = None,
        instrument_type: str = "EQUITY",
    ) -> bool:
        """Alert: new trade opened."""
        dir_emoji = "\U0001f7e2" if direction.upper() in ("LONG", "BUY") else "\U0001f534"
        sl_line = f"\nSL: ${stop_loss:.2f}" if stop_loss is not None else ""
        tp_line = f"\nTP: ${take_profit:.2f}" if take_profit is not None else ""
        msg = (
            f"{dir_emoji} <b>Trade Opened</b>\n"
            f"Strategy: {strategy}\n"
            f"Instrument: {instrument} ({instrument_type})\n"
            f"Direction: {direction}\n"
            f"Qty: {quantity} @ ${price:.2f}"
            f"{sl_line}{tp_line}"
        )
        return self._dispatch(INFO, "trade_opened", msg)

    def trade_closed(
        self,
        strategy: str,
        instrument: str,
        direction: str,
        pnl: float,
        pnl_pct: float,
        exit_reason: str,
        holding_time: str = "",
    ) -> bool:
        """Alert: trade closed with P&L."""
        pnl_emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
        hold_line = f"\nHolding time: {holding_time}" if holding_time else ""
        msg = (
            f"{pnl_emoji} <b>Trade Closed</b>\n"
            f"Strategy: {strategy}\n"
            f"Instrument: {instrument} ({direction})\n"
            f"P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"Exit reason: {exit_reason}"
            f"{hold_line}"
        )
        return self._dispatch(INFO, "trade_closed", msg)

    def daily_report(
        self,
        trades_today: int,
        pnl_today: float,
        pnl_mtd: float,
        positions_open: int,
        margin_used_pct: float,
        strategies_active: int,
    ) -> bool:
        """Daily summary report (18h CET)."""
        pnl_emoji = "\U0001f4c8" if pnl_today >= 0 else "\U0001f4c9"
        msg = (
            f"\U0001f4cb <b>Daily Report</b>\n\n"
            f"Trades today: {trades_today}\n"
            f"P&L today: {pnl_emoji} ${pnl_today:+,.2f}\n"
            f"P&L MTD: ${pnl_mtd:+,.2f}\n"
            f"Open positions: {positions_open}\n"
            f"Margin used: {margin_used_pct:.1f}%\n"
            f"Active strategies: {strategies_active}"
        )
        return self._dispatch(INFO, "daily_report", msg)

    # ==================================================================
    # WARNING alerts
    # ==================================================================

    def slippage_warning(
        self,
        strategy: str,
        instrument: str,
        slippage_bps: float,
        avg_bps: float,
    ) -> bool:
        """Slippage > 2x average."""
        if avg_bps > 0:
            ratio = slippage_bps / avg_bps
        else:
            ratio = 0
        msg = (
            f"<b>Slippage Warning</b>\n\n"
            f"Strategy: {strategy}\n"
            f"Instrument: {instrument}\n"
            f"Slippage: {slippage_bps:.1f} bps (avg: {avg_bps:.1f} bps)\n"
            f"Ratio: {ratio:.1f}x average"
        )
        return self._dispatch(WARNING, "slippage_warning", msg)

    def margin_warning(self, margin_pct: float, threshold: float = 0.70) -> bool:
        """Margin utilization approaching limit."""
        msg = (
            f"<b>Margin Warning</b>\n\n"
            f"Margin used: {margin_pct * 100:.1f}%\n"
            f"Threshold: {threshold * 100:.0f}%\n"
            f"Reduce exposure or add capital."
        )
        return self._dispatch(WARNING, "margin_warning", msg)

    def drawdown_warning(self, dd_pct: float, dd_amount: float) -> bool:
        """Daily drawdown > 1%."""
        msg = (
            f"<b>Drawdown Warning</b>\n\n"
            f"Daily drawdown: {dd_pct:.2f}%\n"
            f"Amount: ${dd_amount:,.2f}\n"
            f"Monitor closely. Circuit-breaker at 2%."
        )
        return self._dispatch(WARNING, "drawdown_warning", msg)

    def signal_skipped(self, strategy: str, instrument: str, reason: str) -> bool:
        """Signal skipped due to risk filter."""
        msg = (
            f"<b>Signal Skipped</b>\n\n"
            f"Strategy: {strategy}\n"
            f"Instrument: {instrument}\n"
            f"Reason: {reason}"
        )
        return self._dispatch(WARNING, "signal_skipped", msg)

    def strategy_paused(self, strategy: str, reason: str) -> bool:
        """Strategy auto-paused."""
        msg = (
            f"<b>Strategy Paused</b>\n\n"
            f"Strategy: {strategy}\n"
            f"Reason: {reason}"
        )
        return self._dispatch(WARNING, "strategy_paused", msg)

    # ==================================================================
    # CRITICAL alerts
    # ==================================================================

    def kill_switch_activated(
        self, reason: str, positions_closed: int, pnl_at_close: float
    ) -> bool:
        """Kill switch triggered — all positions closed."""
        msg = (
            f"\U0001f6a8\U0001f6a8\U0001f6a8 <b>KILL SWITCH ACTIVATED</b>\n\n"
            f"Reason: {reason}\n"
            f"Positions closed: {positions_closed}\n"
            f"P&L at close: ${pnl_at_close:+,.2f}\n\n"
            f"<b>ALL TRADING HALTED</b>"
        )
        return self._dispatch(CRITICAL, "kill_switch", msg)

    def broker_disconnected(self, broker: str, duration_seconds: int = 0) -> bool:
        """Broker connection lost."""
        dur_str = f"{duration_seconds}s" if duration_seconds else "just now"
        msg = (
            f"<b>Broker Disconnected</b>\n\n"
            f"Broker: {broker}\n"
            f"Duration: {dur_str}\n\n"
            f"<b>Attempting reconnection...</b>"
        )
        return self._dispatch(CRITICAL, "broker_disconnected", msg)

    def reconciliation_mismatch(self, divergences: list) -> bool:
        """Position mismatch between model and broker."""
        lines = []
        for d in divergences[:10]:  # cap display at 10
            lines.append(f"  - {d}")
        div_text = "\n".join(lines) if lines else "  (no details)"
        msg = (
            f"<b>Reconciliation Mismatch</b>\n\n"
            f"Divergences ({len(divergences)}):\n"
            f"{div_text}\n\n"
            f"<b>Manual review required.</b>"
        )
        return self._dispatch(CRITICAL, "reconciliation_mismatch", msg)

    def drawdown_critical(self, dd_pct: float, dd_amount: float) -> bool:
        """Daily drawdown > 2% — circuit breaker."""
        msg = (
            f"\U0001f6a8\U0001f6a8\U0001f6a8 <b>CIRCUIT BREAKER — DRAWDOWN</b>\n\n"
            f"Daily drawdown: {dd_pct:.2f}%\n"
            f"Amount: ${dd_amount:,.2f}\n\n"
            f"<b>ALL NEW ORDERS BLOCKED</b>"
        )
        return self._dispatch(CRITICAL, "drawdown_critical", msg)

    def margin_critical(self, margin_pct: float) -> bool:
        """Margin > 85% — new trades blocked."""
        msg = (
            f"<b>MARGIN CRITICAL</b>\n\n"
            f"Margin used: {margin_pct * 100:.1f}%\n"
            f"Threshold: 85%\n\n"
            f"<b>NEW TRADES BLOCKED. Reduce positions immediately.</b>"
        )
        return self._dispatch(CRITICAL, "margin_critical", msg)

    def worker_crash(self, error: str) -> bool:
        """Worker process crashed."""
        # Truncate long error messages
        error_truncated = error[:500] if len(error) > 500 else error
        msg = (
            f"<b>WORKER CRASH</b>\n\n"
            f"Error: {error_truncated}\n\n"
            f"<b>Worker needs manual restart.</b>"
        )
        return self._dispatch(CRITICAL, "worker_crash", msg)

    # ==================================================================
    # Trade-level alerts
    # ==================================================================

    def trade_loss_alert(self, strategy: str, instrument: str, pnl: float,
                         capital: float, threshold_warning: float = 50.0,
                         threshold_critical: float = 100.0):
        """Alert on significant single-trade losses.

        Args:
            strategy: strategy name
            instrument: symbol/pair
            pnl: trade P&L in dollars (negative = loss)
            capital: current capital
            threshold_warning: dollar loss for WARNING (default $50)
            threshold_critical: dollar loss for CRITICAL (default $100)
        """
        if pnl >= 0:
            return  # No alert on profits

        loss = abs(pnl)
        loss_pct = loss / capital * 100 if capital > 0 else 0

        if loss >= threshold_critical:
            self._dispatch(
                CRITICAL,
                "trade_loss",
                f"TRADE LOSS CRITICAL: {strategy} on {instrument} "
                f"lost ${loss:.0f} ({loss_pct:.1f}% of capital)",
            )
        elif loss >= threshold_warning:
            self._dispatch(
                WARNING,
                "trade_loss",
                f"TRADE LOSS WARNING: {strategy} on {instrument} "
                f"lost ${loss:.0f} ({loss_pct:.1f}% of capital)",
            )

    def strategy_stale_alert(self, strategy: str, days_since_last_signal: int,
                             threshold_warning: int = 7, threshold_critical: int = 30):
        """Alert when a strategy hasn't generated signals for too long.

        Args:
            strategy: strategy name
            days_since_last_signal: days since last signal
            threshold_warning: days for WARNING (default 7)
            threshold_critical: days for CRITICAL (default 30)
        """
        if days_since_last_signal >= threshold_critical:
            self._dispatch(
                CRITICAL,
                "strategy_stale",
                f"STRATEGY STALE CRITICAL: {strategy} — no signal for "
                f"{days_since_last_signal} days (threshold: {threshold_critical}d)",
            )
        elif days_since_last_signal >= threshold_warning:
            self._dispatch(
                WARNING,
                "strategy_stale",
                f"STRATEGY STALE WARNING: {strategy} — no signal for "
                f"{days_since_last_signal} days",
            )

    # ==================================================================
    # Utility
    # ==================================================================

    def get_alert_history(
        self, level: str = None, limit: int = 50
    ) -> List[Dict]:
        """Get recent alert history, optionally filtered by level."""
        if level is not None:
            filtered = [a for a in self._alert_history if a["level"] == level]
        else:
            filtered = list(self._alert_history)
        return filtered[-limit:]

    def get_alert_stats(self) -> Dict:
        """Alert statistics: count by level, most frequent types."""
        stats: Dict = {INFO: 0, WARNING: 0, CRITICAL: 0, "by_type": {}}
        for alert in self._alert_history:
            lvl = alert["level"]
            atype = alert["type"]
            stats[lvl] = stats.get(lvl, 0) + 1
            stats["by_type"][atype] = stats["by_type"].get(atype, 0) + 1
        stats["total"] = len(self._alert_history)
        return stats

    def has_unresolved_critical(self) -> bool:
        """Check if any CRITICAL alert is unresolved."""
        return len(self._unresolved_criticals) > 0

    def resolve_critical(self, alert_type: str) -> None:
        """Mark a critical alert as resolved."""
        if alert_type in self._unresolved_criticals:
            del self._unresolved_criticals[alert_type]
            logger.info("Resolved critical alert: %s", alert_type)
