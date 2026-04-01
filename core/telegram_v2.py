"""
Telegram V2 — Smart notification system for live trading.

Architecture:
  - 3 severity levels with different behavior
  - Intelligent throttling per alert_type
  - Daily digest instead of spam
  - Anti-panic mode (rate limit on CRITICAL storms)
  - Aggregation: batch similar events into 1 message

Design principles:
  - CRITICAL: always send immediately, never throttle
  - TRADE: send immediately but with context (1 msg per trade)
  - INFO: aggregate into digest, never send individually

Usage:
    from core.telegram_v2 import tg
    tg.critical("KILL SWITCH", "crypto", details="daily_loss_-5.2%")
    tg.trade("BUY", "BTCUSDC", qty=0.005, price=68000, sl=64600, strat="STRAT-001")
    tg.info("heartbeat", equity=10000, positions=3)
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger("telegram_v2")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ══════════════════════════════════════════════════════════════════════
# Low-level send
# ══════════════════════════════════════════════════════════════════════

def _raw_send(text: str) -> bool:
    """Send a message via Telegram Bot API. HTML parse mode."""
    if not TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text[:4000],  # Telegram limit 4096 chars
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
# Smart Notification Manager
# ══════════════════════════════════════════════════════════════════════

class TelegramV2:
    """Central notification hub. One instance per worker."""

    # Throttle windows per severity
    THROTTLE = {
        "critical": 0,       # Never throttle criticals
        "trade": 0,          # Never throttle trades
        "warning": 300,      # 5 min between same warning type
        "info": 3600,        # 1 hour between same info type
    }

    # Anti-panic: max messages per minute
    RATE_LIMIT_PER_MIN = 10
    PANIC_COOLDOWN = 300     # 5 min silence after hitting rate limit

    def __init__(self):
        self._lock = Lock()
        self._last_sent: dict[str, float] = {}      # alert_type -> timestamp
        self._send_times: list[float] = []           # last N send timestamps
        self._panic_until: float = 0                 # panic cooldown end
        self._digest_buffer: list[dict] = []         # buffered info messages
        self._trade_count_today: int = 0
        self._error_count_today: int = 0
        self._last_digest_date: str = ""

    # ── Rate limiting ────────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        """Returns True if we can send, False if rate limited."""
        now = time.time()

        # Panic mode active?
        if now < self._panic_until:
            return False

        # Clean old timestamps
        self._send_times = [t for t in self._send_times if now - t < 60]

        if len(self._send_times) >= self.RATE_LIMIT_PER_MIN:
            # Enter panic mode
            self._panic_until = now + self.PANIC_COOLDOWN
            _raw_send(
                "🛑 <b>ANTI-PANIC MODE</b>\n\n"
                f">{self.RATE_LIMIT_PER_MIN} messages/min detecte.\n"
                f"Notifications suspendues {self.PANIC_COOLDOWN//60} min.\n"
                "Les CRITICAL passeront toujours."
            )
            return False

        return True

    def _should_throttle(self, severity: str, alert_type: str) -> bool:
        """Check if this alert type was sent too recently."""
        window = self.THROTTLE.get(severity, 300)
        if window == 0:
            return False
        last = self._last_sent.get(alert_type, 0)
        return (time.time() - last) < window

    def _record_send(self, alert_type: str):
        """Record that we sent this alert type now."""
        now = time.time()
        self._last_sent[alert_type] = now
        self._send_times.append(now)

    # ── CRITICAL — always send immediately ───────────────────────────

    def critical(self, title: str, source: str = "", details: str = ""):
        """CRITICAL: kill switch, crash, API down, DD anormal.

        ALWAYS sent. Bypasses rate limit. Bypasses panic mode.
        """
        with self._lock:
            self._error_count_today += 1
            msg = (
                f"🔴🔴🔴 <b>{title}</b>\n"
                f"{'─' * 25}\n"
            )
            if source:
                msg += f"Source: <code>{source}</code>\n"
            if details:
                msg += f"{details}\n"
            msg += f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            _raw_send(msg)
            self._record_send(f"critical_{title}")

    # ── TRADE — send immediately with compact format ─────────────────

    def trade_entry(self, side: str, symbol: str, qty: float, price: float,
                    sl: float = 0, strat: str = "", broker: str = "",
                    notional: float = 0):
        """Trade ENTRY notification. Always sent."""
        with self._lock:
            if not self._check_rate_limit():
                return
            self._trade_count_today += 1

            arrow = "🟢" if side in ("BUY", "LONG") else "🔴"
            _sl = f" SL ${sl:,.0f}" if sl else ""
            _not = f"${notional:,.0f}" if notional else f"{qty}"
            _brk = f" [{broker}]" if broker else ""

            msg = (
                f"{arrow} <b>{side} {symbol}</b> @ ${price:,.2f}\n"
                f"Size: {_not}{_sl}{_brk}\n"
                f"Strat: <code>{strat}</code>"
            )
            _raw_send(msg)
            self._record_send(f"trade_{symbol}")

    def trade_exit(self, side: str, symbol: str, pnl: float = 0,
                   reason: str = "", strat: str = ""):
        """Trade EXIT notification. Always sent."""
        with self._lock:
            if not self._check_rate_limit():
                return

            pnl_emoji = "✅" if pnl >= 0 else "❌"
            msg = (
                f"{pnl_emoji} <b>CLOSE {symbol}</b> PnL ${pnl:+,.2f}\n"
                f"Reason: {reason}\n"
                f"Strat: <code>{strat}</code>"
            )
            _raw_send(msg)
            self._record_send(f"exit_{symbol}")

    # ── WARNING — throttled per type ─────────────────────────────────

    def warning(self, title: str, details: str = ""):
        """WARNING: regime change, risk reduction, anomalie moderee.

        Throttled: 1 per type per 5 min.
        """
        with self._lock:
            alert_type = f"warn_{title}"
            if self._should_throttle("warning", alert_type):
                return
            if not self._check_rate_limit():
                return

            msg = (
                f"🟡 <b>{title}</b>\n"
                f"{details}"
            )
            _raw_send(msg)
            self._record_send(alert_type)

    # ── INFO — buffered into digest ──────────────────────────────────

    def info(self, event: str, **kwargs):
        """INFO: logs techniques, confirmations, metriques.

        NEVER sent individually. Buffered into daily digest.
        """
        with self._lock:
            self._digest_buffer.append({
                "time": datetime.now(timezone.utc).strftime("%H:%M"),
                "event": event,
                **kwargs,
            })
            # Keep buffer manageable
            if len(self._digest_buffer) > 200:
                self._digest_buffer = self._digest_buffer[-100:]

    # ── DIGEST — periodic summary ────────────────────────────────────

    def send_digest(self, equity_binance: float = 0, equity_ibkr: float = 0,
                    equity_alpaca: float = 0, n_positions: int = 0,
                    regime: str = ""):
        """Send periodic digest (call every 4h from worker).

        Replaces heartbeat spam with 1 structured message.
        """
        with self._lock:
            now = datetime.now(timezone.utc)

            total = equity_binance + equity_ibkr + equity_alpaca
            n_info = len(self._digest_buffer)

            msg = (
                f"📊 <b>DIGEST {now.strftime('%H:%M UTC')}</b>\n"
                f"{'─' * 25}\n"
            )

            # Portfolio summary
            if total > 0:
                lines = []
                if equity_binance > 0:
                    lines.append(f"  Binance: ${equity_binance:,.0f}")
                if equity_ibkr > 0:
                    lines.append(f"  IBKR: ${equity_ibkr:,.0f}")
                if equity_alpaca > 0:
                    lines.append(f"  Alpaca: ${equity_alpaca:,.0f}")
                msg += f"NAV: <b>${total:,.0f}</b>\n"
                msg += "\n".join(lines) + "\n"

            if n_positions > 0:
                msg += f"Positions: {n_positions}\n"
            if regime:
                msg += f"Regime: <code>{regime}</code>\n"

            # Day stats
            msg += f"\n📈 Trades: {self._trade_count_today}"
            msg += f" | Errors: {self._error_count_today}"
            msg += f" | Events: {n_info}\n"

            # Notable events from buffer (only anomalies)
            notable = [e for e in self._digest_buffer if "error" in e.get("event", "").lower()
                       or "fail" in e.get("event", "").lower()
                       or "warning" in e.get("event", "").lower()]
            if notable:
                msg += f"\n⚠️ Notable ({len(notable)}):\n"
                for e in notable[-5:]:
                    msg += f"  {e['time']} {e['event']}\n"

            _raw_send(msg)

            # Reset daily counters at midnight
            today = now.strftime("%Y-%m-%d")
            if self._last_digest_date != today:
                self._trade_count_today = 0
                self._error_count_today = 0
                self._last_digest_date = today

            # Clear buffer after digest
            self._digest_buffer = []

    # ── DAILY EOD REPORT ─────────────────────────────────────────────

    def send_eod_report(self, equity: float, daily_pnl: float,
                        n_trades: int, n_wins: int, n_losses: int,
                        best_trade: str = "", worst_trade: str = ""):
        """End-of-day report. Sent once at market close."""
        pnl_pct = daily_pnl / equity * 100 if equity > 0 else 0
        emoji = "📈" if daily_pnl >= 0 else "📉"

        msg = (
            f"{emoji} <b>EOD REPORT</b>\n"
            f"{'═' * 25}\n"
            f"Equity: <b>${equity:,.0f}</b>\n"
            f"P&L: <b>${daily_pnl:+,.0f}</b> ({pnl_pct:+.2f}%)\n"
            f"Trades: {n_trades} ({n_wins}W / {n_losses}L)\n"
        )
        if best_trade:
            msg += f"Best: {best_trade}\n"
        if worst_trade:
            msg += f"Worst: {worst_trade}\n"

        _raw_send(msg)


# ══════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════

tg = TelegramV2()
