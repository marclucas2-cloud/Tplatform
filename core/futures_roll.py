"""
Futures Roll Manager — detecte, execute et journalise les rolls de contrats.

Le roll consiste a fermer la position sur le front month et rouvrir
sur le next month, quelques jours avant l'expiration.

Regles :
  - Roll declenche 5 jours avant expiry (configurable)
  - Roll execute en market order (spread leg-by-leg, pas de spread order)
  - Slippage journalise pour audit
  - Guard _authorized_by obligatoire
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core.broker.ibkr_futures import FuturesContractManager

logger = logging.getLogger(__name__)

_ROLL_LOG_PATH = Path(__file__).parent.parent / "output" / "futures_rolls.jsonl"


class FuturesRollManager:
    """Gere les rolls de contrats futures.

    Usage:
        from core.broker.ibkr_adapter import IBKRBroker
        from core.broker.ibkr_futures import IBKRFuturesClient

        broker = IBKRBroker()
        futures_client = IBKRFuturesClient(broker)
        roll_mgr = FuturesRollManager(futures_client)

        # Verifier et executer les rolls necessaires
        results = roll_mgr.check_and_execute_rolls(["MES", "MNQ"])
    """

    def __init__(
        self,
        futures_client=None,
        contract_mgr: FuturesContractManager | None = None,
        days_before_expiry: int = 5,
    ):
        """
        Args:
            futures_client: IBKRFuturesClient pour executer les ordres (None = dry-run)
            contract_mgr: gestionnaire de contrats (cree un par defaut)
            days_before_expiry: nombre de jours avant expiry pour roller
        """
        self._client = futures_client
        self._contract_mgr = contract_mgr or FuturesContractManager()
        self._days_before = days_before_expiry

    def check_and_execute_roll(
        self,
        symbol: str,
        current_qty: int = 0,
        current_direction: str = "BUY",
        ref_date: date | None = None,
        _authorized_by: str | None = None,
    ) -> dict | None:
        """Verifie si un roll est necessaire et l'execute.

        Args:
            symbol: symbole futures (ex. "MES")
            current_qty: nombre de contrats en position
            current_direction: "BUY" (long) ou "SELL" (short)
            ref_date: date de reference (defaut: aujourd'hui)
            _authorized_by: identifiant du pipeline

        Returns:
            Dict avec details du roll si execute, None sinon.
            {symbol, old_contract, new_contract, qty, direction,
             old_expiry, new_expiry, slippage_estimate, executed_at}
        """
        if ref_date is None:
            ref_date = date.today()

        if not self._contract_mgr.should_roll(
            symbol, self._days_before, ref_date
        ):
            return None

        if current_qty == 0:
            logger.info(f"Roll signale pour {symbol} mais pas de position ouverte.")
            return None

        front = self._contract_mgr.get_front_month(symbol, ref_date)
        next_m = self._contract_mgr.get_next_month(symbol, ref_date)

        roll_info = {
            "symbol": symbol,
            "old_contract": front["local_symbol"],
            "new_contract": next_m["local_symbol"],
            "qty": current_qty,
            "direction": current_direction,
            "old_expiry": front["expiry"],
            "new_expiry": next_m["expiry"],
            "slippage_estimate": None,
            "executed_at": datetime.now().isoformat(),
            "status": "pending",
        }

        # Executer le roll si un client futures est disponible
        if self._client and _authorized_by:
            try:
                # Etape 1 : Fermer le front month
                logger.info(
                    f"ROLL {symbol}: fermeture {front['local_symbol']} "
                    f"({current_direction} x{current_qty})"
                )
                close_result = self._client.close_futures_position(
                    symbol, _authorized_by=_authorized_by
                )

                # Etape 2 : Ouvrir le next month
                logger.info(
                    f"ROLL {symbol}: ouverture {next_m['local_symbol']} "
                    f"({current_direction} x{current_qty})"
                )
                open_result = self._client.create_futures_position(
                    symbol=symbol,
                    direction=current_direction,
                    qty=current_qty,
                    _authorized_by=_authorized_by,
                )

                # Calculer le slippage estime
                close_price = close_result.get("filled_price")
                open_price = open_result.get("filled_price")
                if close_price and open_price:
                    spec = self._contract_mgr.get_contract_spec(symbol)
                    slippage_pts = abs(open_price - close_price)
                    slippage_usd = slippage_pts * spec["point_value"] * current_qty
                    roll_info["slippage_estimate"] = round(slippage_usd, 2)

                roll_info["status"] = "executed"
                logger.info(
                    f"ROLL {symbol} execute: {front['local_symbol']} → "
                    f"{next_m['local_symbol']} (slippage≈${roll_info['slippage_estimate']})"
                )

            except Exception as e:
                roll_info["status"] = "failed"
                roll_info["error"] = str(e)
                logger.error(f"ROLL {symbol} ECHOUE: {e}")

        else:
            roll_info["status"] = "dry_run"
            logger.info(
                f"ROLL {symbol} (dry-run): {front['local_symbol']} → "
                f"{next_m['local_symbol']}"
            )

        # Journaliser
        self.log_roll(
            symbol,
            front["local_symbol"],
            next_m["local_symbol"],
            roll_info.get("slippage_estimate"),
        )

        return roll_info

    def check_and_execute_rolls(
        self,
        symbols: list[str],
        positions: dict[str, dict] | None = None,
        ref_date: date | None = None,
        _authorized_by: str | None = None,
    ) -> list[dict]:
        """Verifie et execute les rolls pour une liste de symboles.

        Args:
            symbols: liste de symboles futures
            positions: {symbol: {qty, direction}} des positions ouvertes
            ref_date: date de reference
            _authorized_by: identifiant du pipeline

        Returns:
            Liste des rolls executes (non-None)
        """
        if positions is None:
            positions = {}

        results = []
        for sym in symbols:
            pos = positions.get(sym, {})
            qty = pos.get("qty", 0)
            direction = pos.get("direction", "BUY")

            result = self.check_and_execute_roll(
                symbol=sym,
                current_qty=qty,
                current_direction=direction,
                ref_date=ref_date,
                _authorized_by=_authorized_by,
            )
            if result:
                results.append(result)

        return results

    def get_roll_schedule(
        self, symbols: list[str] | None = None, ref_date: date | None = None
    ) -> list[dict]:
        """Retourne le calendrier des prochains rolls.

        Args:
            symbols: liste de symboles (defaut: tous les micro)
            ref_date: date de reference

        Returns:
            [{symbol, front_month, expiry, days_to_expiry, roll_date, needs_roll}]
            trie par days_to_expiry croissant
        """
        if ref_date is None:
            ref_date = date.today()

        if symbols is None:
            symbols = [
                s for s in self._contract_mgr.supported_symbols
                if self._contract_mgr.is_micro(s)
            ]

        schedule = []
        for sym in symbols:
            try:
                front = self._contract_mgr.get_front_month(sym, ref_date)
                expiry = date.fromisoformat(front["expiry"])
                days_to_expiry = (expiry - ref_date).days
                roll_date = expiry - timedelta(days=self._days_before)

                schedule.append({
                    "symbol": sym,
                    "front_month": front["local_symbol"],
                    "expiry": front["expiry"],
                    "days_to_expiry": days_to_expiry,
                    "roll_date": roll_date.isoformat(),
                    "needs_roll": days_to_expiry <= self._days_before,
                })
            except Exception as e:
                logger.warning(f"Erreur schedule pour {sym}: {e}")

        schedule.sort(key=lambda x: x["days_to_expiry"])
        return schedule

    def log_roll(
        self,
        symbol: str,
        old_contract: str,
        new_contract: str,
        slippage: float | None,
    ) -> None:
        """Journalise un roll dans un fichier JSONL pour audit.

        Args:
            symbol: symbole futures
            old_contract: contrat ferme (ex. "MESH26")
            new_contract: contrat ouvert (ex. "MESM26")
            slippage: slippage estime en USD (None si dry-run)
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "old_contract": old_contract,
            "new_contract": new_contract,
            "slippage_usd": slippage,
        }

        try:
            _ROLL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_ROLL_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
            logger.debug(f"Roll journalise: {entry}")
        except Exception as e:
            logger.warning(f"Impossible de journaliser le roll: {e}")
