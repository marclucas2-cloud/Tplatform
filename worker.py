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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "intraday-backtesterV2"))

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
    try:
        from core.telegram_alert import send_alert
        send_alert("Worker SIGTERM — shutting down gracefully", level="warning")
    except Exception:
        pass
    # Let the scheduler shut down gracefully
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

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
            try:
                from core.telegram_alert import send_alert
                send_alert(
                    f"⚠️ ORPHAN POSITIONS DETECTED at startup: {', '.join(orphans)}. "
                    f"Manual review required.",
                    level="warning"
                )
            except Exception:
                pass
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
                f"Action manuelle requise!"
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

        # TODO: Unify telegram_alert (legacy) with alerting_live.LiveAlertManager
        # For now, heartbeat uses legacy telegram_alert.send_heartbeat()

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
    if not _execution_lock.acquire(blocking=False):
        logger.warning("SKIP live risk cycle — previous execution still running")
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

        # Calculate PnL metrics (simplified — use trade journal if available)
        equity = portfolio.get("equity", risk_mgr.capital)
        daily_pnl_pct = (equity - risk_mgr.capital) / risk_mgr.capital if risk_mgr.capital > 0 else 0

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
            try:
                from core.telegram_alert import send_alert
                send_alert(
                    f"LIVE RISK ALERT\n"
                    f"Reason: {risk_result['blocked_reason']}\n"
                    f"Actions: {', '.join(risk_result['actions'])}",
                    level="critical"
                )
            except Exception:
                pass

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
        _execution_lock.release()


def main():
    _start_health_server()
    logger.info("=" * 60)
    logger.info("  TRADING WORKER — demarrage")
    logger.info(f"  Paris: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  New York: {datetime.now(ET).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  Alpaca API: {'SET' if os.getenv('ALPACA_API_KEY') else 'NOT SET'}")
    logger.info("=" * 60)

    daily_done_today = False
    last_intraday = 0
    last_eu_intraday = 0
    last_live_risk = 0
    last_heartbeat = 0
    last_cross_portfolio = 0
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

        # Skip weekends
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
        if is_daily_time() and not daily_done_today:
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
                        try:
                            from core.telegram_alert import send_alert
                            send_alert(f"CROSS-PORTFOLIO: {result['message']}", level="warning")
                        except Exception:
                            pass
                    else:
                        logger.info(f"Cross-portfolio check OK: {result['combined_pct']}% combined")

            except Exception as e:
                logger.error(f"Cross-portfolio check error: {e}", exc_info=True)
            last_cross_portfolio = time.time()

        # Sleep 30s entre les checks
        time.sleep(30)


if __name__ == "__main__":
    main()
