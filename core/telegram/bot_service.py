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
  /restart_ibgw — Restart IB Gateway (live + paper)
  /kill CONFIRM — KILL SWITCH (ferme tout)
  /help       — Commandes
"""
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram-bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

from telegram.ext import Application, CommandHandler, ContextTypes

from telegram import Update


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
    """Load strategy phases from canonical quant_registry.yaml.

    Source de verite (plan 9.0 B2) : config/quant_registry.yaml.
    L'ancien dashboard/api/strategy_registry.py est obsolete (code registry drift).

    Returns dict {strategy_id: {phase, broker, asset_class, grade}} avec phase
    mappee depuis status canonique :
      live_core    -> LIVE
      live_micro   -> LIVE_MICRO
      live_probation -> PROBATION
      paper_only   -> PAPER
      paper_retrospective -> PAPER
      frozen       -> FROZEN
      disabled     -> DISABLED
      REJECTED     -> REJECTED
    """
    try:
        import yaml
        path = ROOT / "config" / "quant_registry.yaml"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}

    BOOK_TO_BROKER = {
        "ibkr_futures": "IBKR",
        "ibkr_eu":      "IBKR",
        "ibkr_fx":      "IBKR",
        "binance_crypto": "BINANCE",
        "alpaca_us":    "ALPACA",
    }
    BOOK_TO_ASSET = {
        "ibkr_futures": "FUTURES",
        "ibkr_eu":      "EU",
        "ibkr_fx":      "FX",
        "binance_crypto": "CRYPTO",
        "alpaca_us":    "US",
    }
    STATUS_TO_PHASE = {
        "live_core":           "LIVE",
        "live_probation":      "PROBATION",
        "live_micro":          "LIVE_MICRO",
        "paper_only":          "PAPER",
        "paper_retrospective": "PAPER",
        "frozen":              "FROZEN",
        "disabled":            "DISABLED",
        "keep_research":       "RESEARCH",
        "REJECTED":            "REJECTED",
    }

    out = {}
    for s in data.get("strategies", []) or []:
        sid = s.get("strategy_id")
        if not sid:
            continue
        book = s.get("book", "")
        # Skip meta-orchestrators (us_stocks_daily) qui ne sont pas des strats canoniques
        if s.get("is_canonical_strategy") is False:
            continue
        out[sid] = {
            "phase": STATUS_TO_PHASE.get(s.get("status"), s.get("status", "UNKNOWN")),
            "broker": BOOK_TO_BROKER.get(book, book.upper()),
            "asset_class": BOOK_TO_ASSET.get(book, ""),
            "grade": s.get("grade") or "",
            "is_live": bool(s.get("is_live")),
        }
    return out


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

    now = datetime.now(UTC).strftime("%H:%M UTC")
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


def _ibkr_positions_via_insync(port: int | None = None, client_id: int = 98) -> list[dict]:
    """Read IBKR positions directement via ib_insync (gere stocks/FX/futures).

    Inclut SL/TP broker-side via ib.openTrades() correle par localSymbol.
    """
    import asyncio
    try:
        from ib_insync import IB
    except ImportError:
        return []
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("IBKR_PORT", "4002"))

    ib = IB()
    ib.RequestTimeout = 15
    out = []
    try:
        ib.connect(host, port, clientId=client_id, timeout=10)

        # 1. Pre-build SL/TP map keyed by localSymbol from openTrades.
        # IBKR brackets are STP (stop) + LMT (take-profit) child orders.
        sl_tp_by_sym: dict[str, dict] = {}
        try:
            for trade in ib.openTrades():
                ctr = trade.contract
                sym = getattr(ctr, "localSymbol", None) or ctr.symbol
                order = trade.order
                otype = getattr(order, "orderType", "")
                # STP / STP LMT -> stop loss
                if otype in ("STP", "STP LMT", "TRAIL"):
                    px = getattr(order, "auxPrice", 0) or getattr(order, "trailStopPrice", 0)
                    sl_tp_by_sym.setdefault(sym, {})["sl"] = float(px) if px else None
                # LMT -> take profit (uniquement les enfants brackets, pas les market orders)
                elif otype == "LMT" and getattr(order, "parentId", 0):
                    sl_tp_by_sym.setdefault(sym, {})["tp"] = float(order.lmtPrice or 0) or None
        except Exception:
            pass

        # 2. Build positions list, joining SL/TP map.
        for item in ib.portfolio():
            if item.position == 0:
                continue
            c = item.contract
            sym = getattr(c, "localSymbol", None) or c.symbol
            tp_sl = sl_tp_by_sym.get(sym, {})
            out.append({
                "symbol": sym,
                "qty": float(item.position),
                "market_value": float(item.marketValue),
                "unrealized_pl": float(item.unrealizedPNL),
                "avg_cost": float(item.averageCost),
                "sl": tp_sl.get("sl"),
                "tp": tp_sl.get("tp"),
            })
    except Exception:
        pass
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return out


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/positions — Toutes les positions (IBKR live + Binance + Alpaca paper)."""
    if not _auth(update):
        return

    ibkr_pos = _ibkr_positions_via_insync()
    bnb_pos = _binance_positions()
    alp_pos = _alpaca_positions()

    lines = ["📋 *Positions Ouvertes*\n"]

    paper_mode = os.environ.get("IBKR_PAPER", "false").lower() == "true"
    ibkr_label = "PAPER" if paper_mode else "LIVE"
    if ibkr_pos:
        lines.append(f"*IBKR ({ibkr_label}):*")
        for p in ibkr_pos[:10]:
            sym = p["symbol"]
            qty = p["qty"]
            pnl = p["unrealized_pl"]
            mv = p["market_value"]
            sl = p.get("sl")
            tp = p.get("tp")
            sign = "+" if pnl >= 0 else ""
            e = "🟢" if pnl >= 0 else "🔴"
            sl_tp_str = ""
            if sl or tp:
                parts = []
                if sl:
                    parts.append(f"SL=${sl:,.2f}")
                if tp:
                    parts.append(f"TP=${tp:,.2f}")
                sl_tp_str = f" [{' '.join(parts)}]"
            else:
                sl_tp_str = " [⚠️ NO BRACKET]"
            lines.append(f"  {e} `{sym}` qty={qty:+.0f} ${mv:,.0f} P&L={sign}${pnl:,.0f}{sl_tp_str}")
    else:
        lines.append(f"*IBKR ({ibkr_label}):* Aucune position (ou Gateway down)")

    if bnb_pos:
        lines.append("\n*Binance (LIVE):*")
        for p in bnb_pos[:10]:
            sym = p.get("symbol", "?")
            pnl = float(p.get("unrealized_pl", 0))
            mv = float(p.get("market_value", 0))
            sign = "+" if pnl >= 0 else ""
            e = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {e} `{sym}` ${mv:,.0f} P&L={sign}${pnl:,.0f}")
    else:
        lines.append("\n*Binance:* Aucune position directionnelle")

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
    """/strats — Strategies par phase lifecycle (source: quant_registry.yaml)."""
    if not _auth(update):
        return

    phases = _strategy_phases()
    if not phases:
        await update.message.reply_text(
            "Erreur: impossible de lire config/quant_registry.yaml. "
            "Verifier le fichier sur le serveur."
        )
        return

    grouped = {}
    for sid, info in phases.items():
        p = info.get("phase", "UNKNOWN")
        grouped.setdefault(p, []).append((sid, info))

    icons = {
        "LIVE":       "🟢",
        "LIVE_MICRO": "🟡",
        "PROBATION":  "🟡",
        "PAPER":      "🔵",
        "FROZEN":     "🧊",
        "DISABLED":   "⚪",
        "RESEARCH":   "🔬",
        "REJECTED":   "❌",
    }
    order = ["LIVE", "LIVE_MICRO", "PROBATION", "PAPER", "FROZEN", "DISABLED", "RESEARCH", "REJECTED"]

    lines = [f"🎯 *Strategies ({len(phases)} canoniques)*\n"]
    for phase in order:
        items = grouped.get(phase, [])
        if not items:
            continue
        icon = icons.get(phase, "·")
        lines.append(f"\n*{icon} {phase} ({len(items)}):*")
        for sid, info in items:
            ac = info.get("asset_class", "")
            broker = info.get("broker", "")
            grade = info.get("grade", "")
            grade_s = f" [{grade}]" if grade else ""
            lines.append(f"  `{sid}` {ac}/{broker}{grade_s}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/crypto — Detail crypto Binance + earn."""
    if not _auth(update):
        return

    bnb = _binance_info()
    equity = float(bnb.get("equity", 0))

    lines = ["🪙 *Crypto Binance* — LIVE\n", f"Equity: `${equity:,.0f}`\n"]

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
    """/risk — Kill switch, drawdown numerique, limites + reason si KS actif."""
    if not _auth(update):
        return

    worker_ok = _worker_running()
    ibkr_ok = _ibkr_connected()

    # Kill switch states (avec reason + age)
    ks_ibkr_status = "OFF"
    ks_ibkr_detail = ""
    ks_crypto_status = "OFF"
    ks_crypto_detail = ""
    try:
        ks_path = ROOT / "data" / "kill_switch_state.json"
        if ks_path.exists():
            ks = json.loads(ks_path.read_text())
            if ks.get("active"):
                ks_ibkr_status = "ACTIVE ⚠️"
                reason = ks.get("activation_reason", "?")
                since = ks.get("activated_at", "?")
                ks_ibkr_detail = f" since {since[:16]} ({reason})"
    except Exception:
        pass
    try:
        ks_path = ROOT / "data" / "crypto_kill_switch_state.json"
        if ks_path.exists():
            ks = json.loads(ks_path.read_text())
            if ks.get("active"):
                ks_crypto_status = "ACTIVE ⚠️"
                ks_crypto_detail = f" ({ks.get('reason', '?')})"
    except Exception:
        pass

    # DD numerique depuis unified_portfolio.json
    dd_lines = []
    try:
        snap_path = ROOT / "data" / "risk" / "unified_portfolio.json"
        if snap_path.exists():
            snap = json.loads(snap_path.read_text())
            dd_lines.append(f"  DD daily: `{snap.get('dd_daily_pct', 0):+.2f}%`")
            dd_lines.append(f"  DD weekly: `{snap.get('dd_weekly_pct', 0):+.2f}%`")
            dd_lines.append(f"  DD peak: `{snap.get('dd_from_peak_pct', 0):+.2f}%`")
            alert = snap.get("alert_level", "?")
            alert_icon = "🟢" if alert in ("OK", "NOMINAL") else ("🟡" if alert == "DEFENSIVE" else "🔴")
            dd_lines.append(f"  Alert: `{alert}` {alert_icon}")
    except Exception:
        dd_lines.append("  DD: (snapshot indisponible)")

    text = (
        f"🛡 *Risk Dashboard*\n"
        f"{'─' * 28}\n\n"
        f"*Kill Switch:*\n"
        f"  IBKR: `{ks_ibkr_status}`{ks_ibkr_detail} {'🟢' if 'OFF' in ks_ibkr_status else '🔴'}\n"
        f"  Crypto: `{ks_crypto_status}`{ks_crypto_detail} {'🟢' if 'OFF' in ks_crypto_status else '🔴'}\n\n"
        f"*Drawdown (NAV cross-broker):*\n"
        + "\n".join(dd_lines)
        + f"\n\n*Infra:*\n"
        f"  Worker: `{'ON' if worker_ok else 'OFF'}` {'🟢' if worker_ok else '🔴'}\n"
        f"  IBKR GW: `{'ON' if ibkr_ok else 'OFF'}` {'🟢' if ibkr_ok else '🔴'}\n\n"
        f"*Seuils kill switch:*\n"
        f"  daily -5% / hourly -3% / 5d -8% / monthly -12%"
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
    """/health — Status infrastructure + cycle health + brokers."""
    if not _auth(update):
        return

    from datetime import datetime
    now = datetime.now().strftime("%d/%m %H:%M")

    worker_ok = _worker_running()
    ibkr_ok = _ibkr_connected()

    # Check services via systemctl
    services = {}
    for svc in ["trading-worker", "ibgateway", "ibgateway-paper", "trading-dashboard", "trading-watchdog", "trading-telegram"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            services[svc] = r.stdout.strip() == "active"
        except Exception:
            services[svc] = False

    # Verifier directement les ports IB (4002 live, 4003 paper) car le service
    # peut etre 'active' mais l'app Java pas encore listening (2FA pending matin).
    import socket as _sk
    def _port_open(port):
        try:
            with _sk.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except Exception:
            return False
    ibgw_live_port = _port_open(4002)
    ibgw_paper_port = _port_open(4003)

    text = f"System Health — {now}\n\n"

    # --- Cycle health from metrics ---
    try:
        from dashboard.api.routes.cycles import get_cycles_health
        health = get_cycles_health()
        if health.get("cycles"):
            text += "Cycles:\n"
            for name, info in health["cycles"].items():
                h = info.get("health", "UNKNOWN")
                icon = "OK" if h == "HEALTHY" else ("!!" if h == "FAILED" else "?")
                avg = info.get("avg_duration_seconds", 0)
                runs = info.get("total_runs_24h", 0)
                fails = info.get("total_failures_24h", 0)
                trend = info.get("trend", "")
                trend_txt = f" {trend}" if trend not in ("STABLE", "") else ""
                text += f"  [{icon}] {name} {avg:.1f}s avg ({runs} runs, {fails} fail){trend_txt}\n"

            sys = health.get("system", {})
            if sys.get("cpu_percent"):
                text += f"\nSystem: CPU {sys['cpu_percent']:.0f}%, RAM {sys['ram_percent']:.0f}%, Disk {sys['disk_percent']:.0f}%\n"
    except Exception:
        pass

    # --- Anomalies 24h ---
    try:
        from core.monitoring.anomaly_detector import AnomalyDetector
        from core.monitoring.metrics_pipeline import get_metrics
        detector = AnomalyDetector(get_metrics())
        anomalies = detector.get_recent_anomalies(hours=24)
        if anomalies:
            text += f"\nAnomalies 24h: {len(anomalies)}\n"
            for a in anomalies[-3:]:
                text += f"  {a.detected_at.strftime('%H:%M')} {a.message[:60]}\n"
    except Exception:
        pass

    text += f"\nServices:\n"
    _service_labels = {
        "trading-worker": "Worker",
        "ibgateway": "IB Gateway LIVE",
        "ibgateway-paper": "IB Gateway PAPER",
        "trading-dashboard": "Dashboard",
        "trading-watchdog": "Watchdog",
        "trading-telegram": "Telegram bot",
    }
    for svc, ok in services.items():
        name = _service_labels.get(svc, svc)
        text += f"  [{'ON' if ok else 'OFF'}] {name}\n"

    text += (
        f"\nIBKR Gateway ports:\n"
        f"  4002 LIVE : {'LISTEN' if ibgw_live_port else 'closed (2FA pending?)'}\n"
        f"  4003 PAPER: {'LISTEN' if ibgw_paper_port else 'closed'}\n"
        f"\nHealth checks:\n"
        f"  Worker HTTP: {'OK' if worker_ok else 'FAIL'}\n"
        f"  IBKR (env port): {'OK' if ibkr_ok else 'FAIL'}\n"
    )
    await update.message.reply_text(f"```\n{text}```", parse_mode="Markdown")


def _ibkr_close_all_via_insync(port: int | None = None, client_id: int = 99) -> dict:
    """Close ALL IBKR positions via ib_insync direct (works for stocks/FX/futures).

    Bypasses BaseBroker adapters which only know Stock/Forex contracts.
    Iterates ib.positions() and sends MarketOrder opposite side for each
    contract — works for Stock, Forex, Future, etc.

    Returns: {"closed": int, "cancelled": int, "errors": list[str], "status": str}
    """
    import asyncio
    result = {"closed": 0, "cancelled": 0, "errors": [], "status": "OK"}

    try:
        from ib_insync import IB, MarketOrder
    except ImportError:
        result["status"] = "ERROR"
        result["errors"].append("ib_insync not installed")
        return result

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("IBKR_PORT", "4002"))

    ib = IB()
    ib.RequestTimeout = 30
    try:
        ib.connect(host, port, clientId=client_id, timeout=20)
    except Exception as e:
        result["status"] = "ERROR"
        result["errors"].append(f"connect {host}:{port} cid={client_id}: {e}")
        return result

    try:
        # 1. Cancel ALL open orders FIRST (avoid SL/TP triggering pendant close)
        try:
            open_orders = list(ib.openOrders())
        except Exception as e:
            open_orders = []
            result["errors"].append(f"openOrders query: {e}")
        for order in open_orders:
            try:
                ib.cancelOrder(order)
                result["cancelled"] += 1
            except Exception as e:
                result["errors"].append(f"cancel order {getattr(order, 'orderId', '?')}: {e}")

        # 2. Close ALL positions via MarketOrder oppose
        try:
            positions = list(ib.positions())
        except Exception as e:
            positions = []
            result["errors"].append(f"positions query: {e}")
        for pos in positions:
            try:
                qty = abs(float(pos.position))
                if qty == 0:
                    continue
                side = "SELL" if pos.position > 0 else "BUY"
                contract = pos.contract
                order = MarketOrder(side, qty)
                # outsideRth=True pour futures night session, harmless sur stocks RTH
                order.outsideRth = True
                ib.placeOrder(contract, order)
                result["closed"] += 1
            except Exception as e:
                sym = getattr(getattr(pos, "contract", None), "localSymbol", "?")
                result["errors"].append(f"close {sym}: {e}")

        # 3. Attendre brievement les fills (sans bloquer trop longtemps)
        ib.sleep(3)

    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    if result["errors"]:
        result["status"] = "PARTIAL" if (result["closed"] > 0 or result["cancelled"] > 0) else "FAILED"
    return result


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/kill CONFIRM — Kill switch: ferme TOUTES positions + persiste l'etat.

    Ordre:
      1. Ferme positions IBKR (ib_insync direct, gere stocks/FX/futures).
      2. Ferme positions Binance (BinanceBroker.close_position).
      3. Active les kill switches (apres tentatives, pour bloquer le re-entry
         meme si certaines fermetures ont echoue).
      4. Rapport detaille avec status FAILED/PARTIAL/OK.
    """
    if not _auth(update):
        return

    args = " ".join(context.args) if context.args else ""
    if args.upper() != "CONFIRM":
        await update.message.reply_text(
            "⚠️ *KILL SWITCH*\n\n"
            "Ceci va:\n"
            "1. Fermer TOUTES les positions IBKR + Binance\n"
            "2. Annuler TOUS les ordres ouverts (SL/TP brackets)\n"
            "3. Activer les kill switches (bloque nouveaux trades)\n\n"
            "Envoyez `/kill CONFIRM` pour executer.",
            parse_mode="Markdown"
        )
        return

    lines = []
    overall_ok = True

    # 1. IBKR via ib_insync direct (port 4002 live par defaut, override IBKR_PORT)
    try:
        ibkr = _ibkr_close_all_via_insync()
        lines.append(
            f"IBKR [{ibkr['status']}]: {ibkr['closed']} pos closed, "
            f"{ibkr['cancelled']} orders cancelled"
        )
        if ibkr["errors"]:
            lines.append(f"  IBKR errors: {len(ibkr['errors'])} (premieres: "
                        + "; ".join(ibkr['errors'][:3]) + ")")
            overall_ok = False
    except Exception as e:
        lines.append(f"IBKR [EXCEPTION]: {e}")
        overall_ok = False

    # 2. Binance positions
    bnb_closed = 0
    bnb_errors = []
    try:
        from core.broker.binance_broker import BinanceBroker
        bnb = BinanceBroker()
        try:
            cancelled = bnb.cancel_all_orders(_authorized_by="telegram_kill")
        except Exception as e:
            cancelled = 0
            bnb_errors.append(f"cancel_all: {e}")
        try:
            positions = bnb.get_positions()
        except Exception as e:
            positions = []
            bnb_errors.append(f"get_positions: {e}")
        for pos in positions:
            sym = pos.get("symbol", "?")
            try:
                bnb.close_position(sym, _authorized_by="telegram_kill")
                bnb_closed += 1
            except Exception as e:
                bnb_errors.append(f"close {sym}: {e}")
        status = "OK" if not bnb_errors else ("PARTIAL" if bnb_closed > 0 else "FAILED")
        lines.append(f"Binance [{status}]: {bnb_closed} pos closed, {cancelled} orders cancelled")
        if bnb_errors:
            lines.append(f"  Binance errors: {len(bnb_errors)} (premieres: "
                        + "; ".join(bnb_errors[:3]) + ")")
            overall_ok = False
    except Exception as e:
        lines.append(f"Binance [EXCEPTION]: {e}")
        overall_ok = False

    # 3. Activer les kill switches APRES tentatives (block re-entry)
    try:
        from core.kill_switch_live import LiveKillSwitch
        ks = LiveKillSwitch()
        if not ks.is_active:
            ks.activate(reason="operator_telegram_kill", trigger_type="MANUAL")
        lines.append("Kill switch IBKR: ACTIVE")
    except Exception as e:
        lines.append(f"Kill switch IBKR: {e}")
        overall_ok = False
    try:
        from core.crypto.risk_manager_crypto import CryptoKillSwitch
        cks = CryptoKillSwitch()
        cks._activate("operator_telegram_kill")
        lines.append("Kill switch crypto: ACTIVE")
    except Exception as e:
        lines.append(f"Kill switch crypto: {e}")
        overall_ok = False

    # 4. Rapport
    header = "🔴 *KILL SWITCH ACTIVE*" if overall_ok else "🟠 *KILL SWITCH PARTIEL*"
    text = header + "\n\n" + "\n".join(f"  {r}" for r in lines)
    if not overall_ok:
        text += ("\n\n⚠️ *VERIFIER MANUELLEMENT* dans TWS / Binance que toutes "
                 "les positions sont fermees. Le kill switch bloque les nouveaux "
                 "ordres mais des fermetures ont peut-etre echoue.")
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_restart_ibgw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/restart_ibgw — Redemarrer IB Gateway (live + paper) sans intervention."""
    if not _auth(update):
        return

    import socket

    results = []
    for svc, port, label in [
        ("ibgateway", 4002, "LIVE"),
        ("ibgateway-paper", 4003, "PAPER"),
    ]:
        try:
            r = subprocess.run(
                ["systemctl", "restart", svc],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                results.append(f"{label}: restart OK")
            else:
                results.append(f"{label}: restart FAILED ({r.stderr.strip()[:80]})")
        except Exception as e:
            results.append(f"{label}: {e}")

    # Attendre que les ports soient up
    await update.message.reply_text(
        "IB Gateway restart en cours... verification dans 15s",
        parse_mode="Markdown",
    )
    import asyncio
    await asyncio.sleep(15)

    for port, label in [(4002, "LIVE"), (4003, "PAPER")]:
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        try:
            with socket.create_connection((host, port), timeout=5):
                results.append(f"{label} port {port}: OK")
        except Exception:
            results.append(f"{label} port {port}: FAIL")

    text = "*IB Gateway Restart*\n\n" + "\n".join(f"  {r}" for r in results)
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
        "🔄 /restart\\_ibgw — Restart IB Gateway\n"
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

    # Execute — meme logique que /kill mais avec confirmation code TOTP-like
    code = args.strip().upper()
    try:
        from core.risk.emergency_close_all import _generate_confirmation_code
        expected = _generate_confirmation_code()
        if code != expected:
            await update.message.reply_text("Invalid confirmation code.")
            return
    except Exception as e:
        await update.message.reply_text(f"EMERGENCY ERROR (code check): {e}")
        return

    lines = []
    overall_ok = True

    # 1. IBKR via ib_insync direct
    try:
        ibkr = _ibkr_close_all_via_insync()
        lines.append(
            f"IBKR [{ibkr['status']}]: {ibkr['closed']} pos closed, "
            f"{ibkr['cancelled']} orders cancelled"
        )
        if ibkr["errors"]:
            lines.append(f"  IBKR errors: {len(ibkr['errors'])} (premieres: "
                        + "; ".join(ibkr['errors'][:3]) + ")")
            overall_ok = False
    except Exception as e:
        lines.append(f"IBKR [EXCEPTION]: {e}")
        overall_ok = False

    # 2. Binance
    bnb_closed = 0
    bnb_errors = []
    try:
        if os.environ.get("BINANCE_API_KEY"):
            from core.broker.binance_broker import BinanceBroker
            bnb = BinanceBroker()
            try:
                cancelled = bnb.cancel_all_orders(_authorized_by="telegram_emergency")
            except Exception as e:
                cancelled = 0
                bnb_errors.append(f"cancel_all: {e}")
            try:
                positions = bnb.get_positions()
            except Exception as e:
                positions = []
                bnb_errors.append(f"get_positions: {e}")
            for pos in positions:
                sym = pos.get("symbol", "?")
                try:
                    bnb.close_position(sym, _authorized_by="telegram_emergency")
                    bnb_closed += 1
                except Exception as e:
                    bnb_errors.append(f"close {sym}: {e}")
            status = "OK" if not bnb_errors else ("PARTIAL" if bnb_closed > 0 else "FAILED")
            lines.append(f"Binance [{status}]: {bnb_closed} pos closed, {cancelled} orders cancelled")
            if bnb_errors:
                lines.append(f"  Binance errors: {len(bnb_errors)}")
                overall_ok = False
        else:
            lines.append("Binance: skip (no API key)")
    except Exception as e:
        lines.append(f"Binance [EXCEPTION]: {e}")
        overall_ok = False

    # 3. Active kill switches (block re-entry)
    try:
        from core.kill_switch_live import LiveKillSwitch
        LiveKillSwitch().activate(reason="emergency_LEVEL_3", trigger_type="EMERGENCY")
        lines.append("Kill switch IBKR: ACTIVE")
    except Exception as e:
        lines.append(f"Kill switch IBKR: {e}")
        overall_ok = False
    try:
        from core.crypto.risk_manager_crypto import CryptoKillSwitch
        CryptoKillSwitch()._activate("emergency_LEVEL_3")
        lines.append("Kill switch crypto: ACTIVE")
    except Exception as e:
        lines.append(f"Kill switch crypto: {e}")
        overall_ok = False

    header = "🚨 <b>EMERGENCY CLOSE EXECUTED</b>" if overall_ok else "🟠 <b>EMERGENCY CLOSE PARTIEL</b>"
    text = header + "\n\n" + "\n".join(lines)
    if not overall_ok:
        text += ("\n\n⚠️ VERIFIER MANUELLEMENT TWS / Binance — fermetures partielles. "
                 "Kill switches bloquent les nouveaux ordres.")
    await update.message.reply_text(text, parse_mode="HTML")


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
    app.add_handler(CommandHandler("restart_ibgw", cmd_restart_ibgw))
    app.add_handler(CommandHandler("regime", cmd_regime))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("emergency", cmd_emergency))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    logger.info("Bot polling started — 15 commands active")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
