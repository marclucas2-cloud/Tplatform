"""
TG-001 — CryptoTelegramBot: Telegram bot for crypto portfolio monitoring.

CODE READY — NOT ACTIVATED. Marc will create the bot via @BotFather,
then set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in env.

Works WITHOUT python-telegram-bot installed (lazy imports + ImportError guard).

12 commands:
  /status     — Portfolio summary (equity, P&L, drawdown, positions, regime)
  /positions  — Detailed open positions with P&L, broker badges
  /pnl        — P&L for 24h / 7d / 30d
  /risk       — Risk indicators (drawdown, margin level, exposure)
  /earn       — Earn positions with APY and daily yield
  /regime     — Current regime (BULL/BEAR/CHOP) with confidence
  /borrow     — Current borrow rates per asset
  /kill       — Kill switch (requires CONFIRM argument)
  /alerts     — Last 20 alerts
  /strats     — Performance by strategy
  /sweep      — Cash sweep status
  /help       — List all commands

Security:
  - authorized_chat_id check on EVERY command
  - /kill requires "CONFIRM" argument
  - No API keys in messages
  - Rate limiting (max 5 commands / 60s per user)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


class CryptoTelegramBot:
    """Telegram bot for crypto monitoring — code ready, not activated.

    Usage::

        bot = CryptoTelegramBot(
            token=os.environ["TELEGRAM_BOT_TOKEN"],
            authorized_chat_id=int(os.environ["TELEGRAM_CHAT_ID"]),
            monitor=live_monitor,
            risk_manager=risk_manager,
        )
        app = bot.setup()
        app.run_polling()
    """

    MAX_COMMANDS_PER_MINUTE = 5

    def __init__(
        self,
        token: str,
        authorized_chat_id: int,
        monitor=None,
        risk_manager=None,
    ):
        self._token = token
        self._authorized_chat_id = authorized_chat_id
        self._monitor = monitor
        self._risk_manager = risk_manager
        # Rate limiter: chat_id -> list of timestamps
        self._rate_log: dict[int, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Auth & rate limiting
    # ------------------------------------------------------------------

    def _auth(self, update) -> bool:
        """Check that the message comes from the authorized chat."""
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id != self._authorized_chat_id:
            logger.warning(f"Unauthorized access attempt from chat_id={chat_id}")
            return False
        return True

    def _rate_limit(self, update) -> bool:
        """Return True if the user is rate-limited (should be blocked).

        Max 5 commands per 60 seconds per user.
        """
        chat_id = update.effective_chat.id if update.effective_chat else 0
        now = time.time()
        cutoff = now - 60

        # Prune old entries
        self._rate_log[chat_id] = [
            ts for ts in self._rate_log[chat_id] if ts > cutoff
        ]

        if len(self._rate_log[chat_id]) >= self.MAX_COMMANDS_PER_MINUTE:
            return True  # Rate limited

        self._rate_log[chat_id].append(now)
        return False

    # ------------------------------------------------------------------
    # Automated alerts (not from commands)
    # ------------------------------------------------------------------

    def send_alert(self, level: str, message: str) -> None:
        """Send an automated alert via Telegram (non-interactive).

        Uses emoji per level:
            INFO     = info_emoji
            WARNING  = warning_emoji
            CRITICAL = siren_emoji
        """
        emoji_map = {
            "INFO": "\u2139\ufe0f",       # info
            "WARNING": "\u26a0\ufe0f",     # warning triangle
            "CRITICAL": "\U0001f6a8",      # siren
        }
        emoji = emoji_map.get(level, "\u2139\ufe0f")
        text = f"{emoji} *[{level}]* {message}"

        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = {
                "chat_id": self._authorized_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram alert failed: {resp.status_code} {resp.text}")
        except ImportError:
            logger.warning("requests not installed — cannot send Telegram alert")
        except Exception as e:
            logger.error(f"Telegram alert error: {e}")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_status(self, update, context) -> None:
        """/status — Portfolio summary."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        equity = snapshot.get("equity", 0)
        pnl = snapshot.get("pnl_total", 0)
        pnl_pct = snapshot.get("pnl_pct", 0)
        risk = snapshot.get("risk", {})
        dd = risk.get("drawdown_pct", 0)
        regime = snapshot.get("regime", "UNKNOWN")
        ks = risk.get("kill_switch_active", False)
        n_pos = len(snapshot.get("positions", []))
        n_earn = len(snapshot.get("earn_positions", []))

        sign = "+" if pnl >= 0 else ""
        ks_txt = "\U0001f534 ACTIVE" if ks else "\U0001f7e2 OFF"

        text = (
            "\U0001f4ca *Crypto Portfolio Status*\n\n"
            f"Equity: `${equity:,.2f}`\n"
            f"P&L: `{sign}${pnl:,.2f}` ({sign}{pnl_pct:.1f}%)\n"
            f"Drawdown: `{dd:.1f}%`\n"
            f"Positions: `{n_pos}` open + `{n_earn}` earn\n"
            f"Regime: *{regime}*\n"
            f"Kill Switch: {ks_txt}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_positions(self, update, context) -> None:
        """/positions — Detailed open positions."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        positions = snapshot.get("positions", [])
        if not positions:
            await update.message.reply_text("No open positions.")
            return

        lines = ["\U0001f4cb *Open Positions*\n"]
        for p in positions:
            symbol = p.get("symbol", "???")
            side = p.get("side", "?")
            pnl = p.get("pnl", 0)
            pnl_pct = p.get("pnl_pct", 0)
            mode = p.get("mode", "")
            strategy = p.get("strategy", "")
            sign = "+" if pnl >= 0 else ""

            # Badge for wallet type
            badge = "\U0001f4b0"  # default
            mode_upper = mode.upper() if mode else ""
            if "MARGIN" in mode_upper:
                badge = "\U0001f4b3"  # credit card = margin
            elif "SPOT" in mode_upper:
                badge = "\U0001f4b5"  # dollar = spot

            line = (
                f"{badge} *{symbol}* ({side})\n"
                f"   P&L: `{sign}${pnl:.2f}` ({sign}{pnl_pct:.1f}%)"
            )
            if strategy:
                line += f"\n   Strategy: `{strategy}`"

            # Margin-specific info
            borrow_rate = p.get("borrow_rate")
            margin_lvl = p.get("margin_level")
            if borrow_rate is not None and borrow_rate > 0:
                line += f"\n   Borrow: `{borrow_rate*100:.3f}%/day`"
            if margin_lvl is not None and margin_lvl > 0 and margin_lvl < 999:
                line += f" | Margin: `{margin_lvl:.2f}`"

            lines.append(line)

        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

    async def _cmd_pnl(self, update, context) -> None:
        """/pnl — P&L for 24h / 7d / 30d."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        lines = ["\U0001f4c8 *P&L Summary*\n"]
        for hours, label in [(24, "24h"), (168, "7d"), (720, "30d")]:
            summary = self._monitor.get_summary(period_hours=hours)
            if summary:
                pnl = summary["pnl_period"]
                pnl_pct = summary["pnl_period_pct"]
                sign = "+" if pnl >= 0 else ""
                dd = summary["max_drawdown_pct"]
                lines.append(
                    f"*{label}*: `{sign}${pnl:,.2f}` ({sign}{pnl_pct:.1f}%) | DD: `{dd:.1f}%`"
                )
            else:
                lines.append(f"*{label}*: _no data_")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_risk(self, update, context) -> None:
        """/risk — Risk indicators."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        risk = snapshot.get("risk", {})
        dd = risk.get("drawdown_pct", 0)
        margin_lvl = risk.get("margin_level", 0)
        gross = risk.get("gross_exposure_pct", 0)
        net = risk.get("net_exposure_pct", 0)
        ks = risk.get("kill_switch_active", False)

        ks_txt = "\U0001f534 ACTIVE" if ks else "\U0001f7e2 OFF"

        # Margin level color
        if margin_lvl < 1.3:
            ml_emoji = "\U0001f534"
        elif margin_lvl < 1.5:
            ml_emoji = "\U0001f7e1"
        else:
            ml_emoji = "\U0001f7e2"

        text = (
            "\U0001f6e1 *Risk Dashboard*\n\n"
            f"Drawdown: `{dd:.1f}%`\n"
            f"Margin Level: {ml_emoji} `{margin_lvl:.2f}`\n"
            f"Gross Exposure: `{gross:.1f}%`\n"
            f"Net Exposure: `{net:.1f}%`\n"
            f"Kill Switch: {ks_txt}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_earn(self, update, context) -> None:
        """/earn — Earn positions with APY and yield."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        earn = snapshot.get("earn_positions", [])
        if not earn:
            await update.message.reply_text("No earn positions.")
            return

        lines = ["\U0001f4b0 *Earn Positions*\n"]
        total_daily = 0
        for e_pos in earn:
            asset = e_pos.get("asset", "?")
            amount = e_pos.get("amount", 0)
            apy = e_pos.get("apy", 0)
            daily = e_pos.get("daily_yield", 0)
            total_daily += daily
            lines.append(
                f"*{asset}*: `${amount:,.2f}` | APY: `{apy:.1f}%` | Daily: `${daily:.2f}`"
            )

        lines.append(f"\nTotal daily yield: `${total_daily:.2f}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_regime(self, update, context) -> None:
        """/regime — Current regime with confidence."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        regime = snapshot.get("regime", "UNKNOWN")

        regime_emoji = {
            "BULL": "\U0001f7e2 \U0001f402",   # green + bull
            "BEAR": "\U0001f534 \U0001f43b",   # red + bear
            "CHOP": "\U0001f7e1 \U0001f300",   # yellow + cyclone
        }
        emoji = regime_emoji.get(regime, "\u2753")

        # Try to get confidence from risk manager
        confidence = None
        if self._risk_manager:
            try:
                confidence = getattr(self._risk_manager, "regime_confidence", None)
            except Exception:
                pass

        text = f"{emoji} *Regime: {regime}*"
        if confidence is not None:
            text += f"\nConfidence: `{confidence:.0%}`"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_borrow(self, update, context) -> None:
        """/borrow — Current borrow rates per asset."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        margin_positions = [
            p for p in snapshot.get("positions", [])
            if p.get("borrow_rate") is not None and p.get("borrow_rate", 0) > 0
        ]

        if not margin_positions:
            await update.message.reply_text("No active borrows.")
            return

        lines = ["\U0001f4b3 *Active Borrows*\n"]
        total_cost = 0
        for p in margin_positions:
            symbol = p.get("symbol", "?")
            rate = p.get("borrow_rate", 0)
            cumul = p.get("borrow_cost_cumul", 0)
            total_cost += cumul

            # Rate warning indicator
            rate_emoji = "\U0001f534" if rate > 0.001 else "\U0001f7e2"

            lines.append(
                f"{rate_emoji} *{symbol}*: `{rate*100:.3f}%/day` | "
                f"Cumul cost: `${cumul:.2f}`"
            )

        lines.append(f"\nTotal borrow cost: `${total_cost:.2f}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_kill(self, update, context) -> None:
        """/kill CONFIRM — Activate kill switch with double confirmation."""
        if not self._auth(update) or self._rate_limit(update):
            return

        args = context.args if context and context.args else []

        if not args or args[0] != "CONFIRM":
            await update.message.reply_text(
                "\u26a0\ufe0f *Kill Switch*\n\n"
                "This will close ALL positions and stop trading.\n"
                "To confirm, send: `/kill CONFIRM`",
                parse_mode="Markdown",
            )
            return

        if not self._risk_manager:
            await update.message.reply_text("Risk manager not initialized.")
            return

        try:
            ks = self._risk_manager.kill_switch
            if hasattr(ks, "activate"):
                ks.activate(reason="Telegram /kill command")
            elif hasattr(ks, "trigger"):
                ks.trigger(reason="Telegram /kill command")
            else:
                await update.message.reply_text("Kill switch has no activate/trigger method.")
                return

            await update.message.reply_text(
                "\U0001f6a8 *KILL SWITCH ACTIVATED*\n\n"
                "All positions will be closed.\n"
                "Trading is halted.",
                parse_mode="Markdown",
            )
            logger.critical("Kill switch activated via Telegram /kill command")
        except Exception as e:
            await update.message.reply_text(f"Kill switch error: {e}")

    async def _cmd_alerts(self, update, context) -> None:
        """/alerts — Last 20 alerts."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        # Collect alerts from recent snapshots
        all_alerts = []
        for snap in reversed(self._monitor._snapshots):
            for alert in snap.get("alerts", []):
                all_alerts.append({
                    "timestamp": snap.get("timestamp", ""),
                    "level": alert.get("level", "INFO"),
                    "message": alert.get("message", ""),
                })
            if len(all_alerts) >= 20:
                break

        if not all_alerts:
            await update.message.reply_text("No recent alerts.")
            return

        emoji_map = {
            "INFO": "\u2139\ufe0f",
            "WARNING": "\u26a0\ufe0f",
            "CRITICAL": "\U0001f6a8",
        }

        lines = ["\U0001f514 *Recent Alerts*\n"]
        for a in all_alerts[:20]:
            emoji = emoji_map.get(a["level"], "\u2139\ufe0f")
            ts = a["timestamp"]
            # Shorten timestamp to HH:MM
            if "T" in ts:
                ts_short = ts.split("T")[1][:5]
            else:
                ts_short = ts[:16]
            lines.append(f"{emoji} `{ts_short}` {a['message']}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_strats(self, update, context) -> None:
        """/strats — Performance by strategy."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        positions = snapshot.get("positions", [])
        if not positions:
            await update.message.reply_text("No positions to analyze.")
            return

        # Group by strategy
        strat_pnl: dict[str, list[float]] = {}
        strat_count: dict[str, int] = {}
        for p in positions:
            strat = p.get("strategy", "unknown") or "unknown"
            pnl = p.get("pnl", 0)
            strat_pnl.setdefault(strat, []).append(pnl)
            strat_count[strat] = strat_count.get(strat, 0) + 1

        lines = ["\U0001f4ca *Strategy Performance*\n"]
        for strat in sorted(strat_pnl.keys()):
            total = sum(strat_pnl[strat])
            count = strat_count[strat]
            sign = "+" if total >= 0 else ""
            emoji = "\U0001f7e2" if total >= 0 else "\U0001f534"
            lines.append(
                f"{emoji} *{strat}*: `{sign}${total:.2f}` | `{count}` pos"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_sweep(self, update, context) -> None:
        """/sweep — Cash sweep status (earn balance, APY, total swept)."""
        if not self._auth(update) or self._rate_limit(update):
            return

        if not self._monitor:
            await update.message.reply_text("Monitor not initialized.")
            return

        try:
            snapshot = self._monitor.run_check()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        earn = snapshot.get("earn_positions", [])
        balances = snapshot.get("balances", {})
        earn_total = balances.get("earn_usdt", 0)

        total_apy_weighted = 0
        total_amount = 0
        for e_pos in earn:
            amount = e_pos.get("amount", 0)
            apy = e_pos.get("apy", 0)
            total_apy_weighted += amount * apy
            total_amount += amount

        avg_apy = total_apy_weighted / total_amount if total_amount > 0 else 0

        text = (
            "\U0001f9f9 *Cash Sweep Status*\n\n"
            f"Earn Balance: `${earn_total:,.2f}`\n"
            f"Avg APY: `{avg_apy:.1f}%`\n"
            f"Earn Positions: `{len(earn)}`\n"
            f"Daily Yield Est: `${total_amount * avg_apy / 36500:.2f}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_help(self, update, context) -> None:
        """/help — List all commands."""
        if not self._auth(update) or self._rate_limit(update):
            return

        text = (
            "\U0001f916 *Crypto Bot Commands*\n\n"
            "/status — Portfolio summary\n"
            "/positions — Open positions with P&L\n"
            "/pnl — P&L for 24h / 7d / 30d\n"
            "/risk — Risk indicators\n"
            "/earn — Earn positions (APY, yield)\n"
            "/regime — Market regime (BULL/BEAR/CHOP)\n"
            "/borrow — Active borrow rates\n"
            "/kill CONFIRM — Activate kill switch\n"
            "/alerts — Last 20 alerts\n"
            "/strats — Performance by strategy\n"
            "/sweep — Cash sweep status\n"
            "/help — This message"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self):
        """Register all command handlers and return the Application.

        The caller is responsible for running ``app.run_polling()``.

        Raises:
            ImportError: if python-telegram-bot is not installed.

        Returns:
            telegram.ext.Application
        """
        try:
            from telegram.ext import Application, CommandHandler
        except ImportError:
            logger.error(
                "python-telegram-bot not installed. "
                "Install with: pip install python-telegram-bot"
            )
            raise ImportError(
                "python-telegram-bot is required. "
                "Install with: pip install python-telegram-bot"
            )

        app = Application.builder().token(self._token).build()

        commands = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "pnl": self._cmd_pnl,
            "risk": self._cmd_risk,
            "earn": self._cmd_earn,
            "regime": self._cmd_regime,
            "borrow": self._cmd_borrow,
            "kill": self._cmd_kill,
            "alerts": self._cmd_alerts,
            "strats": self._cmd_strats,
            "sweep": self._cmd_sweep,
            "help": self._cmd_help,
        }

        for cmd_name, handler_fn in commands.items():
            app.add_handler(CommandHandler(cmd_name, handler_fn))

        logger.info(
            f"CryptoTelegramBot ready with {len(commands)} commands. "
            f"Authorized chat_id={self._authorized_chat_id}"
        )

        return app
