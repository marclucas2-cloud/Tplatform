"""
Broker Factory — instancie le bon broker selon la config.

Supporte 3 modes :
  1. BROKER=alpaca   → Alpaca uniquement
  2. BROKER=ibkr     → IBKR uniquement
  3. BROKER=smart    → Smart routing (meilleur broker par actif/strategie)

Smart routing rules :
  - US equities intraday → Alpaca (simple, fiable, REST stateless)
  - Options              → IBKR (Alpaca ne supporte pas)
  - High-frequency (>200 trades/mois) → IBKR (commissions 7x moins cheres)
  - Crypto-proxies       → Alpaca (short plus simple)
  - Overnight/swing      → IBKR (meilleur short locate, pas de 24h disconnect issue)

Variables d'environnement :
  BROKER          : "alpaca" | "ibkr" | "smart" (default: "alpaca")
  ALPACA_API_KEY  : cle API Alpaca
  ALPACA_SECRET_KEY : secret Alpaca
  PAPER_TRADING   : "true" | "false"
  IBKR_HOST       : host TWS/Gateway (default: 127.0.0.1)
  IBKR_PORT       : port (default: 7497 paper, 7496 live)
  IBKR_CLIENT_ID  : client ID (default: 1)
  IBKR_PAPER      : "true" | "false" (default: "true")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from core.broker.base import BaseBroker, BrokerError

logger = logging.getLogger(__name__)

# Singleton cache pour eviter les reconnexions
_broker_cache: dict[str, BaseBroker] = {}


def get_broker(broker_type: str | None = None) -> BaseBroker:
    """Retourne une instance du broker demande (avec cache singleton).

    Args:
        broker_type: "alpaca", "ibkr", ou None (lit BROKER env var, default "alpaca")

    Returns:
        Instance BaseBroker
    """
    if broker_type is None:
        broker_type = os.getenv("BROKER", "alpaca").lower()

    if broker_type in _broker_cache:
        return _broker_cache[broker_type]

    if broker_type == "alpaca":
        from core.broker.alpaca_adapter import AlpacaBroker
        broker = AlpacaBroker()
    elif broker_type == "ibkr":
        from core.broker.ibkr_adapter import IBKRBroker
        broker = IBKRBroker()
    else:
        raise BrokerError(f"Broker inconnu: {broker_type}. Utiliser 'alpaca' ou 'ibkr'.")

    _broker_cache[broker_type] = broker
    logger.info(f"Broker instancie: {broker.name} (paper={broker.is_paper})")
    return broker


class SmartRouter:
    """Route les ordres vers le meilleur broker selon l'actif et la strategie.

    Usage:
        router = SmartRouter()
        broker = router.route(symbol="AAPL", strategy="opex_gamma", asset_type="equity")
    """

    # Regles de routage par defaut
    RULES = {
        # asset_type → broker prefere
        "option": "ibkr",       # Options = IBKR obligatoire
        "future": "ibkr",       # Futures = IBKR obligatoire
        "forex": "ibkr",        # Forex = IBKR obligatoire
        "equity": "alpaca",     # Equities US = Alpaca par defaut
        "crypto": "alpaca",     # Crypto = Alpaca
    }

    # Override par strategie (high-freq → IBKR pour commissions)
    STRATEGY_OVERRIDE = {
        # Strategies a haute frequence → IBKR si disponible
        # "vwap_micro": "ibkr",     # 363 trades/6 mois
        # "triple_ema": "ibkr",     # 360 trades/6 mois
        # "orb_v2": "ibkr",         # 220 trades/6 mois
    }

    def __init__(self):
        self._brokers: dict[str, BaseBroker] = {}
        self._available: set[str] = set()
        self._init_available_brokers()

    def _init_available_brokers(self):
        """Detecte quels brokers sont configurables."""
        # Alpaca : disponible si les cles API sont presentes
        if os.getenv("ALPACA_API_KEY"):
            self._available.add("alpaca")

        # IBKR : disponible si les variables de connexion sont presentes
        # Note: on ne teste pas la connexion ici (TWS pourrait ne pas tourner)
        if os.getenv("IBKR_HOST") or os.getenv("IBKR_PORT"):
            self._available.add("ibkr")

        logger.info(f"SmartRouter: brokers disponibles = {self._available or {'aucun'}}")

    def _get_broker(self, broker_type: str) -> BaseBroker:
        """Recupere ou instancie un broker."""
        if broker_type not in self._brokers:
            self._brokers[broker_type] = get_broker(broker_type)
        return self._brokers[broker_type]

    def route(
        self,
        symbol: str,
        strategy: str = "",
        asset_type: str = "equity",
    ) -> BaseBroker:
        """Determine le meilleur broker pour cet ordre.

        Args:
            symbol: ticker
            strategy: nom de la strategie (pour override)
            asset_type: "equity", "option", "future", "forex", "crypto"

        Returns:
            Le broker optimal
        """
        # 1. Check strategy override
        if strategy in self.STRATEGY_OVERRIDE:
            preferred = self.STRATEGY_OVERRIDE[strategy]
            if preferred in self._available:
                return self._get_broker(preferred)

        # 2. Check asset type rule
        preferred = self.RULES.get(asset_type, "alpaca")
        if preferred in self._available:
            return self._get_broker(preferred)

        # 3. Fallback sur ce qui est disponible
        if "alpaca" in self._available:
            return self._get_broker("alpaca")
        if "ibkr" in self._available:
            return self._get_broker("ibkr")

        raise BrokerError(
            "Aucun broker disponible. Configurez ALPACA_API_KEY ou IBKR_HOST."
        )

    def get_all_brokers(self) -> dict[str, BaseBroker]:
        """Retourne tous les brokers connectes (pour le dashboard)."""
        result = {}
        for name in self._available:
            try:
                result[name] = self._get_broker(name)
            except BrokerError:
                pass
        return result
