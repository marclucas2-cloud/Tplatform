"""
Interface abstraite pour tous les brokers.

Chaque broker (Alpaca, IBKR, etc.) doit implementer cette interface.
Le pipeline paper_portfolio.py utilise UNIQUEMENT cette interface,
il ne connait pas le broker concret.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerError(Exception):
    """Erreur broker generique."""
    pass


class BaseBroker(ABC):
    """Interface commune a tous les brokers."""

    @abstractmethod
    def authenticate(self) -> dict:
        """Verifie la connexion et retourne les infos du compte.

        Returns:
            {status, equity, cash, buying_power, currency, paper, account_number}
        """

    @abstractmethod
    def get_account_info(self) -> dict:
        """Retourne le solde et les infos du compte."""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Retourne toutes les positions ouvertes.

        Returns:
            [{symbol, qty, side, avg_entry, market_val, unrealized_pl}]
        """

    @abstractmethod
    def get_orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        """Retourne les ordres filtres par status.

        Args:
            status: "open", "closed", "all"
            limit: nombre max d'ordres

        Returns:
            [{order_id, symbol, side, type, status, qty, filled_qty, filled_price, created_at}]
        """

    @abstractmethod
    def create_position(
        self,
        symbol: str,
        direction: str,
        qty: float | None = None,
        notional: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        _authorized_by: str | None = None,
    ) -> dict:
        """Ouvre une position avec bracket order optionnel.

        Args:
            symbol: ticker
            direction: "BUY" ou "SELL"
            qty: nombre d'actions
            notional: montant en $ (alternatif a qty, longs uniquement)
            stop_loss: prix du stop loss
            take_profit: prix du take profit
            _authorized_by: identifiant du pipeline appelant (obligatoire)

        Returns:
            {orderId, symbol, side, status, qty, filled_qty, filled_price,
             stop_loss, take_profit, bracket, paper, authorized_by}
        """

    @abstractmethod
    def close_position(self, symbol: str, _authorized_by: str | None = None) -> dict:
        """Ferme toute la position ouverte sur un symbole.

        Returns:
            {orderId, symbol, status}
        """

    @abstractmethod
    def close_all_positions(self, _authorized_by: str | None = None) -> list[dict]:
        """Ferme toutes les positions (emergency stop).

        Returns:
            [{orderId}]
        """

    @abstractmethod
    def cancel_all_orders(self, _authorized_by: str | None = None) -> int:
        """Annule tous les ordres pendants.

        Returns:
            Nombre d'ordres annules.
        """

    @abstractmethod
    def get_prices(
        self,
        symbol: str,
        timeframe: str = "1D",
        bars: int = 500,
        start: str = "",
        end: str = "",
    ) -> dict:
        """Recupere les prix historiques OHLCV.

        Returns:
            {bars: [{t, o, h, l, c, v}], symbol, timeframe}
        """

    # --- Proprietes informatives ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom du broker (ex: 'alpaca', 'ibkr')."""

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """True si mode paper trading."""
