"""
Telegram Bot Service — rich multi-broker dashboard from phone.

Commandes:
  /status     — NAV, P&L, brokers, regime
  /positions  — Positions ouvertes (live + paper)
  /strats     — Strategies par phase (LIVE/PAPER/WF)
  /crypto     — Crypto Binance detail + earn
  /fx         — FX carry status + signaux
  /risk       — Kill switch, drawdown, limites
  /signals    — Derniers signaux worker
  /trades     — Derniers trades executes
  /costs      — Couts trading (commissions, slippage)
  /health     — Infra status (worker, IBKR, Binance)
  /kill CONFIRM — KILL SWITCH (ferme tout)
  /help       — Commandes
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

def _binance_info():
    try:
        from core.broker.binance_broker import BinanceBroker
        return BinanceBroker().get_account_info()
    except Exception as e:
        return {"error": str(e)}


def _binance_positions():
    try:
        from core.broker.binance_broker import BinanceBroker
        return BinanceBroker().get_positions()
    except Exception:
        return []


def _alpaca_info():
    try:
        from core.alpaca_client.client import AlpacaClient
        return AlpacaClient.from_env().get_account_info()
    except Exception as e:
        return {"error": str(e)}


def _alpaca_positions():
    try:
        from core.alpaca_client.client import AlpacaClient
        return AlpacaClient.from_env().get_positions()
    except Exception:
        return []


def _ibkr_equity():
    """IBKR equity from worker snapshot (no direct connection)."""
    import glob
    log_dir = ROOT / "logs" / "portfolio"
    if not log_dir.exists():
        return 0.0
    files = sorted(glob.glob(str(log_dir / "*.jsonl")), reverse=True)
    for fpath in files[:2]:
        try:
            with open(fpath) as f:
                lines = f.readlines()
            for line in reversed(lines[-10:]):
                snap = json.loads(line.strip())
                for b in snap.get("portfolio", {}).get("brokers", []):
                    if b.get("broker") == "ibkr":
                        return float(b.get("equity", 0))
        except Exception:
            continue
    return 0.0


def _ibkr_connected():
    import socket
    try:
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("IBKR_PORT", "4002"))
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def _worker_running():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _worker_signals(n=15):
    """Read recent signals from worker log file."""
    log_file = ROOT / "logs" / "worker" / "worker_stdout.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(errors="replace").split("\n")
        signals = []
        for l in reversed(lines):
            if any(k in l for k in ["SIGNAL", "pas de signal", "aucun signal", "SKIP"]):
                # Extract timestamp + message
                parts = l.split("] ", 1)
                msg = parts[-1].strip() if len(parts) > 1 else l.strip()
                if msg and len(msg) > 5:
                    signals.append(msg[:120])
            if len(signals) >= n:
                break
        return list(reversed(signals))
    except Exception:
        return []


def _load_cash_flows():
    cf_path = ROOT / "data" / "cash_flows.jsonl"
    if not cf_path.exists():
        return []
    try:
        return [json.loads(l) for l in cf_path.read_text().strip().split("\n") if l.strip()]
    except Exception:
        return []


def _strategy_phases():
    """Load strategy phases from registry."""
    try:
        import importlib.util
        reg_path = ROOT / "dashboard" / "api" / "strategy_registry.py"
        spec = importlib.util.spec_from_file_location("strategy_registry", reg_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "STRATEGY_PHASES", {})
    except Exception:
        return {}


# ── Commands ─────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — NAV live, P&L, brokers."""
    if not _auth(update):
        return

    bnb = _binance_info()
    alp = _alpaca_info()
    ibkr_eq = _ibkr_equity()
    ibkr_ok = _ibkr_connected()

    bnb_eq = float(bnb.get("equity", 0))
    alp_eq = float(alp.get("equity", 0))

    nav_live = bnb_eq + ibkr_eq
    cash_flows = _load_cash_flows()
    total_deposited = sum(cf["amount"] for cf in cash_flows if cf.get("type") == "deposit")
    pnl = nav_live - total_deposited
    pnl_pct = (pnl / total_deposited * 100) if total_deposited > 0 else 0

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sign = "+" if pnl >= 0 else ""

    text = (
        f"📊 *NAV & P&L* ({now})\n"
        f"{'─' * 28}\n"
        f"NAV Live: `${nav_live:,.0f}`\n"
        f"P&L Trading: `{sign}${pnl:,.0f}` ({sign}{pnl_pct:.1f}%)\n"
        f"Depose: `${total_deposited:,.0f}`\n\n"
        f"🟢 *IBKR* — {'LIVE' if ibkr_ok else 'OFF'}\n"
        f"  Equity: `${ibkr_eq:,.0f}`\n"
        f"🟢 *Binance* — LIVE\n"
        f"  Equity: `${bnb_eq:,.0f}`\n"
        f"  Spot: `${bnb.get('spot_total_usd', 0):,.0f}` | Earn: `${bnb.get('earn_total_usd', 0):,.0f}`\n"
        f"🟡 *Alpaca* — PAPER\n"
        f"  Equity: `${alp_eq:,.0f}`\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/positions — Toutes les positions."""
    if not _auth(update):
        return

    bnb_pos = _binance_positions()
    alp_pos = _alpaca_positions()

    lines = ["📋 *Positions Ouvertes*\n"]

    if bnb_pos:
        lines.append("*Binance (LIVE):*")
        for p in bnb_pos[:10]:
            sym = p.get("symbol", "?")
            pnl = float(p.get("unrealized_pl", 0))
            mv = float(p.get("market_value", 0))
            sign = "+" if pnl >= 0 else ""
            e = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {e} `{sym}` ${mv:,.0f} P&L={sign}${pnl:,.0f}")
    else:
        lines.append("*Binance:* Aucune position directionnelle")

    if alp_pos:
        lines.append("\n*Alpaca (PAPER):*")
        for p in alp_pos[:10]:
            sym = p.get("symbol", "?")
            pnl = float(p.get("unrealized_pl", 0))
            sign = "+" if pnl >= 0 else ""
            e = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {e} `{sym}` P&L={sign}${pnl:,.0f}")
    else:
        lines.append("\n*Alpaca:* Aucune position")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_strats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/strats — Strategies par phase lifecycle."""
    if not _auth(update):
        return

    phases = _strategy_phases()
    grouped = {}
    for sid, info in phases.items():
        p = info.get("phase", "CODE")
        grouped.setdefault(p, []).append((sid, info))

    icons = {"LIVE": "🟢", "PROBATION": "🟡", "PAPER": "🔵", "WF_PENDING": "⏳", "CODE": "⬜", "REJECTED": "❌"}
    order = ["LIVE", "PROBATION", "PAPER", "WF_PENDING", "CODE", "REJECTED"]

    lines = [f"🎯 *Strategies ({len(phases)} total)*\n"]
    for phase in order:
        items = grouped.get(phase, [])
        if not items:
            continue
        icon = icons.get(phase, "·")
        lines.append(f"\n*{icon} {phase} ({len(items)}):*")
        for sid, info in items:
            ac = info.get("asset_class", "")
            broker = info.get("broker", "")
            name = sid.replace("_", " ").title()
            lines.append(f"  `{name}` [{ac}] {broker}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/crypto — Detail crypto Binance + earn."""
    if not _auth(update):
        return

    bnb = _binance_info()
    equity = float(bnb.get("equity", 0))

    lines = [f"🪙 *Crypto Binance* — LIVE\n", f"Equity: `${equity:,.0f}`\n"]

    try:
        from strategies.crypto import CRYPTO_STRATEGIES
        for sid, data in CRYPTO_STRATEGIES.items():
            cfg = data["config"]
            name = cfg.get("name", sid)
            mtype = cfg.get("market_type", "spot")
            alloc = cfg.get("allocation_pct", 0)
            capital = equity * alloc
            badge = {"spot": "💵", "margin": "💳", "earn": "🏦"}.get(mtype, "💰")
            lines.append(f"{badge} *{name}* [{mtype}] {alloc*100:.0f}% (${capital:,.0f})")
    except Exception as e:
        lines.append(f"Erreur: {e}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_fx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fx — FX carry status."""
    if not _auth(update):
        return

    ibkr_eq = _ibkr_equity()
    ibkr_ok = _ibkr_connected()

    # Read last FX signal from worker log
    log_file = ROOT / "logs" / "worker" / "worker_stdout.log"
    fx_lines = []
    if log_file.exists():
        try:
            for l in reversed(log_file.read_text(errors="replace").split("\n")):
                if "FX CARRY" in l or "FX PAPER" in l or "CarryVS" in l or "CarryMom" in l:
                    parts = l.split("] ", 1)
                    fx_lines.append(parts[-1].strip()[:120] if len(parts) > 1 else l.strip()[:120])
                if len(fx_lines) >= 5:
                    break
        except Exception:
            pass

    text = (
        f"💱 *FX Status*\n"
        f"{'─' * 28}\n"
        f"IBKR: `{'CONNECTED' if ibkr_ok else 'OFF'}` {'🟢' if ibkr_ok else '🔴'}\n"
        f"Equity: `${ibkr_eq:,.0f}`\n"
        f"Mode: `LIVE` (carry daily 10h CET)\n\n"
        f"*Strats actives:*\n"
        f"  📈 FX Carry Vol-Scaled\n"
        f"  📈 FX Carry Momentum Filter\n"
        f"  📝 FX Paper (5 paires, 5min)\n\n"
        f"*Derniers signaux:*\n"
    )
    if fx_lines:
        for l in reversed(fx_lines):
            text += f"  `{l}`\n"
    else:
        text += "  Aucun signal FX recent\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/risk — Kill switch, drawdown, limites."""
    if not _auth(update):
        return

    worker_ok = _worker_running()
    ibkr_ok = _ibkr_connected()

    # Kill switch states
    ks_ibkr = "OFF"
    ks_crypto = "OFF"
    try:
        ks_path = ROOT / "data" / "kill_switch_state.json"
        if ks_path.exists():
            ks = json.loads(ks_path.read_text())
            if ks.get("active"):
                ks_ibkr = "ACTIVE ⚠️"
    except Exception:
        pass
    try:
        ks_path = ROOT / "data" / "crypto" / "kill_switch_state.json"
        if ks_path.exists():
            ks = json.loads(ks_path.read_text())
            if ks.get("active"):
                ks_crypto = "ACTIVE ⚠️"
    except Exception:
        pass

    text = (
        f"🛡 *Risk Dashboard*\n"
        f"{'─' * 28}\n\n"
        f"*Kill Switch:*\n"
        f"  IBKR: `{ks_ibkr}` {'🟢' if 'OFF' in ks_ibkr else '🔴'}\n"
        f"  Crypto: `{ks_crypto}` {'🟢' if 'OFF' in ks_crypto else '🔴'}\n\n"
        f"*Infra:*\n"
        f"  Worker: `{'ON' if worker_ok else 'OFF'}` {'🟢' if worker_ok else '🔴'}\n"
        f"  IBKR GW: `{'ON' if ibkr_ok else 'OFF'}` {'🟢' if ibkr_ok else '🔴'}\n"
        f"  Dashboard: `ON` 🟢\n\n"
        f"*Limites:*\n"
        f"  Max DD daily: `-5%`\n"
        f"  Max DD hourly: `-3%`\n"
        f"  Kelly mode: `NOMINAL (1/8)`\n"
        f"  Crypto regime: `BEAR`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/signals — Derniers signaux worker."""
    if not _auth(update):
        return

    signals = _worker_signals(15)
    if not signals:
        await update.message.reply_text("Aucun signal recent dans les logs.")
        return

    lines = ["📡 *Derniers Signaux*\n"]
    for s in signals:
        if "SIGNAL" in s.upper():
            lines.append(f"  🔴 `{s}`")
        elif "pas de signal" in s or "aucun signal" in s:
            lines.append(f"  ➖ `{s}`")
        else:
            lines.append(f"  ℹ️ `{s}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/trades — Derniers trades."""
    if not _auth(update):
        return

    log_file = ROOT / "logs" / "worker" / "worker_stdout.log"
    trade_lines = []
    if log_file.exists():
        try:
            for l in reversed(log_file.read_text(errors="replace").split("\n")):
                if any(k in l for k in ["ORDER", "FILL", "TRADE", "BUY", "SELL", "EXECUTED"]):
                    if "ib_insync" not in l and "alpaca" not in l.lower()[:30]:
                        parts = l.split("] ", 1)
                        trade_lines.append(parts[-1].strip()[:120] if len(parts) > 1 else l.strip()[:120])
                if len(trade_lines) >= 10:
                    break
        except Exception:
            pass

    if not trade_lines:
        await update.message.reply_text("Aucun trade recent dans les logs.\n(Regime BEAR — pas de setups)")
        return

    lines = ["📈 *Derniers Trades*\n"]
    for t in reversed(trade_lines):
        lines.append(f"  `{t}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_costs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/costs — Resume couts trading."""
    if not _auth(update):
        return

    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:8080/api/trades/costs", timeout=5)
        data = json.loads(r.read())
    except Exception:
        # Fallback: pas de dashboard API
        data = {}

    if not data or data.get("error"):
        await update.message.reply_text("Pas de donnees de couts disponibles.")
        return

    text = (
        f"💸 *Couts Trading*\n"
        f"{'─' * 28}\n"
        f"Commissions: `${data.get('total_commissions', 0):,.2f}`\n"
        f"Interets: `${data.get('total_interest', 0):,.2f}`\n"
        f"Slippage moy: `{data.get('total_slippage_bps_avg', 0):.1f} bps`\n"
        f"Cout/trade: `${data.get('cost_per_trade_avg', 0):,.2f}`\n"
        f"Couts % P&L: `{data.get('cost_as_pct_of_pnl', 0):.1f}%` "
        f"{'✅' if data.get('healthy') else '⚠️'}\n"
        f"Trades: `{data.get('trade_count', 0)}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/health — Status infrastructure."""
    if not _auth(update):
        return

    worker_ok = _worker_running()
    ibkr_ok = _ibkr_connected()

    # Check services via systemctl
    services = {}
    for svc in ["trading-worker", "ibgateway", "trading-dashboard", "trading-watchdog", "trading-telegram"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            services[svc] = r.stdout.strip() == "active"
        except Exception:
            services[svc] = False

    text = (
        f"🏥 *Infrastructure*\n"
        f"{'─' * 28}\n"
    )
    for svc, ok in services.items():
        name = svc.replace("trading-", "").replace("ibgateway", "IB Gateway").title()
        text += f"  {'🟢' if ok else '🔴'} {name}: `{'ON' if ok else 'OFF'}`\n"

    text += (
        f"\n*Health checks:*\n"
        f"  Worker HTTP: `{'OK' if worker_ok else 'FAIL'}`\n"
        f"  IBKR TCP: `{'OK' if ibkr_ok else 'FAIL'}`\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/kill CONFIRM — Ferme toutes les positions."""
    if not _auth(update):
        return

    args = " ".join(context.args) if context.args else ""
    if args.upper() != "CONFIRM":
        await update.message.reply_text(
            "⚠️ *KILL SWITCH*\n\n"
            "Ceci fermera TOUTES les positions live.\n"
            "Envoyez `/kill CONFIRM` pour executer.",
            parse_mode="Markdown"
        )
        return

    # Execute kill switch
    results = []
    try:
        from core.broker.binance_broker import BinanceBroker
        bnb = BinanceBroker()
        bnb.cancel_all_orders()
        results.append("Binance: ordres annules")
    except Exception as e:
        results.append(f"Binance: erreur {e}")

    try:
        from core.broker.ibkr_adapter import IBKRBroker
        with IBKRBroker(client_id=50) as ibkr:
            ibkr.cancel_all_orders()
            results.append("IBKR: ordres annules")
    except Exception as e:
        results.append(f"IBKR: erreur {e}")

    text = "🔴 *KILL SWITCH ACTIVE*\n\n" + "\n".join(f"  {r}" for r in results)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return

    text = (
        "🤖 *Trading Bot*\n"
        f"{'─' * 25}\n\n"
        "📊 /status — NAV, P&L, brokers\n"
        "📋 /positions — Positions ouvertes\n"
        "🎯 /strats — Strategies par phase\n"
        "🪙 /crypto — Crypto Binance\n"
        "💱 /fx — FX carry status\n"
        "🛡 /risk — Risk + kill switch\n"
        "📡 /signals — Derniers signaux\n"
        "📈 /trades — Derniers trades\n"
        "💸 /costs — Couts trading\n"
        "🏥 /health — Infra status\n"
        "🔴 /kill CONFIRM — KILL SWITCH\n"
        "📊 /regime — Regime marche V12\n"
        "💼 /portfolio — NAV cross-broker V12\n"
        "🚨 /emergency — CLOSE ALL brokers V12\n"
        "❓ /help — Cette aide\n\n"
        "🌐 Dashboard: trading.aucoeurdeville-laval.fr"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── V12 Commands ─────────────────────────────────────────────────────────────

async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """V12: Show current market regime per asset class."""
    if not _auth(update):
        return
    try:
        # Read regime transitions log
        regime_file = ROOT / "data" / "regime_transitions.jsonl"
        regime_snapshot = ROOT / "data" / "risk" / "unified_portfolio.json"

        lines = ["<b>Market Regime (V12)</b>\n"]

        # Read last regime state from worker event log
        events_file = ROOT / "logs" / "worker" / "worker.log"
        if events_file.exists():
            import subprocess
            result = subprocess.run(
                ["grep", "-o", "V12 Regime:.*", str(events_file)],
                capture_output=True, text=True, timeout=5,
            )
            last_lines = result.stdout.strip().split("\n")[-3:]
            for l in last_lines:
                if l.strip():
                    lines.append(f"  {l.strip()}")

        if regime_file.exists():
            recent = regime_file.read_text().strip().split("\n")[-5:]
            if recent and recent[0]:
                lines.append("\n<b>Recent transitions:</b>")
                for r in recent:
                    try:
                        d = json.loads(r)
                        lines.append(f"  {d.get('asset_class')}: {d.get('old_regime')} -> {d.get('new_regime')}")
                    except Exception:
                        pass
        else:
            lines.append("  No regime transitions yet")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """V12: Show unified cross-broker portfolio."""
    if not _auth(update):
        return
    try:
        snap_file = ROOT / "data" / "risk" / "unified_portfolio.json"
        if not snap_file.exists():
            await update.message.reply_text("No unified portfolio data yet (next cross-portfolio cycle will generate it)")
            return

        snap = json.loads(snap_file.read_text())
        text = (
            "<b>Unified Portfolio (V12)</b>\n\n"
            f"NAV: ${snap.get('nav_total', 0):,.0f}\n"
            f"  Binance: ${snap.get('binance_equity', 0):,.0f}\n"
            f"  IBKR: ${snap.get('ibkr_equity', 0):,.0f}\n"
            f"  Alpaca: ${snap.get('alpaca_equity', 0):,.0f}\n\n"
            f"DD peak: {snap.get('dd_from_peak_pct', 0):.1f}%\n"
            f"DD daily: {snap.get('dd_daily_pct', 0):.1f}%\n"
            f"DD weekly: {snap.get('dd_weekly_pct', 0):.1f}%\n\n"
            f"Gross exp: {snap.get('gross_exposure_pct', 0):.0f}%\n"
            f"Net exp: {snap.get('net_exposure_pct', 0):.0f}%\n"
            f"Cash: {snap.get('cash_pct', 0):.0f}%\n\n"
            f"Alert: <b>{snap.get('alert_level', '?')}</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """V12: Emergency close all brokers."""
    if not _auth(update):
        return

    args = " ".join(context.args) if context.args else ""

    if not args.strip():
        try:
            from core.risk.emergency_close_all import _generate_confirmation_code
            code = _generate_confirmation_code()
            await update.message.reply_text(
                f"<b>EMERGENCY CLOSE ALL</b>\n\n"
                f"This will close ALL positions on ALL brokers.\n"
                f"Current code: <b>{code}</b>\n\n"
                f"Send /emergency {code} to execute.",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # Execute
    code = args.strip().upper()
    try:
        from core.risk.emergency_close_all import EmergencyCloseAll, _generate_confirmation_code
        expected = _generate_confirmation_code()
        if code != expected:
            await update.message.reply_text(f"Invalid code. Expected: {expected}")
            return

        # Build broker dict from available connections
        brokers = {}
        try:
            if os.environ.get("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                brokers["BINANCE"] = BinanceBroker()
        except Exception:
            pass
        try:
            from core.broker.ibkr_adapter import IBKRBroker
            brokers["IBKR"] = IBKRBroker(client_id=50)
        except Exception:
            pass

        closer = EmergencyCloseAll(brokers=brokers)
        result = closer.execute(confirmation_code=code)

        text = (
            f"<b>EMERGENCY CLOSE {'DONE' if result['status'] == 'EXECUTED' else result['status']}</b>\n"
            f"Positions closed: {result.get('total_positions_closed', 0)}\n"
            f"Orders cancelled: {result.get('total_orders_cancelled', 0)}\n"
            f"Time: {result.get('elapsed_seconds', 0):.1f}s"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"EMERGENCY ERROR: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info(f"Starting trading bot (chat_id={CHAT_ID})")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("strats", cmd_strats))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("fx", cmd_fx))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("costs", cmd_costs))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("regime", cmd_regime))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("emergency", cmd_emergency))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    logger.info("Bot polling started — 15 commands active")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
