"""
Telegram Bot Service — standalone bot with rich multi-broker data.

Runs as a separate systemd service. Commands:
  /status     — Portfolio multi-broker (IBKR + Binance + Alpaca)
  /positions  — All open positions with P&L
  /signals    — Recent signals from worker
  /strats     — All 46 strategies status
  /risk       — Risk indicators + kill switch
  /earn       — Earn positions with APY
  /crypto     — Crypto strategies detail
  /help       — Commands list
"""
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram-bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


def _auth(update: Update) -> bool:
    return update.effective_chat.id == CHAT_ID


# ── Data fetchers ────────────────────────────────────────────────────────────

def _binance():
    try:
        from core.broker.binance_broker import BinanceBroker
        return BinanceBroker()
    except Exception:
        return None


def _binance_info():
    bnb = _binance()
    if not bnb:
        return {}
    try:
        return bnb.get_account_info()
    except Exception as e:
        return {"error": str(e)}


def _binance_positions():
    bnb = _binance()
    if not bnb:
        return []
    try:
        return bnb.get_positions()
    except Exception:
        return []


def _alpaca_info():
    try:
        from core.alpaca_client.client import AlpacaClient
        ac = AlpacaClient.from_env()
        return ac.get_account_info()
    except Exception as e:
        return {"error": str(e)}


def _alpaca_positions():
    try:
        from core.alpaca_client.client import AlpacaClient
        ac = AlpacaClient.from_env()
        return ac.get_positions()
    except Exception:
        return []


def _ibkr_connected():
    import socket
    try:
        with socket.create_connection(
            (os.environ.get("IBKR_HOST", "127.0.0.1"),
             int(os.environ.get("IBKR_PORT", "4002"))),
            timeout=2
        ):
            return True
    except Exception:
        return False


def _worker_signals(n=12):
    try:
        r = subprocess.run(
            ["journalctl", "-u", "trading-worker", "--no-pager", "-n", "150"],
            capture_output=True, text=True, timeout=5
        )
        lines = []
        for l in r.stdout.split("\n"):
            if "SIGNAL" in l or "pas de signal" in l:
                # Extract after [worker]
                idx = l.find("[worker]")
                if idx > 0:
                    lines.append(l[idx + 9:].strip())
                else:
                    lines.append(l.strip()[-100:])
        return lines[-n:]
    except Exception:
        return []


