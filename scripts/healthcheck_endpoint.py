"""
Healthcheck HTTP endpoint — expose /health pour monitoring externe.

Permet a un service comme UptimeRobot de pinger toutes les 5 min et
alerter par SMS/Telegram si le worker est down.

Usage :
    python scripts/healthcheck_endpoint.py              # port 8080
    python scripts/healthcheck_endpoint.py --port 9090  # port custom

Endpoints :
    GET /health  -> 200 JSON {status, timestamp, worker_last_run, ...}
    GET /        -> 200 "Trading Platform Healthcheck"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
)
logger = logging.getLogger("healthcheck")

# Charger .env si present (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DEFAULT_PORT = int(os.getenv("HEALTHCHECK_PORT", "8080"))

# Seuil : le worker est considere "stale" si sa derniere execution
# date de plus de 10 minutes
WORKER_STALE_THRESHOLD_SECONDS = 600


def _check_worker_alive() -> dict:
    """Verifie si le worker a tourne recemment.

    Lit paper_portfolio_state.json et regarde le timestamp de la
    derniere execution. Si > 10 min, le worker est potentiellement down.

    Returns:
        {"alive": bool, "last_run": str, "age_seconds": float}
    """
    state_file = ROOT / "paper_portfolio_state.json"
    if not state_file.exists():
        return {
            "alive": False,
            "last_run": None,
            "age_seconds": -1,
            "reason": "state file missing",
        }

    try:
        with open(state_file) as f:
            state = json.load(f)

        last_run_date = state.get("last_run_date", "")
        last_monthly = state.get("last_monthly", "")

        # Utiliser last_monthly comme proxy du dernier run (contient un timestamp ISO)
        if last_monthly:
            from datetime import datetime, timezone
            try:
                # Parse ISO timestamp
                last_ts = datetime.fromisoformat(last_monthly)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last_ts).total_seconds()
                return {
                    "alive": age < WORKER_STALE_THRESHOLD_SECONDS,
                    "last_run": last_monthly,
                    "age_seconds": round(age, 1),
                }
            except (ValueError, TypeError):
                pass

        # Fallback : si on a seulement la date
        return {
            "alive": True,  # Conservatif : pas assez d'info pour dire down
            "last_run": last_run_date or "unknown",
            "age_seconds": -1,
            "reason": "no precise timestamp",
        }

    except Exception as e:
        return {
            "alive": False,
            "last_run": None,
            "age_seconds": -1,
            "reason": str(e),
        }


def _check_alpaca_connected() -> dict:
    """Verifie la connexion a l'API Alpaca.

    Returns:
        {"connected": bool, "equity": float, "positions": int}
    """
    try:
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        account = client.get_account_info()
        positions = client.get_positions()
        return {
            "connected": True,
            "equity": account.get("equity", 0),
            "cash": account.get("cash", 0),
            "positions": len(positions),
        }
    except Exception as e:
        return {
            "connected": False,
            "equity": 0,
            "cash": 0,
            "positions": 0,
            "error": str(e),
        }


def _check_kill_switch() -> dict:
    """Verifie si un kill switch est actif.

    Lit le state pour voir si des strategies sont desactivees.

    Returns:
        {"active": bool, "disabled_strategies": list}
    """
    state_file = ROOT / "paper_portfolio_state.json"
    if not state_file.exists():
        return {"active": False, "disabled_strategies": []}

    try:
        with open(state_file) as f:
            state = json.load(f)

        disabled = state.get("disabled_strategies", [])
        return {
            "active": len(disabled) > 0,
            "disabled_strategies": disabled,
        }
    except Exception:
        return {"active": False, "disabled_strategies": []}


def build_health_response() -> tuple[int, dict]:
    """Construit la reponse de healthcheck.

    Returns:
        (http_status_code, response_dict)
    """
    worker = _check_worker_alive()
    alpaca = _check_alpaca_connected()
    kill_switch = _check_kill_switch()

    # Determiner le status global
    issues = []
    if not worker.get("alive", False):
        issues.append("worker_stale")
    if not alpaca.get("connected", False):
        issues.append("alpaca_disconnected")
    if kill_switch.get("active", False):
        issues.append("kill_switch_active")

    if not issues:
        status = "healthy"
        http_code = 200
    elif "worker_stale" in issues or "alpaca_disconnected" in issues:
        status = "unhealthy"
        http_code = 503
    else:
        status = "degraded"
        http_code = 200

    response = {
        "status": status,
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "issues": issues,
        "worker": worker,
        "alpaca": {
            "connected": alpaca.get("connected", False),
            "equity": alpaca.get("equity", 0),
            "positions": alpaca.get("positions", 0),
        },
        "kill_switch": kill_switch,
    }

    return http_code, response


class HealthHandler(BaseHTTPRequestHandler):
    """Handler HTTP pour le healthcheck."""

    def do_GET(self):
        if self.path == "/health":
            http_code, response = build_health_response()
            body = json.dumps(response, indent=2).encode("utf-8")

            self.send_response(http_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/":
            body = b"Trading Platform Healthcheck - GET /health for status"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        """Override pour utiliser le logger Python au lieu de stderr."""
        logger.debug(f"{self.client_address[0]} - {format % args}")


def main():
    parser = argparse.ArgumentParser(description="Trading Platform Healthcheck")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Port HTTP (default: {DEFAULT_PORT})"
    )
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), HealthHandler)
    logger.info(f"Healthcheck server started on port {args.port}")
    logger.info(f"  GET http://0.0.0.0:{args.port}/health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Healthcheck server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
