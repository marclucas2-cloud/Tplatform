"""
Worker Railway — scheduler 24/7 pour le paper trading.

Remplace les crons Windows (schtasks) par un process Python permanent.
Execute :
  - Daily portfolio (3 strategies) a 15:35 Paris
  - Intraday strategies (7 strategies) toutes les 5 min, 15:35-22:00 Paris
"""
import os
import signal
import sys
import time
import json
import logging
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path
import zoneinfo

# Setup paths
ROOT = Path(__file__).parent
# ROOT doit etre AVANT intraday-backtesterV2 dans sys.path
# sinon intraday-backtesterV2/strategies/ masque strategies/crypto/
sys.path.insert(0, str(ROOT / "intraday-backtesterV2"))
sys.path.insert(0, str(ROOT))

# Charger .env si present (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # En prod Railway, les vars sont dans l'environnement

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("worker")

# Add file handler for log persistence (Railway has ~24h retention on stdout)
log_dir = Path(__file__).parent / "logs" / "worker"
log_dir.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(
    log_dir / "worker.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))
logging.getLogger().addHandler(file_handler)


# --- Graceful shutdown handler ---
def _handle_sigterm(signum, frame):
    """Graceful shutdown on Railway redeploy."""
    logger.critical("SIGTERM received — graceful shutdown initiated")
    _send_alert("Worker SIGTERM — shutting down gracefully", level="warning")
    # Let the scheduler shut down gracefully
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def _send_alert(message: str, level: str = "info"):
    """Unified alert: try LiveAlertManager first, fallback to legacy."""
    try:
        from core.alerting_live import LiveAlertManager
        mgr = LiveAlertManager.get_instance()
        if mgr:
            mgr.send(message, level=level)
            return
    except Exception:
        pass
    try:
        from core.telegram_alert import send_alert
        send_alert(message, level=level)
    except Exception:
        pass


# Timezone
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
ET = zoneinfo.ZoneInfo("America/New_York")

# Horaires
DAILY_HOUR = 15
DAILY_MINUTE = 35
INTRADAY_START_HOUR = 15
INTRADAY_START_MINUTE = 35
INTRADAY_END_HOUR = 22
INTRADAY_END_MINUTE = 0
INTRADAY_INTERVAL_SECONDS = 300  # 5 min

# EU market hours (09:00-17:30 CET)
EU_START_HOUR = 9
EU_START_MINUTE = 0
EU_END_HOUR = 17
EU_END_MINUTE = 30

# Live risk cycle interval (same as intraday: 5 min)
LIVE_RISK_INTERVAL_SECONDS = 300

# Crypto cycle interval (24/7, every 15 min)
CRYPTO_INTERVAL_SECONDS = 900  # 15 min

# Sizing SOFT_LAUNCH crypto : 1/8 Kelly pour TOUTES les strategies
CRYPTO_KELLY_FRACTION = 0.125


def is_weekday():
    """Verifie si c'est un jour de semaine (lun-ven)."""
    return datetime.now(PARIS).weekday() < 5


def is_eu_intraday_window():
    """Verifie si on est dans la fenetre EU intraday (09:00-17:30 Paris)."""
    now = datetime.now(PARIS)
    start = now.replace(hour=EU_START_HOUR, minute=EU_START_MINUTE, second=0)
    end = now.replace(hour=EU_END_HOUR, minute=EU_END_MINUTE, second=0)
    return start <= now <= end


def is_live_risk_window():
    """Verifie si on est dans la fenetre live risk monitoring (09:00-22:00 Paris)."""
    now = datetime.now(PARIS)
    return 9 <= now.hour <= 22


def is_intraday_window():
    """Verifie si on est dans la fenetre intraday (15:35-22:00 Paris)."""
    now = datetime.now(PARIS)
    start = now.replace(hour=INTRADAY_START_HOUR, minute=INTRADAY_START_MINUTE, second=0)
    end = now.replace(hour=INTRADAY_END_HOUR, minute=INTRADAY_END_MINUTE, second=0)
    return start <= now <= end


def is_daily_time():
    """Verifie si c'est l'heure du run daily (15:35 Paris, +/- 2 min)."""
    now = datetime.now(PARIS)
    return now.hour == DAILY_HOUR and DAILY_MINUTE <= now.minute <= DAILY_MINUTE + 2


import threading
_execution_lock = threading.Lock()
_risk_lock = threading.Lock()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            health = {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "worker": "running",
            }
            self.wfile.write(json.dumps(health).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def _start_health_server(port=8080):
    """Start a minimal HTTP health server for external monitoring."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server started on port {port}")


def reconcile_positions_at_startup():
    """
    Reconciliation au demarrage : compare les positions Alpaca vs le state local.
    Logue les positions orphelines (dans Alpaca mais pas dans le state).
    """
    try:
        from scripts.paper_portfolio import load_state, STATE_FILE
        from core.alpaca_client.client import AlpacaClient

        state = load_state()
        client = AlpacaClient.from_env()
        alpaca_positions = client.get_positions()

        # Positions connues du state (daily + intraday)
        state_symbols = set()
        for sid, pos in state.get("positions", {}).items():
            for sym in pos.get("symbols", []):
                state_symbols.add(sym)
        for sym in state.get("intraday_positions", {}).keys():
            state_symbols.add(sym)

        alpaca_symbols = {p["symbol"] for p in alpaca_positions}

        orphans = alpaca_symbols - state_symbols
        missing = state_symbols - alpaca_symbols

        if orphans:
            logger.warning(
                f"RECONCILIATION: {len(orphans)} position(s) orpheline(s) "
                f"(dans Alpaca mais pas dans le state): {sorted(orphans)}"
            )
            for p in alpaca_positions:
                if p["symbol"] in orphans:
                    logger.warning(
                        f"  ORPHELIN: {p['symbol']} qty={p['qty']} "
                        f"val=${p['market_val']:,.2f} P&L=${p['unrealized_pl']:+.2f}"
                    )
            _send_alert(
                f"ORPHAN POSITIONS DETECTED at startup: {', '.join(orphans)}. "
                f"Manual review required.",
                level="warning"
            )
        if missing:
            logger.warning(
                f"RECONCILIATION: {len(missing)} position(s) dans le state "
                f"mais plus dans Alpaca: {sorted(missing)}"
            )

        if not orphans and not missing:
            logger.info(
                f"RECONCILIATION OK: {len(alpaca_symbols)} position(s) Alpaca "
                f"correspondent au state"
            )

        account = client.get_account_info()
        logger.info(
            f"RECONCILIATION: equity=${account['equity']:,.2f} "
            f"cash=${account['cash']:,.2f} positions={len(alpaca_positions)}"
        )

    except Exception as e:
        logger.error(f"RECONCILIATION ECHOUEE: {e}", exc_info=True)


def check_positions_after_close():
    """
    Verifie apres 16:00 ET si des positions intraday sont encore ouvertes.
    Log CRITICAL si c'est le cas.
    """
    try:
        from core.alpaca_client.client import AlpacaClient
        from scripts.paper_portfolio import load_state

        state = load_state()
        intraday_pos = state.get("intraday_positions", {})
        if not intraday_pos:
            return  # Rien a verifier

        client = AlpacaClient.from_env()
        alpaca_positions = client.get_positions()
        alpaca_symbols = {p["symbol"] for p in alpaca_positions}

        # Verifier si des positions intraday sont encore ouvertes
        still_open = set(intraday_pos.keys()) & alpaca_symbols
        if still_open:
            logger.critical(
                f"POSITIONS INTRADAY NON FERMEES APRES 16:00 ET: {sorted(still_open)}. "
                f"Auto-close en cours..."
            )
            try:
                from core.telegram_alert import send_position_not_closed
                send_position_not_closed(sorted(still_open))
            except Exception:
                pass
            for sym in still_open:
                for p in alpaca_positions:
                    if p["symbol"] == sym:
                        logger.critical(
                            f"  NON FERME: {sym} qty={p['qty']} "
                            f"val=${p['market_val']:,.2f} P&L=${p['unrealized_pl']:+.2f}"
                        )

            # Auto-close orphan intraday positions
            try:
                result = client.close_all_positions(_authorized_by="auto_close_15_55")
                for r in result if isinstance(result, list) else [result]:
                    logger.critical(
                        f"  AUTO-CLOSE: {r.get('symbol', '?')} — "
                        f"status={r.get('status', '?')}"
                    )
                _send_alert(
                    f"AUTO-CLOSE 15:55 ET: {len(still_open)} position(s) intraday "
                    f"fermees automatiquement: {sorted(still_open)}",
                    level="critical",
                )
            except Exception as close_err:
                logger.critical(
                    f"ECHEC AUTO-CLOSE positions intraday: {close_err}"
                )
                _send_alert(
                    f"ECHEC AUTO-CLOSE 15:55 ET: {close_err}",
                    level="critical",
                )
    except Exception as e:
        logger.error(f"Erreur check_positions_after_close: {e}")


def log_heartbeat():
    """Log un heartbeat avec l'etat du worker (positions, equity)."""
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.get_account_info()
        positions = client.get_positions()
        equity = account["equity"]
        n_pos = len(positions)

        total_pnl = sum(p.get("unrealized_pl", 0) for p in positions)

        # FIX CRO B-2 : monitoring memoire
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        logger.info(
            f"HEARTBEAT: worker alive, {n_pos} position(s), "
            f"equity=${equity:,.2f}, unrealized P&L=${total_pnl:+.2f}, "
            f"RAM={mem_mb:.0f}MB"
        )
        if mem_mb > 500:
            logger.warning(f"MEMORY WARNING: worker utilise {mem_mb:.0f}MB (>500MB)")

        # Telegram heartbeat — paper (silencieux si non configure)
        try:
            from core.telegram_alert import send_heartbeat
            send_heartbeat(equity, n_pos, total_pnl, n_strategies=12)
        except Exception:
            pass

        # NOTE: heartbeat uses legacy telegram_alert.send_heartbeat()
        # Other alerts use _send_alert() which tries LiveAlertManager first

    except ImportError:
        logger.warning("HEARTBEAT: psutil non installe, monitoring memoire desactive")
    except Exception as e:
        logger.warning(f"HEARTBEAT: worker alive (Alpaca inaccessible: {e})")


def run_daily():
    """Execute le portfolio daily (3 strategies)."""
    if not _execution_lock.acquire(blocking=False):
        logger.warning("DAILY RUN SKIP — execution deja en cours (lock)")
        return
    try:
        logger.info("=== DAILY RUN ===")
        from scripts.paper_portfolio import run
        now = datetime.now(PARIS)
        force = now.day == 1  # Force rebalance le 1er du mois
        run(dry_run=False, force=force)
    except Exception as e:
        logger.error(f"Erreur daily run: {e}", exc_info=True)
        logger.warning(f"WARNING API: erreur lors du daily run — {type(e).__name__}: {e}")
    finally:
        _execution_lock.release()


def run_intraday(market: str = "US"):
    """Execute les strategies intraday.

    Args:
        market: 'US' (default, 15:35-22:00 Paris) or 'EU' (09:00-17:30 Paris)
    """
    if not _execution_lock.acquire(blocking=False):
        logger.warning("INTRADAY RUN SKIP — execution deja en cours (lock)")
        return
    try:
        logger.info(f"=== INTRADAY RUN ({market}) ===")
        from scripts.paper_portfolio import run_intraday
        run_intraday(dry_run=False)
    except Exception as e:
        logger.error(f"Erreur intraday run ({market}): {e}", exc_info=True)
        logger.warning(f"WARNING API: erreur lors du intraday run — {type(e).__name__}: {e}")
    finally:
        _execution_lock.release()


def run_live_risk_cycle():
    """Poll live risk checks every 5 minutes — circuit breakers, kill switches, deleveraging."""
    if not _risk_lock.acquire(blocking=False):
        logger.warning("SKIP live risk cycle — previous risk check still running")
        return
    try:
        from core.risk_manager_live import LiveRiskManager
        from core.kill_switch_live import LiveKillSwitch

        risk_mgr = LiveRiskManager()

        # Build portfolio snapshot from IBKR (or skip if not connected)
        # For now, use a lightweight check that doesn't require full TradingEngine
        portfolio = {"equity": risk_mgr.capital, "positions": [], "cash": risk_mgr.capital}

        try:
            # Try to get real portfolio from IBKR if available
            if os.environ.get("IBKR_CONNECTED") == "true":
                from core.broker.ibkr_adapter import IBKRBroker
                broker = IBKRBroker()
                account = broker.get_account_info()
                positions = broker.get_positions()
                portfolio = {
                    "equity": float(account.get("equity", risk_mgr.capital)),
                    "cash": float(account.get("cash", risk_mgr.capital)),
                    "positions": positions,
                    "margin_used_pct": float(account.get("margin_used_pct", 0)),
                }
        except Exception as e:
            logger.warning(f"Could not fetch IBKR portfolio for risk cycle: {e}")

        # FIX CRO H-4 : PnL calculation using actual daily starting equity,
        # NOT risk_mgr.capital (static config value like $10K).
        # Load daily_start_equity from state file; fallback to current equity.
        equity = portfolio.get("equity", risk_mgr.capital)
        daily_start_equity = equity  # fallback
        try:
            _state_path = ROOT / "paper_portfolio_state.json"
            if _state_path.exists():
                _state = json.loads(_state_path.read_text(encoding="utf-8"))
                _saved_equity = _state.get("daily_start_equity")
                if _saved_equity and float(_saved_equity) > 0:
                    daily_start_equity = float(_saved_equity)
                else:
                    # First check of the day — persist current equity as the baseline
                    _state["daily_start_equity"] = equity
                    _state_path.write_text(
                        json.dumps(_state, indent=2, default=str),
                        encoding="utf-8",
                    )
                    daily_start_equity = equity
        except Exception as _e:
            logger.warning(f"Could not load daily_start_equity: {_e}, using current equity")

        daily_pnl_pct = (equity - daily_start_equity) / daily_start_equity if daily_start_equity > 0 else 0

        # Run all risk checks
        risk_result = risk_mgr.check_all_limits(
            portfolio=portfolio,
            daily_pnl_pct=daily_pnl_pct,
            margin_used_pct=portfolio.get("margin_used_pct", 0),
        )

        if not risk_result["passed"]:
            logger.critical(f"LIVE RISK CHECK FAILED: {risk_result['blocked_reason']}")
            logger.critical(f"Actions required: {risk_result['actions']}")

            # Send alert
            _send_alert(
                f"LIVE RISK ALERT\n"
                f"Reason: {risk_result['blocked_reason']}\n"
                f"Actions: {', '.join(risk_result['actions'])}",
                level="critical"
            )

        # Check kill switch triggers
        kill_switch = LiveKillSwitch()
        ks_result = kill_switch.check_automatic_triggers(
            daily_pnl=daily_pnl_pct * risk_mgr.capital,
            capital=risk_mgr.capital,
        )

        if ks_result["triggered"]:
            logger.critical(f"KILL SWITCH TRIGGERED: {ks_result['reason']}")
            kill_switch.activate(
                reason=ks_result["reason"],
                trigger_type=ks_result["trigger_type"],
            )

        # Log deleveraging level
        delev = risk_result.get("deleveraging", {})
        if delev.get("level", 0) > 0:
            logger.warning(f"DELEVERAGING LEVEL {delev['level']}: {delev['message']}")

        # --- FIX M-9: Auto-deleverage L2+ (reduce largest position by 50%) ---
        actions = risk_result.get("actions", [])
        if any(a in actions for a in ("DELEVERAGE_L2", "DELEVERAGE_L3")):
            try:
                positions = portfolio.get("positions", [])
                if positions:
                    # Find the largest position by market_val (absolute)
                    largest = max(positions, key=lambda p: abs(float(p.get("market_val", 0))))
                    symbol = largest.get("symbol", "UNKNOWN")
                    qty = abs(float(largest.get("qty", 0)))
                    half_qty = qty / 2.0

                    if half_qty > 0 and os.environ.get("IBKR_CONNECTED") == "true":
                        from core.broker.ibkr_adapter import IBKRBroker
                        broker = IBKRBroker()
                        deleverage_action = "DELEVERAGE_L3" if "DELEVERAGE_L3" in actions else "DELEVERAGE_L2"
                        logger.critical(
                            f"AUTO-DELEVERAGE {deleverage_action}: reducing {symbol} "
                            f"by 50% (qty {qty} -> {qty - half_qty})"
                        )
                        broker.close_position(
                            symbol, qty=half_qty,
                            _authorized_by=f"auto_deleverage_{deleverage_action}",
                        )
                        logger.critical(
                            f"AUTO-DELEVERAGE {deleverage_action} EXECUTED: "
                            f"{symbol} reduced by {half_qty} units"
                        )
                        _send_alert(
                            f"AUTO-DELEVERAGE {deleverage_action}\n"
                            f"Position: {symbol}\n"
                            f"Reduced by 50%: {half_qty} units",
                            level="critical"
                        )
                    else:
                        logger.warning("Auto-deleverage skipped — IBKR not connected or qty=0")
            except Exception as e:
                logger.error(f"Auto-deleverage failed: {e}", exc_info=True)

        # --- SAFE-003 : LivePerformanceGuard (auto-disable strats) ---
        try:
            from core.live_performance_guard import LivePerformanceGuard, DISABLE, ALERT
            guard = LivePerformanceGuard()
            state = json.loads((ROOT / "paper_portfolio_state.json").read_text(encoding="utf-8")) if (ROOT / "paper_portfolio_state.json").exists() else {}
            pnl_log = state.get("strategy_pnl_log", {})
            for strat_id, entries in pnl_log.items():
                trades = [{"pnl": e.get("pnl", 0)} for e in entries]
                if len(trades) >= 10:
                    action, reason = guard.evaluate(strat_id, trades)
                    if action == DISABLE:
                        logger.critical(f"SAFE-003 AUTO-DISABLE: {strat_id} — {reason}")
                    elif action == ALERT:
                        logger.warning(f"SAFE-003 ALERT: {strat_id} — {reason}")
        except Exception as e:
            logger.debug(f"LivePerformanceGuard skip: {e}")

        # --- VIX/SPY stress guard (sizing reduction) ---
        try:
            from core.vix_stress_guard import VixStressGuard
            vix_guard = VixStressGuard()
            stress = vix_guard.check()
            if stress["level"] != "NORMAL":
                logger.warning(f"VIX STRESS: {stress['level']} — sizing {stress['sizing_factor']:.0%} — {stress['reason']}")
        except Exception as e:
            logger.debug(f"VixStressGuard skip: {e}")

        logger.info(f"Live risk cycle OK — equity=${equity:,.0f}, daily_pnl={daily_pnl_pct:.2%}")

    except Exception as e:
        logger.error(f"Live risk cycle error: {e}", exc_info=True)
    finally:
        _risk_lock.release()


def run_crypto_cycle():
    """Execute le cycle crypto : 8 strategies Binance, 24/7, toutes les 15 min.

    Charge les 8 strategies depuis strategies.crypto, genere les signaux,
    passe par CryptoRiskManager + CryptoKillSwitch, route vers BinanceBroker.
    """
    if not _execution_lock.acquire(blocking=False):
        logger.warning("CRYPTO CYCLE SKIP — execution deja en cours (lock)")
        return
    try:
        # FIX CRO H-5 : check trading_paused_until before any crypto activity
        try:
            _pause_state_path = ROOT / "paper_portfolio_state.json"
            if _pause_state_path.exists():
                _pause_state = json.loads(
                    _pause_state_path.read_text(encoding="utf-8")
                )
                _paused_until = _pause_state.get("trading_paused_until")
                if _paused_until:
                    _pause_dt = datetime.fromisoformat(_paused_until)
                    if datetime.now(timezone.utc) < _pause_dt:
                        logger.warning(
                            f"CRYPTO CYCLE SKIP — trading paused until "
                            f"{_paused_until}"
                        )
                        return
        except Exception as _pe:
            logger.warning(f"Could not check trading_paused_until: {_pe}")

        logger.info("=== CRYPTO CYCLE ===")

        # --- Verifier que Binance est configure ---
        if not os.getenv("BINANCE_API_KEY"):
            logger.debug("Crypto cycle skip — BINANCE_API_KEY non configuree")
            return

        # --- Charger la config d'allocation ---
        import yaml
        alloc_path = ROOT / "config" / "crypto_allocation.yaml"
        crypto_config = {}
        if alloc_path.exists():
            try:
                crypto_config = yaml.safe_load(
                    alloc_path.read_text(encoding="utf-8")
                ).get("crypto_allocation", {})
            except Exception as e:
                logger.error(f"Erreur lecture crypto_allocation.yaml: {e}")

        total_capital = crypto_config.get("total_capital", 20_000)

        # --- Importer les 8 strategies ---
        try:
            from strategies.crypto import CRYPTO_STRATEGIES
        except Exception as e:
            logger.error(f"Erreur import strategies crypto: {e}", exc_info=True)
            return

        if not CRYPTO_STRATEGIES:
            logger.warning("Aucune strategie crypto chargee — skip")
            return

        # --- Initialiser le risk manager + kill switch ---
        try:
            from core.crypto.risk_manager_crypto import CryptoRiskManager
        except Exception as e:
            logger.error(f"Erreur import CryptoRiskManager: {e}", exc_info=True)
            return

        risk_mgr = CryptoRiskManager(capital=total_capital)

        # --- Verifier le kill switch AVANT tout trade ---
        # CRO H-8: verifier l'etat persiste du kill switch (pas les triggers
        # dynamiques — ceux-la sont verifies dans check_all() plus bas)
        if risk_mgr.kill_switch._active:
            kill_reason = risk_mgr.kill_switch._reason or "previously activated"
            logger.critical(
                f"CRYPTO KILL SWITCH ACTIF — aucun trade: {kill_reason}"
            )
            _send_alert(
                f"CRYPTO KILL SWITCH: {kill_reason}", level="critical"
            )
            return

        # --- Initialiser le broker Binance ---
        broker = None
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
        except Exception as e:
            logger.error(
                f"Binance broker init echoue — signaux seront logues "
                f"mais pas executes: {e}"
            )

        # --- Recuperer les positions et l'equity pour le risk check ---
        positions = []
        current_equity = total_capital
        cash_available = 0
        earn_total = 0
        if broker:
            try:
                acct = broker.get_account_info()
                spot_equity = float(acct.get("equity", 0))
                cash_available = float(acct.get("cash", 0))
                positions = broker.get_positions()

                # Inclure les positions Earn dans l'equity totale
                # (LDBTC, LDUSDC, LDETH = Earn Flexible, pas dans equity spot)
                try:
                    earn_positions = broker.get_earn_positions()
                    for ep in earn_positions:
                        asset = ep.get("asset", "")
                        amount = float(ep.get("amount", 0))
                        if amount > 0:
                            # Estimer la valeur USD de chaque earn position
                            if asset in ("USDT", "USDC", "BUSD"):
                                earn_total += amount
                            elif asset == "BTC":
                                try:
                                    btc_ticker = broker.get_ticker_24h("BTCUSDT")
                                    btc_price = float(btc_ticker.get("last_price", 0))
                                    earn_total += amount * btc_price
                                except Exception:
                                    earn_total += amount * 85000  # Fallback
                            elif asset == "ETH":
                                try:
                                    eth_ticker = broker.get_ticker_24h("ETHUSDT")
                                    eth_price = float(eth_ticker.get("last_price", 0))
                                    earn_total += amount * eth_price
                                except Exception:
                                    earn_total += amount * 2000  # Fallback
                except Exception as e:
                    logger.warning(f"Earn positions indisponibles: {e}")

                current_equity = spot_equity + earn_total

                # Les stablecoins en Earn Flexible sont recuperables en < 1 min
                # Ils comptent comme cash disponible pour le risk check
                stable_earn = sum(
                    float(ep.get("amount", 0))
                    for ep in earn_positions
                    if ep.get("asset") in ("USDT", "USDC", "BUSD")
                ) if earn_positions else 0
                cash_available = float(acct.get("cash", 0)) + spot_equity + stable_earn

                logger.info(
                    f"  Equity: spot=${spot_equity:,.0f} + earn=${earn_total:,.0f} "
                    f"= total=${current_equity:,.0f} "
                    f"(cash_available=${cash_available:,.0f})"
                )
            except Exception as e:
                logger.warning(f"Binance account info indisponible: {e}")

        # --- Risk check global avant signaux ---
        risk_result = risk_mgr.check_all(
            positions=positions,
            current_equity=current_equity,
            cash_available=cash_available,
            earn_total=earn_total,
        )

        if not risk_result["passed"]:
            failed_checks = [
                name for name, c in risk_result["checks"].items()
                if not c["passed"]
            ]
            logger.warning(
                f"CRYPTO RISK CHECK FAILED ({len(failed_checks)} checks): "
                f"{failed_checks}"
            )

        # --- Boucle sur les 8 strategies ---
        import pandas as pd

        n_signals = 0
        n_orders = 0
        n_errors = 0

        for strat_id, strat_data in CRYPTO_STRATEGIES.items():
            config = strat_data["config"]
            signal_fn = strat_data["signal_fn"]
            strat_name = config.get("name", strat_id)

            try:
                # Construire le candle minimal (dernier prix) et le state
                # Chaque strategie recoit un candle pd.Series et un state dict
                candle_data = {"close": 0, "open": 0, "high": 0, "low": 0,
                               "volume": 0, "timestamp": datetime.now(
                                   timezone.utc).isoformat()}

                # Tenter de recuperer le dernier prix via Binance
                primary_symbol = config.get("symbols", ["BTCUSDT"])[0]
                df_full = None
                if broker and primary_symbol.endswith("USDT"):
                    try:
                        timeframe = config.get("timeframe", "4h")
                        price_data = broker.get_prices(
                            primary_symbol, timeframe=timeframe, bars=100
                        )
                        bars = price_data.get("bars", [])
                        if bars:
                            last_bar = bars[-1]
                            candle_data = {
                                "close": last_bar["c"],
                                "open": last_bar["o"],
                                "high": last_bar["h"],
                                "low": last_bar["l"],
                                "volume": last_bar["v"],
                                "timestamp": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }
                            # Construire df_full pour les strategies qui en ont besoin
                            df_full = pd.DataFrame(bars)
                            df_full.rename(columns={
                                "o": "open", "h": "high", "l": "low",
                                "c": "close", "v": "volume",
                            }, inplace=True)
                    except Exception as e:
                        logger.warning(
                            f"  [{strat_id}] Impossible de recuperer "
                            f"les prix {primary_symbol}: {e}"
                        )

                candle = pd.Series(candle_data)

                # State avec capital alloue (1/8 Kelly SOFT_LAUNCH)
                alloc_pct = config.get("allocation_pct", 0.10)
                strat_capital = total_capital * alloc_pct * CRYPTO_KELLY_FRACTION
                state = {
                    "capital": total_capital,
                    "equity": current_equity,
                    "positions": positions,
                    "i": len(df_full) - 1 if df_full is not None and not df_full.empty else 0,
                }

                # Kwargs supplementaires pour certaines strategies
                kwargs = {}
                if df_full is not None:
                    kwargs["df_full"] = df_full
                kwargs["symbol"] = primary_symbol

                # --- Appel du signal_fn (FIX CRO H: per-strategy timeout 30s) ---
                _signal_result = [None]
                _signal_error = [None]
                def _run_signal(_fn=signal_fn, _c=candle, _s=state, _kw=kwargs):
                    try:
                        _signal_result[0] = _fn(_c, _s, **_kw)
                    except Exception as _e:
                        _signal_error[0] = _e
                _t = threading.Thread(target=_run_signal, daemon=True)
                _t.start()
                _t.join(timeout=30)
                if _t.is_alive():
                    logger.critical(
                        f"  [{strat_id}] signal_fn TIMEOUT (30s) — skipping"
                    )
                    n_errors += 1
                    continue
                if _signal_error[0] is not None:
                    raise _signal_error[0]
                signal = _signal_result[0]

                # --- Log du signal (meme si None) ---
                if signal is None:
                    logger.info(
                        f"  [{strat_id}] {strat_name}: pas de signal"
                    )
                    continue

                n_signals += 1
                action = signal.get("action", "UNKNOWN")
                logger.info(
                    f"  [{strat_id}] {strat_name}: SIGNAL {action} "
                    f"— {json.dumps({k: v for k, v in signal.items() if k != 'df_full'}, default=str)}"
                )

                # --- Verifier risk avant execution ---
                if not risk_result["passed"]:
                    logger.warning(
                        f"  [{strat_id}] Signal ignore — risk check global "
                        f"non passe"
                    )
                    continue

                # --- Executer via BinanceBroker si disponible ---
                if broker is None:
                    logger.info(
                        f"  [{strat_id}] Signal logue mais pas execute "
                        f"(broker indisponible)"
                    )
                    continue

                # Determiner la direction et le market_type
                market_type = config.get("market_type", "spot")

                # Pour les signaux EARN, pas d'ordre classique
                if action in ("EARN_REBALANCE", "EARN_SUBSCRIBE", "EARN_REDEEM",
                              "CAPITAL_RELEASE"):
                    logger.info(
                        f"  [{strat_id}] Earn signal logue "
                        f"(execution earn non implementee dans le worker)"
                    )
                    continue

                # Pour les signaux CLOSE
                if action == "CLOSE":
                    try:
                        result = broker.close_position(
                            primary_symbol,
                            _authorized_by=f"crypto_worker_{strat_id}",
                        )
                        logger.info(
                            f"  [{strat_id}] Position fermee: {result}"
                        )
                        n_orders += 1
                    except Exception as e:
                        logger.error(
                            f"  [{strat_id}] Erreur close: {e}"
                        )
                        n_errors += 1
                    continue

                # Pour BUY/SELL — calculer le sizing
                direction = signal.get("direction", action)
                if direction not in ("BUY", "SELL", "LONG", "SHORT"):
                    logger.info(
                        f"  [{strat_id}] Action {action} non routee "
                        f"(direction inconnue)"
                    )
                    continue

                # Mapping direction -> Binance side
                side = "BUY" if direction in ("BUY", "LONG") else "SELL"

                # Sizing : capital alloue * 1/8 Kelly
                price = candle_data.get("close", 0)
                if price <= 0:
                    logger.warning(
                        f"  [{strat_id}] Prix nul — ordre non place"
                    )
                    continue

                notional = strat_capital
                stop_loss = signal.get("stop_loss")

                # CRO: JAMAIS d'ordre sans stop-loss sur des positions directionnelles
                if stop_loss is None and market_type != "earn":
                    # Calculer un SL par defaut a -5% du prix
                    default_sl_pct = 0.05
                    if side == "BUY":
                        stop_loss = round(price * (1 - default_sl_pct), 2)
                    else:
                        stop_loss = round(price * (1 + default_sl_pct), 2)
                    logger.warning(
                        f"  [{strat_id}] Signal SANS stop_loss — SL par defaut "
                        f"applique a {default_sl_pct*100:.0f}%: ${stop_loss:.2f}"
                    )

                try:
                    result = broker.create_position(
                        symbol=signal.get("symbol", primary_symbol),
                        direction=side,
                        notional=notional if side == "BUY" else None,
                        qty=round(notional / price, 6) if side == "SELL" else None,
                        stop_loss=stop_loss,
                        market_type=market_type,
                        _authorized_by=f"crypto_worker_{strat_id}",
                    )
                    logger.info(
                        f"  [{strat_id}] ORDRE EXECUTE: {side} "
                        f"${notional:.0f} {signal.get('symbol', primary_symbol)} "
                        f"— {result.get('status', '???')}"
                    )
                    n_orders += 1
                except Exception as e:
                    logger.error(
                        f"  [{strat_id}] Erreur execution: {e}",
                        exc_info=True,
                    )
                    n_errors += 1

            except Exception as e:
                # Une strategie qui plante ne doit pas bloquer les autres
                logger.error(
                    f"  [{strat_id}] ERREUR STRATEGIE: {e}", exc_info=True
                )
                n_errors += 1

        logger.info(
            f"=== CRYPTO CYCLE TERMINE: {n_signals} signal(s), "
            f"{n_orders} ordre(s), {n_errors} erreur(s) ==="
        )

        # --- Telegram recap ---
        if n_signals > 0 or n_orders > 0:
            _send_alert(
                f"CRYPTO CYCLE: {n_signals} signaux, {n_orders} ordres, "
                f"{n_errors} erreurs — equity=${current_equity:,.0f}",
                level="info",
            )

    except Exception as e:
        logger.error(f"Erreur critique crypto cycle: {e}", exc_info=True)
        _send_alert(
            f"CRYPTO CYCLE ERREUR CRITIQUE: {e}", level="critical"
        )
    finally:
        _execution_lock.release()


def main():
    _start_health_server()
    logger.info("=" * 60)
    logger.info("  TRADING WORKER — demarrage")
    logger.info(f"  Paris: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  New York: {datetime.now(ET).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  Alpaca API: {'SET' if os.getenv('ALPACA_API_KEY') else 'NOT SET'}")
    logger.info(f"  Binance API: {'SET' if os.getenv('BINANCE_API_KEY') else 'NOT SET'}")
    logger.info("=" * 60)

    daily_done_today = False
    last_intraday = 0
    last_eu_intraday = 0
    last_live_risk = 0
    last_heartbeat = 0
    last_cross_portfolio = 0
    last_crypto = 0
    after_close_checked_today = False
    HEARTBEAT_INTERVAL = 1800  # 30 min
    CROSS_PORTFOLIO_INTERVAL = 14400  # 4 hours

    # Verifier que les imports fonctionnent au demarrage
    try:
        from scripts.paper_portfolio import run, run_intraday
        logger.info("  Imports paper_portfolio OK")
    except Exception as e:
        logger.error(f"  ERREUR IMPORT: {e}", exc_info=True)
        logger.error("  Le worker ne peut pas demarrer sans paper_portfolio")
        sys.exit(1)

    # === RECONCILIATION AU DEMARRAGE ===
    logger.info("  Reconciliation des positions au demarrage...")
    reconcile_positions_at_startup()

    # Crypto reconciliation
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            acct = broker.get_account_info()
            positions = broker.get_positions()
            earn = broker.get_earn_positions()
            logger.info(f"CRYPTO RECONCILIATION: equity=${acct.get('equity',0):.0f}, "
                        f"{len(positions)} positions, {len(earn)} earn products")
            if positions:
                for p in positions:
                    logger.info(f"  Binance position: {p.get('symbol')} {p.get('side')} "
                               f"qty={p.get('qty',0)}")
        except Exception as e:
            logger.warning(f"Crypto reconciliation failed: {e}")

    # Premier heartbeat
    log_heartbeat()
    last_heartbeat = time.time()

    while True:
        now_paris = datetime.now(PARIS)
        now_et = datetime.now(ET)
        today = now_paris.date()

        # Reset daily flags au changement de jour
        if daily_done_today and now_paris.hour < DAILY_HOUR:
            daily_done_today = False
            after_close_checked_today = False

        # === CRYPTO CYCLE 24/7 (y compris weekends) — toutes les 15 min ===
        if time.time() - last_crypto >= CRYPTO_INTERVAL_SECONDS:
            run_crypto_cycle()
            last_crypto = time.time()

        # Skip weekends pour les marches traditionnels (US/EU/FX)
        if not is_weekday():
            time.sleep(60)
            continue

        # === HEARTBEAT toutes les 30 min ===
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            log_heartbeat()
            last_heartbeat = time.time()

        # === CHECK POSITIONS APRES FERMETURE (16:05-16:30 ET) ===
        if (not after_close_checked_today
                and now_et.hour == 16 and 5 <= now_et.minute <= 30):
            logger.info("  Check des positions apres fermeture du marche...")
            check_positions_after_close()
            after_close_checked_today = True

        # Daily run a 15:35 Paris (une seule fois par jour)
        # FIX CRO H-6 : check trading_paused_until before run_daily
        _daily_paused = False
        try:
            _d_state_path = ROOT / "paper_portfolio_state.json"
            if _d_state_path.exists():
                _d_state = json.loads(
                    _d_state_path.read_text(encoding="utf-8")
                )
                _d_paused_until = _d_state.get("trading_paused_until")
                if _d_paused_until:
                    _d_pause_dt = datetime.fromisoformat(_d_paused_until)
                    if datetime.now(timezone.utc) < _d_pause_dt:
                        _daily_paused = True
        except Exception:
            pass

        if is_daily_time() and not daily_done_today:
            if _daily_paused:
                logger.warning(
                    f"DAILY RUN SKIP — trading paused until {_d_paused_until}"
                )
            else:
                run_daily()
            daily_done_today = True

        # Intraday US toutes les 5 min pendant la fenetre (15:35-22:00 Paris)
        if is_intraday_window():
            elapsed = time.time() - last_intraday
            if elapsed >= INTRADAY_INTERVAL_SECONDS:
                run_intraday(market="US")
                last_intraday = time.time()

        # Intraday EU toutes les 5 min pendant la fenetre (09:00-17:30 Paris)
        if is_eu_intraday_window():
            elapsed = time.time() - last_eu_intraday
            if elapsed >= INTRADAY_INTERVAL_SECONDS:
                run_intraday(market="EU")
                last_eu_intraday = time.time()

        # Live risk monitoring every 5 min (09:00-22:00 Paris)
        if is_live_risk_window():
            elapsed = time.time() - last_live_risk
            if elapsed >= LIVE_RISK_INTERVAL_SECONDS:
                run_live_risk_cycle()
                last_live_risk = time.time()

        # Cross-portfolio exposure check every 4 hours (IBKR + Binance)
        if time.time() - last_cross_portfolio >= CROSS_PORTFOLIO_INTERVAL:
            try:
                from core.cross_portfolio_guard import check_combined_exposure

                ibkr_long, ibkr_short, ibkr_capital = 0, 0, 0
                crypto_long, crypto_short, crypto_capital = 0, 0, 0

                # Try IBKR
                try:
                    if os.environ.get("IBKR_CONNECTED") == "true":
                        from core.broker.ibkr_adapter import IBKRBroker
                        ibkr = IBKRBroker()
                        acct = ibkr.get_account_info()
                        ibkr_capital = float(acct.get("equity", 0))
                        for p in ibkr.get_positions():
                            val = abs(float(p.get("market_val", 0)))
                            if float(p.get("qty", 0)) >= 0:
                                ibkr_long += val
                            else:
                                ibkr_short += val
                except Exception as e:
                    logger.debug(f"Cross-portfolio: IBKR unavailable: {e}")

                # Try Binance
                try:
                    if os.environ.get("BINANCE_API_KEY"):
                        from core.broker.binance_broker import BinanceBroker
                        bnb = BinanceBroker()
                        acct = bnb.get_account_info()
                        crypto_capital = float(acct.get("equity", 0))
                        for p in bnb.get_positions():
                            val = abs(float(p.get("market_val", 0)))
                            if p.get("side") == "SHORT":
                                crypto_short += val
                            else:
                                crypto_long += val
                except Exception as e:
                    logger.debug(f"Cross-portfolio: Binance unavailable: {e}")

                if ibkr_capital > 0 or crypto_capital > 0:
                    result = check_combined_exposure(
                        ibkr_long, ibkr_short, ibkr_capital,
                        crypto_long, crypto_short, crypto_capital,
                    )
                    if result["level"] != "OK":
                        logger.warning(f"CROSS-PORTFOLIO: {result['message']}")
                        _send_alert(f"CROSS-PORTFOLIO: {result['message']}", level="warning")
                    else:
                        logger.info(f"Cross-portfolio check OK: {result['combined_pct']}% combined")

            except Exception as e:
                logger.error(f"Cross-portfolio check error: {e}", exc_info=True)
            last_cross_portfolio = time.time()

        # Sleep 30s entre les checks
        time.sleep(30)


if __name__ == "__main__":
    main()
