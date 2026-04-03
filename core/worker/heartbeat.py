"""Worker heartbeat — health monitoring and dead man's switch."""
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from core.worker.alerts import log_event, send_alert
from core.worker.config import PARIS

logger = logging.getLogger("worker")

ROOT = Path(__file__).parent.parent.parent
_HEARTBEAT_FILE = ROOT / "data" / "monitoring" / "heartbeat.json"
_HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_heartbeat():
    """Log un heartbeat avec l'etat du worker (positions, equity)."""
    log_event("heartbeat", details={"source": "worker_main_loop"})

    try:
        _HEARTBEAT_FILE.write_text(json.dumps({
            "timestamp": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
        }))
    except Exception:
        pass

    try:
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        logger.info(f"HEARTBEAT: worker alive, RAM={mem_mb:.0f}MB, PID={os.getpid()}")
        if mem_mb > 500:
            logger.warning(f"MEMORY WARNING: worker utilise {mem_mb:.0f}MB (>500MB)")
    except ImportError:
        logger.info("HEARTBEAT: worker alive (psutil not installed)")

    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.get_account_info()
        positions = client.get_positions()
        total_pnl = sum(p.get("unrealized_pl", 0) for p in positions)
        logger.info(
            f"  Alpaca: equity=${account['equity']:,.2f}, "
            f"{len(positions)} pos, P&L=${total_pnl:+.2f}"
        )
    except Exception as e:
        logger.info(f"  Alpaca: unavailable ({e})")


def telegram_heartbeat_full():
    """Heartbeat enrichi multi-broker pour Telegram (toutes les 30 min)."""
    lines = [f"HEARTBEAT {datetime.now(PARIS).strftime('%H:%M')} CET"]

    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        acct = client.get_account_info()
        positions = client.get_positions()
        total_pnl = sum(p.get("unrealized_pl", 0) for p in positions)
        lines.append(f"\nALPACA (paper): ${acct['equity']:,.0f}")
        lines.append(f"  {len(positions)} pos, PnL ${total_pnl:+,.0f}")
        for p in positions:
            lines.append(
                f"  {p['symbol']} {p.get('side','?')} {p.get('qty',0)} "
                f"PnL=${p.get('unrealized_pl',0):+.1f}"
            )
    except Exception as e:
        lines.append(f"\nALPACA: {e}")

    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            acct = broker.get_account_info()
            eq = float(acct.get("equity", 0))
            positions = broker.get_positions()
            lines.append(f"\nBINANCE (LIVE): ${eq:,.0f}")
            lines.append(f"  {len(positions)} pos")
            for p in positions[:5]:
                lines.append(f"  {p.get('symbol')} {p.get('side','')} qty={p.get('qty',0)}")
        except Exception as e:
            lines.append(f"\nBINANCE: {e}")

    try:
        import socket
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4002"))
        with socket.create_connection((host, port), timeout=3):
            pass
        lines.append(f"\nIBKR (LIVE): gateway UP, ${os.getenv('IBKR_PORT','4002')}")
    except Exception:
        lines.append("\nIBKR: gateway DOWN")

    try:
        import psutil
        mem = psutil.Process().memory_info().rss / 1024 / 1024
        lines.append(f"\nRAM: {mem:.0f}MB")
    except Exception:
        pass

    send_alert("\n".join(lines), level="info")


def reconcile_positions_at_startup():
    """Reconciliation au demarrage : compare positions Alpaca vs state local."""
    try:
        from scripts.paper_portfolio import load_state

        from core.alpaca_client.client import AlpacaClient

        state = load_state()
        client = AlpacaClient.from_env()
        alpaca_positions = client.get_positions()

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
            send_alert(
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
    """Verifie apres 16:00 ET si des positions intraday sont encore ouvertes."""
    try:
        from scripts.paper_portfolio import load_state

        from core.alpaca_client.client import AlpacaClient

        state = load_state()
        intraday_pos = state.get("intraday_positions", {})

        client = AlpacaClient.from_env()
        alpaca_positions = client.get_positions()

        if not alpaca_positions:
            return

        still_open = {p["symbol"] for p in alpaca_positions}
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

            _closed = []
            _failed = []
            for sym in still_open:
                try:
                    client.close_position(sym, _authorized_by="auto_close_15_55")
                    _closed.append(sym)
                    logger.critical(f"  AUTO-CLOSE OK: {sym}")
                except Exception as close_err:
                    _failed.append(f"{sym}: {close_err}")
                    logger.critical(f"  AUTO-CLOSE FAIL {sym}: {close_err}")

            for sym in _closed:
                intraday_pos.pop(sym, None)
            from scripts.paper_portfolio import save_state
            save_state(state)

            if _closed:
                send_alert(
                    f"AUTO-CLOSE 16:00 ET: {', '.join(_closed)} ferme(s)",
                    level="critical",
                )
            if _failed:
                send_alert(
                    f"ECHEC AUTO-CLOSE 16:00 ET: {'; '.join(_failed)}",
                    level="critical",
                )
    except Exception as e:
        logger.error(f"Erreur check_positions_after_close: {e}")
