"""
Adapter Interactive Brokers — interface BaseBroker pour IBKR.

Utilise la librairie `ib_insync` pour se connecter a TWS/IB Gateway.
Connexion socket sur localhost (TWS port 7497 paper / 7496 live,
IB Gateway port 4002 paper / 4001 live).

Installation : pip install ib_insync
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from core.broker.base import BaseBroker, BrokerError

logger = logging.getLogger(__name__)

# Retry config pour reconnexion IBKR
_MAX_RECONNECT_ATTEMPTS = 5
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0


class IBKRBroker(BaseBroker):
    """Broker Interactive Brokers via ib_insync."""

    def __init__(self):
        # Fix Python 3.14 : eventkit requiert un event loop
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        try:
            from ib_insync import IB
        except ImportError:
            raise BrokerError(
                "ib_insync non installe. pip install ib_insync"
            )

        self._ib = IB()
        self._paper = os.getenv("IBKR_PAPER", "true").lower() == "true"
        self._host = os.getenv("IBKR_HOST", "127.0.0.1")
        self._port = int(os.getenv("IBKR_PORT", "7497" if self._paper else "7496"))
        self._client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))
        self._connected = False
        self._permanently_down = False
        self._reconnect_attempts = 0

    def _ensure_connected(self):
        """Connexion lazy avec retry backoff exponentiel.

        Tente de se connecter jusqu'a _MAX_RECONNECT_ATTEMPTS fois
        avec un backoff exponentiel (1s, 2s, 4s, 8s, max 30s).
        Si toutes les tentatives echouent, marque le broker comme
        permanently down et leve une BrokerError.
        """
        if self._permanently_down:
            raise BrokerError(
                "IBKR permanently down — toutes les tentatives de reconnexion "
                "ont echoue. Redemarrez le worker apres avoir verifie TWS/IB Gateway."
            )

        if self._connected and self._ib.isConnected():
            return

        backoff = _INITIAL_BACKOFF_SECONDS
        last_error = None

        for attempt in range(1, _MAX_RECONNECT_ATTEMPTS + 1):
            try:
                self._ib.connect(
                    self._host, self._port, clientId=self._client_id,
                    timeout=10,
                )
                self._connected = True
                self._reconnect_attempts = 0
                mode = "PAPER" if self._paper else "LIVE"
                if attempt > 1:
                    logger.warning(
                        f"IBKR reconnecte ({mode}) apres {attempt} tentatives "
                        f"sur {self._host}:{self._port}"
                    )
                else:
                    logger.info(
                        f"IBKR connecte ({mode}) sur {self._host}:{self._port}"
                    )
                return
            except Exception as e:
                last_error = e
                self._reconnect_attempts = attempt
                if attempt < _MAX_RECONNECT_ATTEMPTS:
                    logger.warning(
                        f"IBKR connexion echouee (tentative {attempt}/{_MAX_RECONNECT_ATTEMPTS}) "
                        f"— retry dans {backoff:.0f}s — {e}"
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        # Toutes les tentatives ont echoue
        self._permanently_down = True
        self._connected = False
        logger.critical(
            f"IBKR PERMANENTLY DOWN apres {_MAX_RECONNECT_ATTEMPTS} tentatives. "
            f"Derniere erreur : {last_error}"
        )
        raise BrokerError(
            f"IBKR permanently down apres {_MAX_RECONNECT_ATTEMPTS} tentatives "
            f"sur {self._host}:{self._port} — {last_error}. "
            f"Verifiez que TWS/IB Gateway est demarre."
        )

    def health_check(self) -> bool:
        """Verifie si le broker est operationnel.

        Returns:
            True si connecte et operationnel, False sinon.
        """
        if self._permanently_down:
            return False
        try:
            return self._connected and self._ib.isConnected()
        except Exception:
            return False

    @property
    def name(self) -> str:
        return "ibkr"

    @property
    def is_paper(self) -> bool:
        return self._paper

    def authenticate(self) -> dict:
        self._ensure_connected()
        accounts = self._ib.managedAccounts()
        if not accounts:
            raise BrokerError("IBKR: aucun compte trouve")

        # Recuperer le premier compte
        account_id = accounts[0]
        summary = {tag.tag: tag.value for tag in self._ib.accountSummary(account_id)}

        equity = float(summary.get("NetLiquidation", 0))
        cash = float(summary.get("TotalCashValue", 0))
        buying_power = float(summary.get("BuyingPower", 0))

        mode = "PAPER" if self._paper else "LIVE"
        logger.info(f"IBKR authentifie ({mode}) — equity={equity}, cash={cash}")

        return {
            "status": "ACTIVE",
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "currency": summary.get("Currency", "USD"),
            "paper": self._paper,
            "account_number": account_id,
        }

    def get_account_info(self) -> dict:
        return self.authenticate()

    def get_positions(self) -> list[dict]:
        self._ensure_connected()
        positions = self._ib.positions()
        return [
            {
                "symbol": p.contract.symbol,
                "qty": float(p.position),
                "side": "long" if p.position > 0 else "short",
                "avg_entry": float(p.avgCost),
                "market_val": float(p.position * p.marketPrice)
                    if hasattr(p, "marketPrice") else 0,
                "unrealized_pl": float(p.unrealizedPNL)
                    if hasattr(p, "unrealizedPNL") else 0,
            }
            for p in positions
        ]

    def get_orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        self._ensure_connected()
        if status == "open":
            orders = self._ib.openOrders()
        else:
            orders = self._ib.trades()

        result = []
        for trade in orders[:limit]:
            o = trade.order if hasattr(trade, "order") else trade
            result.append({
                "order_id": str(o.orderId),
                "symbol": trade.contract.symbol if hasattr(trade, "contract") else "",
                "side": o.action,
                "type": o.orderType,
                "status": trade.orderStatus.status if hasattr(trade, "orderStatus") else "",
                "qty": str(o.totalQuantity),
                "filled_qty": float(trade.orderStatus.filled)
                    if hasattr(trade, "orderStatus") else 0,
                "filled_price": float(trade.orderStatus.avgFillPrice)
                    if hasattr(trade, "orderStatus") else 0,
                "created_at": "",
            })
        return result

    def create_position(self, symbol, direction, qty=None, notional=None,
                        stop_loss=None, take_profit=None, _authorized_by=None) -> dict:
        if _authorized_by is None:
            raise BrokerError(
                f"Ordre REFUSE pour {symbol}: create_position() sans _authorized_by."
            )

        # GUARD paper
        if not self._paper:
            logger.critical("ABORT: IBKR LIVE trading bloque.")
            raise BrokerError("Trading LIVE bloque. Settez IBKR_PAPER=true.")

        self._ensure_connected()
        from ib_insync import Stock, MarketOrder, StopOrder, LimitOrder

        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)

        action = "BUY" if direction.upper() == "BUY" else "SELL"

        # Calculer qty si notional fourni
        if qty is None and notional:
            ticker = self._ib.reqMktData(contract, snapshot=True)
            self._ib.sleep(2)
            price = ticker.marketPrice()
            if price and price > 0:
                qty = int(notional / price)
            else:
                raise BrokerError(f"IBKR: impossible d'obtenir le prix de {symbol}")

        if not qty or qty <= 0:
            raise BrokerError(f"IBKR: qty invalide pour {symbol}: {qty}")

        # Ordre principal (market)
        parent_order = MarketOrder(action, qty)
        parent_order.transmit = not (stop_loss or take_profit)

        trade = self._ib.placeOrder(contract, parent_order)
        self._ib.sleep(1)

        # Bracket : stop loss
        if stop_loss and stop_loss > 0:
            sl_action = "SELL" if action == "BUY" else "BUY"
            sl_order = StopOrder(sl_action, qty, round(stop_loss, 2))
            sl_order.parentId = parent_order.orderId
            sl_order.transmit = take_profit is None
            self._ib.placeOrder(contract, sl_order)

        # Bracket : take profit
        if take_profit and take_profit > 0:
            tp_action = "SELL" if action == "BUY" else "BUY"
            tp_order = LimitOrder(tp_action, qty, round(take_profit, 2))
            tp_order.parentId = parent_order.orderId
            tp_order.transmit = True  # dernier leg = transmit
            self._ib.placeOrder(contract, tp_order)

        bracket_info = ""
        if stop_loss:
            bracket_info += f" SL=${stop_loss:.2f}"
        if take_profit:
            bracket_info += f" TP=${take_profit:.2f}"

        logger.info(
            f"IBKR ordre soumis: {direction} {symbol} qty={qty}{bracket_info} "
            f"— orderId={parent_order.orderId}"
        )

        fill = trade.orderStatus
        return {
            "orderId": str(parent_order.orderId),
            "symbol": symbol,
            "side": action,
            "status": fill.status if fill else "Submitted",
            "qty": str(qty),
            "filled_qty": float(fill.filled) if fill else 0,
            "filled_price": float(fill.avgFillPrice) if fill and fill.avgFillPrice else None,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "bracket": bool(stop_loss or take_profit),
            "paper": self._paper,
            "authorized_by": _authorized_by,
        }

    def close_position(self, symbol, _authorized_by=None) -> dict:
        if _authorized_by is None:
            raise BrokerError(f"close_position({symbol}) sans _authorized_by.")
        self._ensure_connected()
        from ib_insync import Stock, MarketOrder

        # Trouver la position
        positions = self._ib.positions()
        pos = next((p for p in positions if p.contract.symbol == symbol), None)
        if not pos:
            raise BrokerError(f"IBKR: pas de position ouverte sur {symbol}")

        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)

        action = "SELL" if pos.position > 0 else "BUY"
        qty = abs(pos.position)
        order = MarketOrder(action, qty)
        trade = self._ib.placeOrder(contract, order)

        logger.info(f"IBKR: position {symbol} fermee ({action} {qty})")
        return {
            "orderId": str(order.orderId),
            "symbol": symbol,
            "status": "Submitted",
        }

    def close_all_positions(self, _authorized_by=None) -> list[dict]:
        if _authorized_by is None:
            raise BrokerError("close_all_positions() sans _authorized_by.")
        self._ensure_connected()

        results = []
        positions = self._ib.positions()
        for pos in positions:
            try:
                r = self.close_position(
                    pos.contract.symbol, _authorized_by=_authorized_by
                )
                results.append(r)
            except Exception as e:
                logger.error(f"IBKR: erreur fermeture {pos.contract.symbol}: {e}")
        return results

    def cancel_all_orders(self, _authorized_by=None) -> int:
        if _authorized_by is None:
            raise BrokerError("cancel_all_orders() sans _authorized_by.")
        self._ensure_connected()
        self._ib.reqGlobalCancel()
        logger.warning("IBKR: tous les ordres annules (reqGlobalCancel)")
        return -1  # IBKR ne retourne pas le nombre

    def get_prices(self, symbol, timeframe="1D", bars=500, start="", end="") -> dict:
        self._ensure_connected()
        from ib_insync import Stock, util
        import datetime

        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)

        # Mapping timeframe
        tf_map = {
            "1M": "1 min", "5M": "5 mins", "15M": "15 mins",
            "30M": "30 mins", "1H": "1 hour", "4H": "4 hours",
            "1D": "1 day", "1W": "1 week",
        }
        bar_size = tf_map.get(timeframe, "1 day")

        # Calculer la duree
        if timeframe in ("1M", "5M", "15M", "30M"):
            duration = f"{min(bars // 60 + 5, 30)} D"
        elif timeframe in ("1H", "4H"):
            duration = f"{min(bars // 6 + 5, 365)} D"
        else:
            duration = f"{min(bars + 30, 365)} D"

        ibkr_bars = self._ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
        )

        result = []
        for bar in ibkr_bars[-bars:]:
            result.append({
                "t": bar.date.isoformat() if hasattr(bar.date, "isoformat")
                     else str(bar.date),
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            })

        logger.info(f"IBKR: {len(result)} barres {timeframe} pour {symbol}")
        return {"bars": result, "symbol": symbol, "timeframe": timeframe}

    def disconnect(self):
        """Deconnexion propre."""
        if self._connected:
            self._ib.disconnect()
            self._connected = False
            logger.info("IBKR deconnecte")
