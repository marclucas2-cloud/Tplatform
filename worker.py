"""
Worker Railway — scheduler 24/7 pour le paper trading.

Remplace les crons Windows (schtasks) par un process Python permanent.
Execute :
  - Daily portfolio (3 strategies) a 15:35 Paris
  - Intraday strategies (7 strategies) toutes les 5 min, 15:35-22:00 Paris
"""
import os
import sys
import time
import json
import logging
from datetime import datetime
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


def is_weekday():
    """Verifie si c'est un jour de semaine (lun-ven)."""
    return datetime.now(PARIS).weekday() < 5


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
        logger.info(
            f"HEARTBEAT: worker alive, {n_pos} position(s), "
            f"equity=${equity:,.2f}, unrealized P&L=${total_pnl:+.2f}"
        )
        # Telegram heartbeat (silencieux si non configure)
        try:
            from core.telegram_alert import send_heartbeat
            send_heartbeat(equity, n_pos, total_pnl, n_strategies=12)
        except Exception:
            pass
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


def run_intraday():
    """Execute les strategies intraday."""
    if not _execution_lock.acquire(blocking=False):
        logger.warning("INTRADAY RUN SKIP — execution deja en cours (lock)")
        return
    try:
        logger.info("=== INTRADAY RUN ===")
        from scripts.paper_portfolio import run_intraday
        run_intraday(dry_run=False)
    except Exception as e:
        logger.error(f"Erreur intraday run: {e}", exc_info=True)
        logger.warning(f"WARNING API: erreur lors du intraday run — {type(e).__name__}: {e}")
    finally:
        _execution_lock.release()


def main():
    logger.info("=" * 60)
    logger.info("  TRADING WORKER — demarrage")
    logger.info(f"  Paris: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  New York: {datetime.now(ET).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  Alpaca API: {'SET' if os.getenv('ALPACA_API_KEY') else 'NOT SET'}")
    logger.info("=" * 60)

    daily_done_today = False
    last_intraday = 0
    last_heartbeat = 0
    after_close_checked_today = False
    HEARTBEAT_INTERVAL = 1800  # 30 min

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

        # Intraday toutes les 5 min pendant la fenetre
        if is_intraday_window():
            elapsed = time.time() - last_intraday
            if elapsed >= INTRADAY_INTERVAL_SECONDS:
                run_intraday()
                last_intraday = time.time()

        # Sleep 30s entre les checks
        time.sleep(30)


if __name__ == "__main__":
    main()
