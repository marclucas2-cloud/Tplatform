"""
Reconciliation automatique des positions — compare l'etat interne
vs les positions reelles chez les brokers.

Alerte si divergence > $10 ou > 1 share.

Usage :
    python scripts/reconciliation.py           # Run complet
    python scripts/reconciliation.py --json    # Sortie JSON

    from scripts.reconciliation import PositionReconciler
    rec = PositionReconciler()
    result = rec.run()
    if result["status"] == "DIVERGENCE":
        for d in result["divergences"]:
            print(d)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Charger .env si present (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
)
logger = logging.getLogger("reconciliation")

# Import au niveau module pour permettre le mocking dans les tests
from core.alpaca_client.client import AlpacaClient

STATE_FILE = ROOT / "paper_portfolio_state.json"

# Seuils de divergence
DIVERGENCE_AMOUNT_THRESHOLD = 10.0   # $10
DIVERGENCE_QTY_THRESHOLD = 1.0       # 1 share


class PositionReconciler:
    """Compare les positions internes vs les positions reelles chez les brokers.

    Alerte si divergence > $10 ou > 1 share.
    """

    def __init__(self, state_path: Optional[Path] = None):
        """
        Args:
            state_path: chemin vers paper_portfolio_state.json
                       (default: ROOT/paper_portfolio_state.json)
        """
        self._state_path = state_path or STATE_FILE

    def _load_internal_state(self) -> dict:
        """Charge le state interne depuis le fichier JSON."""
        if not self._state_path.exists():
            logger.warning(f"State file non trouve: {self._state_path}")
            return {}

        with open(self._state_path) as f:
            return json.load(f)

    def _get_internal_symbols(self, state: dict) -> dict[str, dict]:
        """Extrait tous les symboles connus du state interne.

        Returns:
            {symbol: {"source": "daily"|"intraday", "strategy": str, "direction": str}}
        """
        symbols = {}

        # Positions daily (dans "positions")
        for strategy_id, pos_data in state.get("positions", {}).items():
            for sym in pos_data.get("symbols", []):
                symbols[sym] = {
                    "source": "daily",
                    "strategy": strategy_id,
                    "direction": "LONG",  # daily = long par defaut
                }

        # Positions intraday (dans "intraday_positions")
        for sym, pos_data in state.get("intraday_positions", {}).items():
            symbols[sym] = {
                "source": "intraday",
                "strategy": pos_data.get("strategy", "unknown"),
                "direction": pos_data.get("direction", "LONG"),
            }

        return symbols

    def reconcile_alpaca(self) -> list[dict]:
        """Compare paper_portfolio_state.json vs Alpaca API positions.

        Returns:
            Liste de divergences :
            [{
                "type": "orphan"|"missing"|"qty_mismatch"|"value_mismatch",
                "broker": "alpaca",
                "symbol": str,
                "detail": str,
                "severity": "warning"|"critical",
            }]
        """
        state = self._load_internal_state()
        internal_symbols = self._get_internal_symbols(state)

        try:
            client = AlpacaClient.from_env()
            broker_positions = client.get_positions()
        except Exception as e:
            logger.error(f"Alpaca API inaccessible: {e}")
            return [{"type": "error", "broker": "alpaca",
                     "symbol": "*", "detail": f"API inaccessible: {e}",
                     "severity": "critical"}]

        return self._compare_positions(
            broker_name="alpaca",
            internal_symbols=internal_symbols,
            broker_positions=broker_positions,
            state=state,
        )

    def reconcile_ibkr(self) -> list[dict]:
        """Compare l'etat interne vs IBKR positions.

        Returns:
            Liste de divergences (meme format que reconcile_alpaca).
        """
        try:
            from core.broker.ibkr_adapter import IBKRBroker
        except ImportError:
            logger.info("IBKRBroker non disponible — skip IBKR reconciliation")
            return []

        try:
            broker = IBKRBroker()
            broker_positions = broker.get_positions()
        except Exception as e:
            # IBKR peut ne pas etre configure — pas critique
            logger.info(f"IBKR non connecte (normal si non configure): {e}")
            return []

        state = self._load_internal_state()
        internal_symbols = self._get_internal_symbols(state)

        return self._compare_positions(
            broker_name="ibkr",
            internal_symbols=internal_symbols,
            broker_positions=broker_positions,
            state=state,
        )

    def _compare_positions(
        self,
        broker_name: str,
        internal_symbols: dict[str, dict],
        broker_positions: list[dict],
        state: dict,
    ) -> list[dict]:
        """Compare les positions internes vs broker.

        Args:
            broker_name: "alpaca" ou "ibkr"
            internal_symbols: {symbol: {source, strategy, direction}}
            broker_positions: [{symbol, qty, side, avg_entry, market_val, unrealized_pl}]
            state: le state complet

        Returns:
            Liste de divergences
        """
        divergences = []

        broker_by_symbol = {p["symbol"]: p for p in broker_positions}
        broker_symbols = set(broker_by_symbol.keys())
        internal_set = set(internal_symbols.keys())

        # 1. Positions orphelines (chez le broker mais pas dans le state)
        orphans = broker_symbols - internal_set
        for sym in sorted(orphans):
            pos = broker_by_symbol[sym]
            qty = pos.get("qty", 0)
            market_val = pos.get("market_val", 0)
            divergences.append({
                "type": "orphan",
                "broker": broker_name,
                "symbol": sym,
                "detail": (
                    f"Position dans {broker_name} mais pas dans le state: "
                    f"qty={qty}, val=${market_val:,.2f}"
                ),
                "severity": "critical" if abs(market_val) > 100 else "warning",
                "broker_qty": qty,
                "broker_val": market_val,
            })

        # 2. Positions manquantes (dans le state mais pas chez le broker)
        missing = internal_set - broker_symbols
        for sym in sorted(missing):
            info = internal_symbols[sym]
            divergences.append({
                "type": "missing",
                "broker": broker_name,
                "symbol": sym,
                "detail": (
                    f"Position dans le state ({info['source']}/{info['strategy']}) "
                    f"mais absente chez {broker_name}"
                ),
                "severity": "critical",
                "strategy": info["strategy"],
            })

        # 3. Positions presentes des deux cotes — verifier la coherence
        common = internal_set & broker_symbols
        for sym in sorted(common):
            broker_pos = broker_by_symbol[sym]
            internal_info = internal_symbols[sym]

            # Verifier la direction
            broker_side = broker_pos.get("side", "").upper()
            # Normaliser : "long" -> "LONG", "buy" -> "LONG"
            if broker_side in ("LONG", "BUY"):
                broker_direction = "LONG"
            elif broker_side in ("SHORT", "SELL"):
                broker_direction = "SHORT"
            else:
                broker_direction = broker_side

            internal_direction = internal_info.get("direction", "").upper()

            if broker_direction and internal_direction:
                if broker_direction != internal_direction:
                    divergences.append({
                        "type": "direction_mismatch",
                        "broker": broker_name,
                        "symbol": sym,
                        "detail": (
                            f"Direction diverge: state={internal_direction}, "
                            f"{broker_name}={broker_direction}"
                        ),
                        "severity": "critical",
                    })

        return divergences

    def run(self) -> dict:
        """Execute la reconciliation complete.

        Returns:
            {
                "timestamp": str (ISO),
                "divergences": [...],
                "status": "OK" | "DIVERGENCE",
                "summary": {
                    "alpaca_checked": bool,
                    "ibkr_checked": bool,
                    "total_divergences": int,
                    "critical_count": int,
                    "warning_count": int,
                },
            }
        """
        all_divergences = []

        # Reconciliation Alpaca
        alpaca_checked = False
        try:
            alpaca_divs = self.reconcile_alpaca()
            all_divergences.extend(alpaca_divs)
            alpaca_checked = True
        except Exception as e:
            logger.error(f"Erreur reconciliation Alpaca: {e}")

        # Reconciliation IBKR
        ibkr_checked = False
        try:
            ibkr_divs = self.reconcile_ibkr()
            all_divergences.extend(ibkr_divs)
            ibkr_checked = True
        except Exception as e:
            logger.info(f"IBKR reconciliation non disponible: {e}")

        # Compter les severites
        critical_count = sum(
            1 for d in all_divergences if d.get("severity") == "critical"
        )
        warning_count = sum(
            1 for d in all_divergences if d.get("severity") == "warning"
        )

        status = "DIVERGENCE" if all_divergences else "OK"

        # Log les resultats
        if all_divergences:
            logger.warning(
                f"RECONCILIATION: {len(all_divergences)} divergence(s) detectee(s) "
                f"({critical_count} critiques, {warning_count} warnings)"
            )
            for d in all_divergences:
                level = logger.critical if d.get("severity") == "critical" else logger.warning
                level(f"  [{d['type']}] {d['symbol']}: {d['detail']}")

            # Alerte Telegram si des divergences critiques
            if critical_count > 0:
                try:
                    from core.telegram_alert import send_alert
                    msg = (
                        f"RECONCILIATION: {critical_count} divergence(s) critique(s)\n\n"
                        + "\n".join(
                            f"- [{d['type']}] {d['symbol']}: {d['detail']}"
                            for d in all_divergences
                            if d.get("severity") == "critical"
                        )
                    )
                    send_alert(msg, level="critical")
                except Exception:
                    pass
        else:
            logger.info("RECONCILIATION OK: aucune divergence detectee")

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "divergences": all_divergences,
            "status": status,
            "summary": {
                "alpaca_checked": alpaca_checked,
                "ibkr_checked": ibkr_checked,
                "total_divergences": len(all_divergences),
                "critical_count": critical_count,
                "warning_count": warning_count,
            },
        }


def main():
    parser = argparse.ArgumentParser(description="Reconciliation des positions")
    parser.add_argument(
        "--json", action="store_true",
        help="Sortie JSON (pour integration avec d'autres outils)"
    )
    args = parser.parse_args()

    reconciler = PositionReconciler()
    result = reconciler.run()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  RECONCILIATION — {result['timestamp']}")
        print(f"  Status: {result['status']}")
        print(f"  Divergences: {result['summary']['total_divergences']}")
        print(f"    Critiques: {result['summary']['critical_count']}")
        print(f"    Warnings: {result['summary']['warning_count']}")
        print(f"{'='*60}")

        if result["divergences"]:
            print()
            for d in result["divergences"]:
                icon = "!!" if d.get("severity") == "critical" else " ?"
                print(f"  {icon} [{d['type']}] {d['symbol']}: {d['detail']}")
            print()


if __name__ == "__main__":
    main()
