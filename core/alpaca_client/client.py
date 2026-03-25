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
            # GUARD CRITIQUE : empecher tout ordre live par erreur
            if not self._paper:
                logger.critical(
                    "ABORT: PAPER_TRADING=false detecte. "
                    "Le trading live n'est PAS autorise sans validation explicite. "
                    "Settez PAPER_TRADING=true dans l'environnement."
                )
                raise AlpacaAuthError(
                    "Trading LIVE bloque. Settez PAPER_TRADING=true."
                )
            try:
                from alpaca.trading.client import TradingClient
            except ImportError:
                raise AlpacaAuthError("alpaca-py non installe — pip install alpaca-py")
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

    def _get_crypto_client(self):
        if not hasattr(self, "_crypto") or self._crypto is None:
            try:
                from alpaca.data.historical import CryptoHistoricalDataClient
            except ImportError:
                raise AlpacaAuthError("alpaca-py non installé — pip install alpaca-py")
            self._crypto = CryptoHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._crypto

    # ─── Listing dynamique ────────────────────────────────────────────────────

    def list_tradable_assets(self, asset_class: str = "us_equity") -> list[dict]:
        """
        Liste tous les actifs tradables sur Alpaca.

        asset_class : "us_equity" ou "crypto"
        Retourne une liste de dicts {symbol, name, exchange, tradable, fractionable}.
        """
        client = self._get_trading_client()

        try:
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        ac = AssetClass.CRYPTO if asset_class == "crypto" else AssetClass.US_EQUITY
        request = GetAssetsRequest(asset_class=ac, status=AssetStatus.ACTIVE)
        assets = client.get_all_assets(request)

        result = []
        for a in assets:
            if a.tradable:
                result.append({
                    "symbol":       a.symbol,
                    "name":         a.name,
                    "exchange":     a.exchange.value if a.exchange else "",
                    "tradable":     a.tradable,
                    "fractionable": a.fractionable,
                })

        logger.info(f"Alpaca : {len(result)} actifs tradables ({asset_class})")
        return result

    # ─── Prix historiques ────────────────────────────────────────────────────

    def _build_timeframe(self, timeframe: str):
        """Convertit un timeframe interne en TimeFrame Alpaca."""
        try:
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        tf_unit, tf_val = _TF_MAP.get(timeframe, ("Day", 1))
        if tf_unit == "Day":
            return TimeFrame.Day, tf_unit, tf_val
        elif tf_unit == "Hour":
            return TimeFrame.Hour, tf_unit, tf_val
        elif tf_unit == "Week":
            return TimeFrame.Week, tf_unit, tf_val
        else:
            return TimeFrame(tf_val, TimeFrameUnit.Minute), tf_unit, tf_val

    def _build_date_kwargs(self, start: str, end: str,
                           bars: int, tf_unit: str, tf_val: int) -> dict:
        """Construit les kwargs start/end pour les requetes historiques."""
        import datetime
        kwargs: dict[str, Any] = {}

        if start:
            kwargs["start"] = datetime.datetime.fromisoformat(start).replace(
                tzinfo=datetime.timezone.utc)
        else:
            if tf_unit == "Day":
                days_back = bars * 1.5
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

        return kwargs

    @staticmethod
    def _bars_to_list(bars_data, symbol: str) -> list[dict]:
        """Convertit les barres Alpaca en liste de dicts OHLCV."""
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
        return result

    def get_prices(self, symbol: str, timeframe: str = "1D",
                   bars: int = 500, start: str = "", end: str = "") -> dict:
        """
        Récupère les prix historiques OHLCV (actions US + ETFs).

        symbol    : ticker US (ex: "IWM", "AAPL", "SPY")
        timeframe : "1M", "5M", "15M", "1H", "4H", "1D", "1W"
        bars      : nombre de barres (max 10 000)
        start/end : dates ISO optionnelles "YYYY-MM-DD"

        Retourne un dict {"bars": [...]} pour compatibilité avec OHLCVLoader.
        Pour les crypto, utiliser get_crypto_prices().
        """
        try:
            from alpaca.data.requests import StockBarsRequest
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        alpaca_tf, tf_unit, tf_val = self._build_timeframe(timeframe)
        date_kwargs = self._build_date_kwargs(start, end, bars, tf_unit, tf_val)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_tf,
            **date_kwargs,
        )

        data_client = self._get_data_client()
        bars_data = data_client.get_stock_bars(request)
        result = self._bars_to_list(bars_data, symbol)

        logger.info(f"Alpaca : {len(result)} barres {timeframe} pour {symbol}")
        return {"bars": result, "symbol": symbol, "timeframe": timeframe}

    def get_crypto_prices(self, symbol: str, timeframe: str = "1D",
                          bars: int = 500, start: str = "", end: str = "") -> dict:
        """
        Récupère les prix historiques OHLCV crypto.

        symbol    : paire crypto (ex: "BTC/USD", "ETH/USD")
                    Accepte aussi "BTC", "ETH" (auto-ajoute /USD)
        timeframe : "1M", "5M", "15M", "1H", "4H", "1D", "1W"
        bars      : nombre de barres (max 10 000)
        start/end : dates ISO optionnelles "YYYY-MM-DD"
        """
        try:
            from alpaca.data.requests import CryptoBarsRequest
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installé — pip install alpaca-py")

        # Auto-format : BTC -> BTC/USD
        if "/" not in symbol:
            symbol = f"{symbol}/USD"

        alpaca_tf, tf_unit, tf_val = self._build_timeframe(timeframe)
        date_kwargs = self._build_date_kwargs(start, end, bars, tf_unit, tf_val)

        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_tf,
            **date_kwargs,
        )

        crypto_client = self._get_crypto_client()
        bars_data = crypto_client.get_crypto_bars(request)
        result = self._bars_to_list(bars_data, symbol)

        logger.info(f"Alpaca crypto : {len(result)} barres {timeframe} pour {symbol}")
        return {"bars": result, "symbol": symbol, "timeframe": timeframe}

    # ─── Ordres ──────────────────────────────────────────────────────────────

    def create_position(self, symbol: str, direction: str,
                        qty: float | None = None,
                        notional: float | None = None,
                        stop_loss: float | None = None,
                        take_profit: float | None = None,
                        _authorized_by: str | None = None) -> dict:
        """
        Ouvre une position avec bracket order optionnel (stop loss + take profit).

        IMPORTANT : tout ordre DOIT passer par le pipeline d'allocation.
        Le parametre _authorized_by doit contenir l'identifiant du composant
        appelant (ex: "paper_portfolio", "execution_agent"). Tout appel
        direct sans _authorized_by est refuse.

        symbol      : ticker US
        direction   : "BUY" ou "SELL" (short)
        qty         : nombre d'actions
        notional    : montant en $ (alternatif a qty, longs uniquement)
        stop_loss   : prix du stop loss (optionnel)
        take_profit : prix du take profit (optionnel)
        _authorized_by : identifiant du pipeline appelant (obligatoire)
        """
        if _authorized_by is None:
            raise AlpacaAPIError(
                f"Ordre REFUSE pour {symbol}: create_position() appele sans "
                f"_authorized_by. Tout ordre doit passer par le pipeline "
                f"d'allocation (paper_portfolio.py ou ExecutionAgent)."
            )
        try:
            from alpaca.trading.requests import (
                MarketOrderRequest, StopLossRequest, TakeProfitRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
        except ImportError:
            raise AlpacaAPIError("alpaca-py non installe — pip install alpaca-py")

        side = OrderSide.BUY if direction.upper() == "BUY" else OrderSide.SELL

        # Bracket order si stop_loss ou take_profit fourni
        has_bracket = stop_loss is not None or take_profit is not None
        order_class = OrderClass.BRACKET if has_bracket and qty else None

        # Construire les legs du bracket
        sl_request = None
        tp_request = None
        if stop_loss is not None and stop_loss > 0:
            sl_request = StopLossRequest(stop_price=round(stop_loss, 2))
        if take_profit is not None and take_profit > 0:
            tp_request = TakeProfitRequest(limit_price=round(take_profit, 2))

        # Bracket orders necessitent qty (pas notional)
        if has_bracket and notional and not qty:
            # On ne peut pas faire de bracket avec notional — fallback sans bracket
            logger.warning(
                f"  Bracket non supporte avec notional pour {symbol}. "
                f"Ordre simple sans SL/TP."
            )
            order_class = None
            sl_request = None
            tp_request = None

        if notional and not order_class:
            request = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        elif qty:
            kwargs = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "time_in_force": TimeInForce.DAY,
            }
            if order_class:
                kwargs["order_class"] = order_class
            if sl_request:
                kwargs["stop_loss"] = sl_request
            if tp_request:
                kwargs["take_profit"] = tp_request
            request = MarketOrderRequest(**kwargs)
        else:
            raise AlpacaAPIError(f"Ordre {symbol}: ni qty ni notional fourni")

        client = self._get_trading_client()
        order = client.submit_order(request)

        bracket_info = ""
        if sl_request:
            bracket_info += f" SL=${stop_loss:.2f}"
        if tp_request:
            bracket_info += f" TP=${take_profit:.2f}"

        logger.info(
            f"Alpaca ordre soumis : {direction} {symbol} "
            f"qty={qty or ''} notional={notional or ''}{bracket_info} "
            f"— id={order.id}"
        )

        filled_price = float(order.filled_avg_price) if order.filled_avg_price else None
        filled_qty = float(order.filled_qty) if order.filled_qty else None

        return {
            "orderId":       str(order.id),
            "symbol":        order.symbol,
            "side":          order.side.value,
            "status":        order.status.value,
            "qty":           str(order.qty),
            "filled_qty":    filled_qty,
            "filled_price":  filled_price,
            "stop_loss":     stop_loss,
            "take_profit":   take_profit,
            "bracket":       order_class is not None,
            "paper":         self._paper,
            "authorized_by": _authorized_by,
        }

    def close_position(self, symbol: str, _authorized_by: str | None = None) -> dict:
        """Ferme toute la position ouverte sur un symbole."""
        if _authorized_by is None:
            raise AlpacaAPIError(
                f"Ordre REFUSE: close_position({symbol}) sans _authorized_by."
            )
        client = self._get_trading_client()
        response = client.close_position(symbol)
        logger.info(f"Alpaca : position {symbol} fermée")
        return {
            "orderId": str(response.id),
            "symbol":  response.symbol,
            "status":  response.status.value,
        }

    def close_all_positions(self, _authorized_by: str | None = None) -> list[dict]:
        """Ferme toutes les positions ouvertes (emergency stop)."""
        if _authorized_by is None:
            raise AlpacaAPIError(
                "Ordre REFUSE: close_all_positions() sans _authorized_by."
            )
        client = self._get_trading_client()
        responses = client.close_all_positions(cancel_orders=True)
        logger.warning(f"Alpaca : toutes les positions fermées ({len(responses)} ordres)")
        return [{"orderId": str(r.id)} for r in responses]