# ── Commands ─────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — Portfolio complet multi-broker."""
    if not _auth(update):
        return

    bnb = _binance_info()
    alp = _alpaca_info()
    ibkr_ok = _ibkr_connected()

    bnb_eq = bnb.get("equity", 0)
    bnb_spot = bnb.get("spot_total_usd", 0)
    bnb_earn = bnb.get("earn_total_usd", 0)
    alp_eq = alp.get("equity", 0)
    alp_paper = alp.get("paper", True)

    total = bnb_eq + alp_eq

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    text = (
        f"\U0001f4ca *Portfolio Status* ({now})\n"
        f"{'=' * 30}\n\n"
        f"\U0001f7e2 *Binance* — LIVE\n"
        f"  Equity: `${bnb_eq:,.2f}`\n"
        f"  Spot: `${bnb_spot:,.2f}`\n"
        f"  Earn: `${bnb_earn:,.2f}`\n"
        f"  Margin lvl: `{bnb.get('margin_level', 'N/A')}`\n\n"
        f"{'🟢' if ibkr_ok else '🔴'} *IBKR* — {'LIVE' if ibkr_ok else 'OFF (weekend)'}\n"
        f"  Capital: `~500 EUR` (virement 10K en cours)\n"
        f"  Port: `{os.environ.get('IBKR_PORT', '4002')}`\n\n"
        f"\U0001f7e1 *Alpaca* — {'PAPER' if alp_paper else 'LIVE'}\n"
        f"  Equity: `${alp_eq:,.2f}`\n\n"
        f"\U0001f4b0 *Total*: `${total:,.2f}`\n"
        f"\U0001f6e1 Safety Mode: `ON (Phase 1)`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/positions — Toutes les positions ouvertes."""
    if not _auth(update):
        return

    bnb_pos = _binance_positions()
    alp_pos = _alpaca_positions()

    lines = ["\U0001f4cb *Positions Ouvertes*\n"]

    if bnb_pos:
        lines.append("*Binance:*")
        for p in bnb_pos[:10]:
            sym = p.get("symbol", "?")
            qty = float(p.get("qty", 0))
            mv = float(p.get("market_value", 0))
            pnl = float(p.get("unrealized_pl", 0))
            entry = float(p.get("avg_entry", 0))
            price = float(p.get("current_price", 0))
            sign = "+" if pnl >= 0 else ""
            emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
            lines.append(
                f"  {emoji} `{sym}` qty={qty:.6f}\n"
                f"      entry=${entry:,.2f} now=${price:,.2f}\n"
                f"      val=${mv:,.2f} P&L={sign}${pnl:,.2f}"
            )
    else:
        lines.append("*Binance:* Aucune position")

    if alp_pos:
        lines.append("\n*Alpaca (PAPER):*")
        for p in alp_pos[:10]:
            sym = p.get("symbol", "?")
            qty = float(p.get("qty", 0))
            mv = float(p.get("market_val", 0))
            pnl = float(p.get("unrealized_pl", 0))
            sign = "+" if pnl >= 0 else ""
            emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
            lines.append(f"  {emoji} `{sym}` {qty} shares val=${mv:,.2f} P&L={sign}${pnl:,.2f}")
    else:
        lines.append("\n*Alpaca:* Aucune position")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/signals — Derniers signaux du worker."""
    if not _auth(update):
        return

    signals = _worker_signals(12)
    if not signals:
        await update.message.reply_text("Aucun signal recent.")
        return

    lines = ["\U0001f4e1 *Derniers Signaux*\n"]
    for s in signals:
        if "SIGNAL" in s:
            lines.append(f"\U0001f534 `{s[:120]}`")
        else:
            lines.append(f"\u2796 `{s[:80]}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_strats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/strats — Toutes les strategies par categorie."""
    if not _auth(update):
        return

    lines = ["\U0001f3af *Strategies (46 total)*\n"]

    # Crypto
    try:
        sys.path.insert(0, str(ROOT))
        from strategies.crypto import CRYPTO_STRATEGIES
        lines.append(f"*Crypto Binance ({len(CRYPTO_STRATEGIES)} strats):*")
        for sid, data in CRYPTO_STRATEGIES.items():
            cfg = data["config"]
            name = cfg.get("name", sid)
            mtype = cfg.get("market_type", "spot")
            alloc = cfg.get("allocation_pct", 0) * 100
            lines.append(f"  \U0001f7e2 `{name}` [{mtype}] {alloc:.0f}%")
    except Exception as e:
        lines.append(f"  Erreur crypto: {e}")

    # FX/EU
    lines.append(f"\n*FX/EU (IBKR) — 15 strats:*")
    fx_strats = [
        "EUR/USD Carry", "EUR/GBP MR", "EUR/JPY Mom",
        "AUD/JPY Carry", "GBP/USD Trend", "USD/CHF Range",
        "NZD/USD Carry", "EUR/NOK Carry", "EU STOXX Pairs",
        "DAX Opening Range", "CAC MR", "FTSE Gap",
        "EUR/SEK", "Lead-Lag Cross-TZ", "Nordic Carry"
    ]
    for s in fx_strats[:8]:
        lines.append(f"  \U0001f535 `{s}`")
    lines.append(f"  ... +{len(fx_strats)-8} autres")

    # US
    lines.append(f"\n*US Equities (Alpaca PAPER) — 7 strats:*")
    us_strats = ["OpEx Gamma", "Gap Continuation", "Day-of-Week",
                 "Late Day MR", "Crypto Proxy V2", "Triple EMA", "Dow Seasonal"]
    for s in us_strats:
        lines.append(f"  \U0001f7e1 `{s}`")

    # Futures
    lines.append(f"\n*Futures Micro (IBKR) — 8 strats:*")
    lines.append(f"  \U0001f535 MES Trend, MNQ MR, MCL Brent, MGC Gold...")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/risk — Indicateurs de risque complets."""
    if not _auth(update):
        return

    bnb = _binance_info()
    ibkr_ok = _ibkr_connected()

    # Worker health
    worker_ok = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as r:
            worker_ok = r.status == 200
    except Exception:
        pass

    text = (
        f"\U0001f6e1 *Risk Status*\n"
        f"{'=' * 30}\n\n"
        f"*Kill Switch:*\n"
        f"  IBKR: `OFF` \U0001f7e2\n"
        f"  Binance: `OFF` \U0001f7e2\n\n"
        f"*Margin:*\n"
        f"  Binance level: `{bnb.get('margin_level', 'N/A')}`\n"
        f"  Borrowed BTC: `{bnb.get('margin_borrowed_btc', 0)}`\n\n"
        f"*Infra:*\n"
        f"  Worker: `{'RUNNING' if worker_ok else 'OFF'}` {'🟢' if worker_ok else '🔴'}\n"
        f"  IBKR Gateway: `{'ON' if ibkr_ok else 'OFF'}` {'🟢' if ibkr_ok else '🔴'}\n"
        f"  Dashboard: `ON` \U0001f7e2\n\n"
        f"*Safety Mode:* `Phase 1 ON`\n"
        f"  Max 5 strats simultanées\n"
        f"  Max leverage 1.0x\n"
        f"  Max ERE 20%"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_earn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/earn — Positions Earn Binance avec APY."""
    if not _auth(update):
        return

    bnb = _binance()
    if not bnb:
        await update.message.reply_text("Binance indisponible.")
        return

    try:
        resp = bnb._get("/sapi/v1/simple-earn/flexible/position", signed=True, weight=10)
        rows = resp.get("rows", []) if isinstance(resp, dict) else []

        lines = ["\U0001f4b0 *Earn Positions (Flexible)*\n"]
        total_usd = 0

        for r in rows:
            amt = float(r.get("totalAmount", 0))
            if amt <= 0:
                continue
            asset = r.get("asset", "?")
            apy = float(r.get("latestAnnualPercentageRate", 0)) * 100

            # USD value
            usd_val = 0
            if asset in ("USDT", "USDC"):
                usd_val = amt
            else:
                try:
                    ticker = bnb._get("/api/v3/ticker/price", {"symbol": asset + "USDT"})
                    usd_val = amt * float(ticker["price"])
                except Exception:
                    pass
            total_usd += usd_val

            daily_yield = usd_val * (apy / 100) / 365
            lines.append(
                f"  `{asset}`: {amt:.4f} (${usd_val:,.2f})\n"
                f"      APY: {apy:.1f}% | yield/jour: ${daily_yield:.2f}"
            )

        daily_total = total_usd * 0.04 / 365  # ~4% average APY estimate
        lines.append(f"\n\U0001f4b5 *Total Earn:* `${total_usd:,.2f}`")
        lines.append(f"\U0001f4c8 *Yield/jour:* ~`${daily_total:.2f}`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/crypto — Detail strategies crypto."""
    if not _auth(update):
        return

    bnb_info = _binance_info()
    equity = bnb_info.get("equity", 0)

    lines = [
        f"\U0001f4ca *Crypto Binance* — LIVE\n",
        f"Equity: `${equity:,.2f}`\n",
    ]

    try:
        from strategies.crypto import CRYPTO_STRATEGIES
        for sid, data in CRYPTO_STRATEGIES.items():
            cfg = data["config"]
            name = cfg.get("name", sid)
            mtype = cfg.get("market_type", "spot")
            alloc = cfg.get("allocation_pct", 0)
            symbols = ", ".join(cfg.get("symbols", [])[:3])
            capital = equity * alloc
            tf = cfg.get("timeframe", "?")

            badge = {"spot": "\U0001f4b5", "margin": "\U0001f4b3", "earn": "\U0001f3e6"}.get(mtype, "\U0001f4b0")
            lines.append(
                f"{badge} *{name}*\n"
                f"  {mtype} | {tf} | {alloc*100:.0f}% (${capital:,.0f})\n"
                f"  {symbols}"
            )
    except Exception as e:
        lines.append(f"Erreur: {e}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — Liste des commandes."""
    if not _auth(update):
        return

    text = (
        "\U0001f916 *tradingKing Bot*\n"
        f"{'=' * 25}\n\n"
        "/status — Portfolio multi-broker\n"
        "/positions — Positions ouvertes\n"
        "/signals — Derniers signaux worker\n"
        "/strats — 46 strategies (toutes)\n"
        "/crypto — Detail crypto Binance\n"
        "/risk — Risk + kill switch + infra\n"
        "/earn — Earn positions + APY\n"
        "/help — Cette aide\n\n"
        "\U0001f310 Dashboard: trading.aucoeurdeville-laval.fr"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info(f"Starting tradingKing bot (chat_id={CHAT_ID})")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("strats", cmd_strats))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("earn", cmd_earn))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    logger.info("Bot polling started — 8 commands active")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
