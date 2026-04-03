"""
Adapter Alpaca — wrappe AlpacaClient existant dans l'interface BaseBroker.

Ne modifie PAS le client Alpaca existant, l'enveloppe simplement.
"""
from __future__ import annotations

import logging

from core.broker.base import BaseBroker, BrokerError

logger = logging.getLogger(__name__)


class AlpacaBroker(BaseBroker):
    """Broker Alpaca via l'AlpacaClient existant."""

    def __init__(self):
        from core.alpaca_client.client import AlpacaClient
        self._client = AlpacaClient.from_env()

    @property
    def name(self) -> str:
        return "alpaca"

    @property
    def is_paper(self) -> bool:
        return self._client._paper

    def authenticate(self) -> dict:
        return self._client.authenticate()

    def get_account_info(self) -> dict:
        return self._client.get_account_info()

    def get_positions(self) -> list[dict]:
        return self._client.get_positions()

    def get_orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        client = self._client._get_trading_client()
        orders = client.get_orders(filter=GetOrdersRequest(status=status, limit=limit))
        return [
            {
                "order_id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "type": o.type.value if o.type else "market",
                "status": o.status.value,
                "qty": str(o.qty),
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "filled_price": float(o.filled_avg_price) if o.filled_avg_price else 0,
                "created_at": o.created_at.isoformat() if o.created_at else "",
            }
            for o in orders
        ]

    def create_position(self, symbol, direction, qty=None, notional=None,
                        stop_loss=None, take_profit=None, _authorized_by=None) -> dict:
        return self._client.create_position(
            symbol=symbol, direction=direction, qty=qty, notional=notional,
            stop_loss=stop_loss, take_profit=take_profit,
            _authorized_by=_authorized_by,
        )

    def close_position(self, symbol, _authorized_by=None) -> dict:
        return self._client.close_position(symbol, _authorized_by=_authorized_by)

    def close_all_positions(self, _authorized_by=None) -> list[dict]:
        return self._client.close_all_positions(_authorized_by=_authorized_by)

    def cancel_all_orders(self, _authorized_by=None) -> int:
        if _authorized_by is None:
            raise BrokerError("cancel_all_orders() sans _authorized_by.")
        client = self._client._get_trading_client()
        responses = client.cancel_orders()
        logger.warning("Alpaca: tous les ordres annules")
        return len(responses) if responses else 0

    def get_prices(self, symbol, timeframe="1D", bars=500, start="", end="") -> dict:
        return self._client.get_prices(symbol, timeframe, bars, start, end)
