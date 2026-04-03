"""
Telegram Command Handler — control live trading from phone.

Commands:
  /status        — Open positions, P&L, margin, active strategies
  /positions     — Detail of each position (entry, PnL, duration)
  /pnl           — P&L today, MTD, YTD (live only)
  /paper         — P&L paper (separate)
  /margin        — Margin utilization detailed
  /kill          — KILL SWITCH: close ALL live positions (requires CONFIRM)
  /pause [strat] — Pause a strategy (stop signals, keep positions)
  /resume [strat]— Resume a strategy
  /reduce 50%    — Reduce ALL positions by percentage
  /close [ticker]— Close a specific position
  /leverage      — Current leverage by asset class
  /health        — Infrastructure status (broker, Railway, healthcheck)
  /help          — List of commands

Security:
  - Authentication by Telegram chat_id (only Marc can send commands)
  - Destructive commands (/kill, /close, /reduce) require confirmation
  - Rate limiting: max 1 destructive command per minute
  - Every command logged with timestamp

Uses polling (not webhook) to avoid exposing an endpoint.
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Callable, Dict

logger = logging.getLogger(__name__)

# How long a confirmation token stays valid (seconds)
CONFIRMATION_TTL = 60
# Minimum interval between destructive commands (seconds)
DESTRUCTIVE_COOLDOWN = 60

DESTRUCTIVE_COMMANDS = frozenset(("kill", "close", "reduce"))

HELP_TEXT = (
    "<b>Trading Platform Commands</b>\n\n"
    "/status — Open positions, P&L, margin, active strategies\n"
    "/positions — Detail of each position (entry, PnL, duration)\n"
    "/pnl — P&L today, MTD, YTD (live)\n"
    "/paper — P&L paper (separate)\n"
    "/margin — Margin utilization detailed\n"
    "/leverage — Current leverage by asset class\n"
    "/health — Infrastructure status\n"
    "/regime — Current market regime per asset class (V12)\n"
    "/portfolio — Cross-broker NAV, DD, exposure (V12)\n"
    "/pause [strat] — Pause a strategy\n"
    "/resume [strat] — Resume a strategy\n"
    "/close [ticker] — Close a specific position (requires CONFIRM)\n"
    "/reduce [pct%] — Reduce ALL positions by % (requires CONFIRM)\n"
    "/kill — KILL SWITCH: close ALL positions (requires CONFIRM)\n"
    "/emergency [code] — EMERGENCY: close ALL on ALL brokers (V12)\n"
    "/help — This message"
)


class TelegramCommandHandler:
    """Handles incoming Telegram commands for live trading control.

    Usage:
        handler = TelegramCommandHandler(
            authorized_chat_ids=[os.getenv("TELEGRAM_CHAT_ID")],
            get_status_func=lambda: engine.get_full_status(),
            kill_func=lambda: kill_switch.activate("TELEGRAM"),
        )
        handler.start_polling()  # blocks, run in thread
    """

    def __init__(
        self,
        authorized_chat_ids: list = None,
        bot_token: str = None,
        # Callback functions for each action
        get_status_func: Callable = None,
        get_positions_func: Callable = None,
        get_pnl_func: Callable = None,
        get_paper_pnl_func: Callable = None,
        get_margin_func: Callable = None,
        kill_func: Callable = None,
        pause_strategy_func: Callable = None,
        resume_strategy_func: Callable = None,
        reduce_positions_func: Callable = None,
        close_position_func: Callable = None,
        get_leverage_func: Callable = None,
        get_health_func: Callable = None,
        get_regime_func: Callable = None,
        get_portfolio_func: Callable = None,
        emergency_close_func: Callable = None,
    ):
        """All callbacks are optional — returns 'not configured' if missing."""
        self._token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        authorized = set(
            str(cid) for cid in (authorized_chat_ids or [os.getenv("TELEGRAM_CHAT_ID", "")])
        )
        self._authorized = {cid for cid in authorized if cid}  # filter empty strings
        self._callbacks: Dict[str, Callable | None] = {
            "status": get_status_func,
            "positions": get_positions_func,
            "pnl": get_pnl_func,
            "paper": get_paper_pnl_func,
            "margin": get_margin_func,
            "kill": kill_func,
            "pause": pause_strategy_func,
            "resume": resume_strategy_func,
            "reduce": reduce_positions_func,
            "close": close_position_func,
            "leverage": get_leverage_func,
            "health": get_health_func,
            "regime": get_regime_func,
            "portfolio": get_portfolio_func,
            "emergency": emergency_close_func,
        }
        # {chat_id: {"command": str, "args": str, "timestamp": float}}
        self._pending_confirmations: Dict[str, dict] = {}
        # {chat_id: timestamp} for rate limiting
        self._last_destructive: Dict[str, float] = {}
        self._command_log: list = []  # audit trail
        self._running = False
        self._last_update_id = 0

    # ------------------------------------------------------------------
    # Security helpers
    # ------------------------------------------------------------------

    def _is_authorized(self, chat_id: str) -> bool:
        """Check if chat_id is authorized."""
        return str(chat_id) in self._authorized

    def _is_destructive(self, command: str) -> bool:
        """Commands that require confirmation: kill, close, reduce."""
        return command in DESTRUCTIVE_COMMANDS

    def _rate_limit_check(self, chat_id: str) -> bool:
        """Return True if the command is allowed (not rate-limited).

        Max 1 destructive command per DESTRUCTIVE_COOLDOWN seconds.
        """
        last = self._last_destructive.get(str(chat_id))
        if last is None:
            return True
        return (time.time() - last) >= DESTRUCTIVE_COOLDOWN

    def _needs_confirmation(self, command: str, args: str, chat_id: str) -> bool:
        """Return True if a valid pending confirmation exists for this exact command."""
        chat_id = str(chat_id)
        pending = self._pending_confirmations.get(chat_id)
        if pending is None:
            return False
        # Check expiry
        if (time.time() - pending["timestamp"]) > CONFIRMATION_TTL:
            del self._pending_confirmations[chat_id]
            return False
        # Must match exact command + args
        return pending["command"] == command and pending["args"] == args

    def _request_confirmation(self, chat_id: str, command: str, args: str):
        """Store a pending confirmation for a destructive command."""
        chat_id = str(chat_id)
        self._pending_confirmations[chat_id] = {
            "command": command,
            "args": args,
            "timestamp": time.time(),
        }

    def _log_command(self, chat_id: str, text: str, response_preview: str):
        """Record every command in the audit trail."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "chat_id": str(chat_id),
            "text": text,
            "response_preview": response_preview[:120],
        }
        self._command_log.append(entry)
        logger.info("Telegram command: chat=%s text=%s", chat_id, text)

    # ------------------------------------------------------------------
    # Main dispatcher
    # ------------------------------------------------------------------

    def handle_message(self, chat_id: str, text: str) -> str:
        """Process an incoming message and return response text (HTML).

        Returns:
            HTML-formatted response string
        """
        chat_id = str(chat_id)

        # Auth check
        if not self._is_authorized(chat_id):
            logger.warning("Unauthorized Telegram command from chat_id=%s", chat_id)
            return "Unauthorized."

        text = (text or "").strip()
        if not text.startswith("/"):
            return "Unknown command. Type /help for available commands."

        # Parse: "/command args..."
        parts = text.split(None, 1)
        raw_command = parts[0].lower().lstrip("/")
        # Strip bot mention suffix e.g. /status@MyBot
        if "@" in raw_command:
            raw_command = raw_command.split("@")[0]
        args = parts[1].strip() if len(parts) > 1 else ""

        # Route
        handler_map: Dict[str, Callable] = {
            "status": lambda: self._handle_status(),
            "positions": lambda: self._handle_positions(),
            "pnl": lambda: self._handle_pnl(),
            "paper": lambda: self._handle_paper(),
            "margin": lambda: self._handle_margin(),
            "leverage": lambda: self._handle_leverage(),
            "health": lambda: self._handle_health(),
            "kill": lambda: self._handle_kill(chat_id, args),
            "pause": lambda: self._handle_pause(args),
            "resume": lambda: self._handle_resume(args),
            "reduce": lambda: self._handle_reduce(chat_id, args),
            "close": lambda: self._handle_close(chat_id, args),
            "regime": lambda: self._handle_regime(),
            "portfolio": lambda: self._handle_portfolio(),
            "emergency": lambda: self._handle_emergency(chat_id, args),
            "help": lambda: self._handle_help(),
        }

        handler = handler_map.get(raw_command)
        if handler is None:
            response = f"Unknown command: /{raw_command}\nType /help for available commands."
        else:
            try:
                response = handler()
            except Exception as exc:
                logger.exception("Error handling /%s", raw_command)
                response = f"Error executing /{raw_command}: {exc}"

        self._log_command(chat_id, text, response)
        return response

    # ------------------------------------------------------------------
    # Read-only command handlers
    # ------------------------------------------------------------------

    def _call_or_not_configured(self, name: str) -> str:
        """Call callback by name, or return 'not configured'."""
        cb = self._callbacks.get(name)
        if cb is None:
            return f"/{name} is not configured."
        result = cb()
        if isinstance(result, str):
            return result
        # dict / list — pretty-print
        return f"<pre>{json.dumps(result, indent=2, default=str)}</pre>"

    def _handle_status(self) -> str:
        """Format status response."""
        return self._call_or_not_configured("status")

    def _handle_positions(self) -> str:
        """Format positions response."""
        return self._call_or_not_configured("positions")

    def _handle_pnl(self) -> str:
        """Format P&L response."""
        return self._call_or_not_configured("pnl")

    def _handle_paper(self) -> str:
        """Format paper P&L response."""
        return self._call_or_not_configured("paper")

    def _handle_margin(self) -> str:
        """Format margin response."""
        return self._call_or_not_configured("margin")

    def _handle_leverage(self) -> str:
        """Format leverage response."""
        return self._call_or_not_configured("leverage")

    def _handle_health(self) -> str:
        """Format health/infrastructure response."""
        return self._call_or_not_configured("health")

    def _handle_help(self) -> str:
        """Return list of available commands."""
        return HELP_TEXT

    # ------------------------------------------------------------------
    # Destructive command handlers (with confirmation flow)
    # ------------------------------------------------------------------

    def _handle_kill(self, chat_id: str, args: str) -> str:
        """Handle kill switch with confirmation flow.

        Flow:
          1. /kill            -> asks for confirmation
          2. /kill CONFIRM    -> executes (if pending confirmation exists)
        """
        cb = self._callbacks.get("kill")
        if cb is None:
            return "/kill is not configured."

        if args.upper() == "CONFIRM":
            # Check pending confirmation
            if not self._needs_confirmation("kill", "", chat_id):
                return "No pending /kill confirmation. Send /kill first."
            if not self._rate_limit_check(chat_id):
                return "Rate limited. Wait 60s between destructive commands."
            # Execute
            del self._pending_confirmations[str(chat_id)]
            self._last_destructive[str(chat_id)] = time.time()
            try:
                result = cb()
                return f"KILL SWITCH ACTIVATED.\n{result if isinstance(result, str) else 'All positions closed.'}"
            except Exception as exc:
                return f"KILL SWITCH FAILED: {exc}"
        else:
            # Request confirmation
            self._request_confirmation(chat_id, "kill", "")
            return (
                "KILL SWITCH requested.\n"
                "This will close ALL positions immediately.\n\n"
                "Send <b>/kill CONFIRM</b> within 60s to execute."
            )

    def _handle_pause(self, args: str) -> str:
        """Pause a strategy."""
        cb = self._callbacks.get("pause")
        if cb is None:
            return "/pause is not configured."
        if not args:
            return "Usage: /pause &lt;strategy_name&gt;"
        try:
            result = cb(args)
            return result if isinstance(result, str) else f"Strategy '{args}' paused."
        except Exception as exc:
            return f"Error pausing '{args}': {exc}"

    def _handle_resume(self, args: str) -> str:
        """Resume a strategy."""
        cb = self._callbacks.get("resume")
        if cb is None:
            return "/resume is not configured."
        if not args:
            return "Usage: /resume &lt;strategy_name&gt;"
        try:
            result = cb(args)
            return result if isinstance(result, str) else f"Strategy '{args}' resumed."
        except Exception as exc:
            return f"Error resuming '{args}': {exc}"

    def _handle_reduce(self, chat_id: str, args: str) -> str:
        """Reduce all positions by percentage.

        Flow:
          1. /reduce 50%            -> asks for confirmation
          2. /reduce 50% CONFIRM    -> executes
        """
        cb = self._callbacks.get("reduce")
        if cb is None:
            return "/reduce is not configured."

        parts = args.split()
        if not parts:
            return "Usage: /reduce &lt;percentage&gt;%  (e.g. /reduce 50%)"

        pct_str = parts[0].rstrip("%")
        try:
            pct = float(pct_str)
        except ValueError:
            return f"Invalid percentage: {parts[0]}"

        if pct <= 0 or pct > 100:
            return "Percentage must be between 1 and 100."

        is_confirm = len(parts) > 1 and parts[1].upper() == "CONFIRM"
        reduce_args = pct_str  # normalized key for confirmation matching

        if is_confirm:
            if not self._needs_confirmation("reduce", reduce_args, chat_id):
                return f"No pending /reduce {pct_str}% confirmation. Send /reduce {pct_str}% first."
            if not self._rate_limit_check(chat_id):
                return "Rate limited. Wait 60s between destructive commands."
            del self._pending_confirmations[str(chat_id)]
            self._last_destructive[str(chat_id)] = time.time()
            try:
                result = cb(pct)
                return result if isinstance(result, str) else f"All positions reduced by {pct}%."
            except Exception as exc:
                return f"Error reducing positions: {exc}"
        else:
            self._request_confirmation(chat_id, "reduce", reduce_args)
            return (
                f"Reduce ALL positions by <b>{pct}%</b> requested.\n\n"
                f"Send <b>/reduce {pct_str}% CONFIRM</b> within 60s to execute."
            )

    def _handle_close(self, chat_id: str, args: str) -> str:
        """Close a specific position.

        Flow:
          1. /close AAPL            -> asks for confirmation
          2. /close AAPL CONFIRM    -> executes
        """
        cb = self._callbacks.get("close")
        if cb is None:
            return "/close is not configured."

        parts = args.split()
        if not parts:
            return "Usage: /close &lt;ticker&gt;  (e.g. /close AAPL)"

        ticker = parts[0].upper()
        is_confirm = len(parts) > 1 and parts[1].upper() == "CONFIRM"

        if is_confirm:
            if not self._needs_confirmation("close", ticker, chat_id):
                return f"No pending /close {ticker} confirmation. Send /close {ticker} first."
            if not self._rate_limit_check(chat_id):
                return "Rate limited. Wait 60s between destructive commands."
            del self._pending_confirmations[str(chat_id)]
            self._last_destructive[str(chat_id)] = time.time()
            try:
                result = cb(ticker)
                return result if isinstance(result, str) else f"Position {ticker} closed."
            except Exception as exc:
                return f"Error closing {ticker}: {exc}"
        else:
            self._request_confirmation(chat_id, "close", ticker)
            return (
                f"Close position <b>{ticker}</b> requested.\n\n"
                f"Send <b>/close {ticker} CONFIRM</b> within 60s to execute."
            )

    # ------------------------------------------------------------------
    # V12 command handlers
    # ------------------------------------------------------------------

    def _handle_regime(self) -> str:
        """Show current market regime per asset class."""
        cb = self._callbacks.get("regime")
        if cb is None:
            return "/regime is not configured."
        try:
            result = cb()
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                regimes = result.get("regimes", {})
                global_r = result.get("global", "UNKNOWN")
                lines = ["<b>Market Regime (V12)</b>", f"Global: <b>{global_r}</b>", ""]
                for ac, r in regimes.items():
                    lines.append(f"  {ac}: {r}")
                return "\n".join(lines)
            return str(result)
        except Exception as exc:
            return f"Error: {exc}"

    def _handle_portfolio(self) -> str:
        """Show unified cross-broker portfolio."""
        cb = self._callbacks.get("portfolio")
        if cb is None:
            return "/portfolio is not configured."
        try:
            result = cb()
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                lines = [
                    "<b>Unified Portfolio (V12)</b>",
                    f"NAV: ${result.get('nav_total', 0):,.0f}",
                    f"  Binance: ${result.get('binance_equity', 0):,.0f}",
                    f"  IBKR: ${result.get('ibkr_equity', 0):,.0f}",
                    f"  Alpaca: ${result.get('alpaca_equity', 0):,.0f}",
                    f"DD peak: {result.get('dd_from_peak_pct', 0):.1f}%",
                    f"DD daily: {result.get('dd_daily_pct', 0):.1f}%",
                    f"Gross exp: {result.get('gross_exposure_pct', 0):.0f}%",
                    f"Alert: {result.get('alert_level', '?')}",
                ]
                return "\n".join(lines)
            return str(result)
        except Exception as exc:
            return f"Error: {exc}"

    def _handle_emergency(self, chat_id: str, args: str) -> str:
        """Emergency close all brokers. Requires hourly code."""
        cb = self._callbacks.get("emergency")
        if cb is None:
            return "/emergency is not configured."

        if not args.strip():
            try:
                from core.risk.emergency_close_all import _generate_confirmation_code
                code = _generate_confirmation_code()
                return (
                    f"<b>EMERGENCY CLOSE ALL</b>\n\n"
                    f"This will close ALL positions on ALL brokers.\n"
                    f"Current code: <b>{code}</b>\n\n"
                    f"Send <b>/emergency {code}</b> to execute."
                )
            except Exception:
                return "Send /emergency <code> to execute."

        # Execute with code
        code = args.strip().upper()
        try:
            result = cb(code)
            if isinstance(result, dict):
                status = result.get("status", "?")
                if status == "REJECTED":
                    return f"REJECTED: {result.get('reason', 'invalid code')}"
                return (
                    f"<b>EMERGENCY CLOSE EXECUTED</b>\n"
                    f"Positions closed: {result.get('total_positions_closed', 0)}\n"
                    f"Orders cancelled: {result.get('total_orders_cancelled', 0)}\n"
                    f"PnL: ${result.get('total_pnl', 0):,.2f}\n"
                    f"Time: {result.get('elapsed_seconds', 0):.1f}s"
                )
            return str(result)
        except Exception as exc:
            return f"EMERGENCY ERROR: {exc}"

    # ------------------------------------------------------------------
    # Telegram API (polling)
    # ------------------------------------------------------------------

    def _send_response(self, chat_id: str, text: str) -> bool:
        """Send response back to Telegram via sendMessage API."""
        if not self._token:
            logger.warning("Telegram bot token not configured")
            return False

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    return True
                logger.warning("Telegram sendMessage error: %s", result)
                return False
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    def _poll_updates(self) -> list:
        """Get new messages via Telegram getUpdates API.

        Returns:
            List of update dicts from the Telegram API.
        """
        if not self._token:
            return []

        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = urllib.parse.urlencode({
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": json.dumps(["message"]),
        })
        full_url = f"{url}?{params}"

        try:
            req = urllib.request.Request(full_url, method="GET")
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())
                if data.get("ok"):
                    return data.get("result", [])
                logger.warning("Telegram getUpdates error: %s", data)
                return []
        except Exception as exc:
            logger.warning("Telegram poll failed: %s", exc)
            return []

    def start_polling(self, interval: float = 2.0):
        """Start polling for messages. Runs in a blocking loop.

        Call from a background thread:
            threading.Thread(target=handler.start_polling, daemon=True).start()

        Args:
            interval: Seconds to sleep between poll cycles on empty results.
        """
        self._running = True
        logger.info("Telegram command handler: polling started")

        while self._running:
            try:
                updates = self._poll_updates()
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id > self._last_update_id:
                        self._last_update_id = update_id

                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "")

                    if not chat_id or not text:
                        continue

                    response = self.handle_message(chat_id, text)
                    self._send_response(chat_id, response)

                if not updates:
                    time.sleep(interval)

            except Exception as exc:
                logger.exception("Telegram polling error: %s", exc)
                time.sleep(interval * 2)

        logger.info("Telegram command handler: polling stopped")

    def stop_polling(self):
        """Stop the polling loop."""
        self._running = False

    def get_command_log(self, limit: int = 50) -> list:
        """Get recent command audit log entries.

        Args:
            limit: Maximum number of entries to return (most recent first).

        Returns:
            List of log entry dicts with timestamp, chat_id, text, response_preview.
        """
        return list(reversed(self._command_log[-limit:]))
