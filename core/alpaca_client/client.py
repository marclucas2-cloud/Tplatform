"""
Client Alpaca Markets — paper trading et live US equities.

Couvre :
  - Authentification (API key + secret)
  - Données historiques OHLCV (StockHistoricalDataClient)
  - Création / fermeture de positions (TradingClient)
  - Consultation du compte (solde, positions ouvertes)

Paper trading : base_url = https://paper-api.alpaca.markets
Live trading  : base_url = https://api.alpaca.markets (PAPER_TRADING=false)

Documentation : https://docs.alpaca.markets/reference/
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Mapping timeframe interne → TimeFrame Alpaca
_TF_MAP = {
    "1M":  ("Minute", 1),
    "5M":  ("Minute", 5),
    "15M": ("Minute", 15),
    "30M": ("Minute", 30),
    "1H":  ("Hour", 1),
    "4H":  ("Hour", 4),
    "1D":  ("Day", 1),
    "1W":  ("Week", 1),
}


class AlpacaAuthError(Exception):
    pass


class AlpacaAPIError(Exception):
    def __init__(self, message: str):
        super().__init__(f"Alpaca API error: {message}")


class AlpacaClient:
    """
    Client Alpaca — interface miroir d'IGClient pour compatibilité
    avec l'ExecutionAgent et l'OHLCVLoader.

    Usage :
        client = AlpacaClient.from_env()
        info   = client.authenticate()
        data   = client.get_prices("IWM", "1D", bars=500)
        order  = client.create_position("IWM", "BUY", qty=10)
    """

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self._api_key   = api_key
        self._secret_key = secret_key
        self._paper     = paper
        self._trading   = None   # TradingClient (lazy)
        self._data      = None   # StockHistoricalDataClient (lazy)

    @classmethod
    def from_env(cls) -> "AlpacaClient":
        """Construit le client depuis les variables d'environnement."""
        required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise AlpacaAuthError(f"Variables d'environnement Alpaca manquantes : {missing}")

        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
        return cls(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
            paper=paper,
        )

    def _get_trading_client(self):
        if self._trading is None:
            try:
                from alpaca.trading.client import TradingClient
            except ImportError:
                raise AlpacaAuthError("alpaca-py non installé — pip install alpaca-py")
            self._trading = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=self._paper,
            )
        return self._trading

    def _get_data_client(self):
        if self._data is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
            except ImportError:
                raise AlpacaAuthError("alpaca-py non installé — pip install alpaca-py")
            self._data = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._data

    # ─── Authentification ────────────────────────────────────────────────────

    def authenticate(self) -> dict:
        """
        Vérifie la connexion et retourne les infos du compte.
        Équivalent d'IGClient.authenticate().
        """
        try:
            client = self._get_trading_client()
            account = client.get_account()
            mode = "PAPER" if self._paper else "LIVE"
            logger.info(
                f"Alpaca authentifié ({mode}) — "
                f"equity={account.equity}, buying_power={account.buying_power}"
            )
            return {
                "status":        account.status,
                "equity":        float(account.equity),
                "cash":          float(account.cash),
                "buying_power":  float(account.buying_power),
                "currency":      account.currency,
                "paper":         self._paper,
                "account_number": account.account_number,
            }
        except Exception as e:
            raise AlpacaAuthError(f"Authentification Alpaca échouée : {e}")

    # ─── Compte ──────────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Retourne le solde et les infos du compte."""
        return self.authenticate()

    def get_positions(self) -> list[dict]:
        """Retourne toutes les positions ouvertes."""
        client = self._get_trading_client()
        positions = client.get_all_positions()
        return [
            {
                "symbol":     p.symbol,
                "qty":        float(p.qty),
                "side":       p.side.value,
                "avg_entry":  float(p.avg_entry_price),
                "market_val": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ]

    # ─── Prix historiques ────────────────────────────────────────────────────

    def get_prices(self, symbol: str, timeframe: str = "1D",
                   bars: int = 500, start: str = "", end: str = "") -> dict:
        """
        Récupère les prix historiques OHLCV.

        symbol    : ticker US (ex: "IWM", "AAPL", "SPY")
        timeframe : "1M", "5M", "15M", "1H", "4H", "1D", "1W"
        bars      : nombre de barres (max 10 000)
        start/end : dates ISO optionnelles "YYYY-MM-DD"

        Retourne un dict {"bars": [...]} pour compatibilité avec OHLCVLoader.
        """
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            import datetime
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        tf_unit, tf_val = _TF_MAP.get(timeframe, ("Day", 1))
        if tf_unit == "Day":
            alpaca_tf = TimeFrame.Day
        elif tf_unit == "Hour":
            alpaca_tf = TimeFrame.Hour
        elif tf_unit == "Week":
            alpaca_tf = TimeFrame.Week
        else:
            alpaca_tf = TimeFrame(tf_val, TimeFrameUnit.Minute)

        kwargs: dict[str, Any] = {"symbol_or_symbols": symbol, "timeframe": alpaca_tf}

        if start:
            kwargs["start"] = datetime.datetime.fromisoformat(start).replace(
                tzinfo=datetime.timezone.utc)
        else:
            # Pas de date start → calculer depuis bars estimées
            if tf_unit == "Day":
                days_back = bars * 1.5  # marge pour weekends/fériés
            elif tf_unit == "Hour":
                days_back = (bars / 6.5) * 1.5
            else:
                days_back = (bars / (6.5 * 60 / tf_val)) * 1.5
            kwargs["start"] = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=max(int(days_back), 30))
            )

        if end:
            kwargs["end"] = datetime.datetime.fromisoformat(end).replace(
                tzinfo=datetime.timezone.utc)

        data_client = self._get_data_client()
        request = StockBarsRequest(**kwargs)
        bars_data = data_client.get_stock_bars(request)

        result = []
        for bar in bars_data[symbol]:
            result.append({
                "t": bar.timestamp.isoformat(),
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            })

        logger.info(f"Alpaca : {len(result)} barres {timeframe} pour {symbol}")
        return {"bars": result, "symbol": symbol, "timeframe": timeframe}

    # ─── Ordres ──────────────────────────────────────────────────────────────

    def create_position(self, symbol: str, direction: str,
                        qty: float | None = None,
                        notional: float | None = None) -> dict:
        """
        Ouvre une position market order.

        symbol    : ticker US
        direction : "BUY" ou "SELL" (short)
        qty       : nombre d'actions (ou notional en $)
        notional  : montant en $ (alternatif à qty)
        """
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        side = OrderSide.BUY if direction.upper() == "BUY" else OrderSide.SELL

        if notional:
            request = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        else:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )

        client = self._get_trading_client()
        order = client.submit_order(request)

        logger.info(
            f"Alpaca ordre soumis : {direction} {symbol} "
            f"qty={qty or ''} notional={notional or ''} — id={order.id}"
        )
        return {
            "orderId":   str(order.id),
            "symbol":    order.symbol,
            "side":      order.side.value,
            "status":    order.status.value,
            "qty":       str(order.qty),
            "paper":     self._paper,
        }

    def close_position(self, symbol: str) -> dict:
        """Ferme toute la position ouverte sur un symbole."""
        client = self._get_trading_client()
        response = client.close_position(symbol)
        logger.info(f"Alpaca : position {symbol} fermée")
        return {
            "orderId": str(response.id),
            "symbol":  response.symbol,
            "status":  response.status.value,
        }

    def close_all_positions(self) -> list[dict]:
        """Ferme toutes les positions ouvertes (emergency stop)."""
        client = self._get_trading_client()
        responses = client.close_all_positions(cancel_orders=True)
        logger.warning(f"Alpaca : toutes les positions fermées ({len(responses)} ordres)")
        return [{"orderId": str(r.id)} for r in responses]
