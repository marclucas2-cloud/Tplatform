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
import math
import os
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Simple rate limiter for Alpaca API — 200 req/min, sleep if >180 in last 60s."""

    def __init__(self, max_requests: int = 200, window_seconds: float = 60.0,
                 soft_limit: int = 180):
        self._max = max_requests
        self._window = window_seconds
        self._soft = soft_limit
        self._timestamps: deque[float] = deque()

    def acquire(self):
        """Wait if approaching rate limit before making a request."""
        now = time.monotonic()
        # Purge timestamps older than the window
        while self._timestamps and self._timestamps[0] < now - self._window:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._soft:
            # Sleep until the oldest request in window expires
            sleep_time = self._timestamps[0] + self._window - now + 0.1
            if sleep_time > 0:
                logger.warning(
                    f"Alpaca rate limiter: {len(self._timestamps)} req in last "
                    f"{self._window}s (soft limit={self._soft}), sleeping {sleep_time:.1f}s"
                )
                time.sleep(sleep_time)

        self._timestamps.append(time.monotonic())

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
        self._rate_limiter = _RateLimiter()  # FIX CRO H-1: 200 req/min limiter

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
        self._rate_limiter.acquire()  # FIX CRO H-1: rate limit avant chaque appel
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
        self._rate_limiter.acquire()  # FIX CRO H-1: rate limit avant chaque appel
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
        notional_orig = notional  # sauvegarde pour logs conversion

        # FIX CRO M-1 : guard fractional shorts — les shorts doivent avoir qty entiere
        if side == OrderSide.SELL and qty is not None and qty != int(qty):
            original_qty = qty
            qty = math.floor(qty)
            logger.warning(
                f"Fractional short converti: {symbol} qty {original_qty} -> {qty} "
                f"(shorts en qty entiere uniquement)"
            )
            if qty < 1:
                logger.warning(
                    f"Ordre REFUSE pour {symbol}: qty={original_qty} arrondi a {qty} "
                    f"(< 1 action) — impossible de shorter"
                )
                return {"status": "refused", "reason": "fractional short qty < 1 after rounding"}

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
        # FIX CRO H-1 : convertir notional → qty au lieu de supprimer le bracket
        if has_bracket and notional and not qty:
            try:
                data_client = self._get_data_client()
                from alpaca.data.requests import StockLatestQuoteRequest
                quote = data_client.get_stock_latest_quote(
                    StockLatestQuoteRequest(symbol_or_symbols=symbol)
                )
                price = float(quote[symbol].ask_price or quote[symbol].bid_price or 0)
                if price > 0:
                    qty = int(notional / price)
                    notional = None  # switch to qty mode
                    order_class = OrderClass.BRACKET if qty > 0 else None
                    if qty < 1:
                        logger.warning(
                            f"  Notional ${notional_orig:.2f} trop petit pour {symbol} "
                            f"@ ${price:.2f} — ordre annule."
                        )
                        return {"status": "cancelled", "reason": "qty < 1 after notional conversion"}
                    logger.info(
                        f"  Notional→qty conversion: {symbol} ${notional_orig:.2f} → "
                        f"{qty} shares @ ${price:.2f} (bracket preserved)"
                    )
                else:
                    # If we can't set up bracket protection, REFUSE the order entirely
                    # A position without SL is unacceptable
                    logger.critical(
                        f"REFUSING order for {symbol}: cannot create bracket "
                        f"(price fetch returned 0) — position without SL is unacceptable"
                    )
                    return None
            except Exception as e:
                # If we can't set up bracket protection, REFUSE the order entirely
                # A position without SL is unacceptable
                logger.critical(
                    f"REFUSING order for {symbol}: cannot create bracket "
                    f"(price fetch failed: {e}) — position without SL is unacceptable"
                )
                return None

        if notional and not order_class:
            # CRO C-1: REFUSE position sans stop-loss = risque non borne
            logger.critical(
                f"CRO REJECT: ordre {side} {symbol} notional=${notional:.2f} "
                f"SANS stop-loss — risque non borne! Ordre REFUSE."
            )
            return {
                "orderId": None, "symbol": symbol, "status": "REJECTED",
                "reason": "no_stop_loss", "paper": self._paper,
                "authorized_by": _authorized_by,
            }
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

        # FIX CRO H-2 : error handling + alerting on submit_order
        # NO RETRY to prevent double positions — just log and alert
        try:
            order = client.submit_order(request)
        except Exception as submit_err:
            err_msg = str(submit_err).lower()
            err_type = type(submit_err).__name__

            # Classify error type
            if "429" in err_msg or "rate limit" in err_msg:
                logger.critical(
                    f"ALPACA RATE LIMIT (429) pour {symbol}: {submit_err}"
                )
            elif "timeout" in err_msg or "timed out" in err_msg:
                logger.critical(
                    f"ALPACA TIMEOUT pour {symbol}: {submit_err}"
                )
            elif "buying power" in err_msg or "insufficient" in err_msg:
                logger.critical(
                    f"ALPACA INSUFFICIENT BUYING POWER pour {symbol}: {submit_err}"
                )
            else:
                logger.critical(
                    f"ALPACA ORDER FAILED pour {symbol} ({err_type}): {submit_err}"
                )

            # Try to send Telegram alert (best-effort)
            try:
                from core.telegram_alert import send_alert
                send_alert(
                    f"ALPACA ORDER FAILED: {direction} {symbol} "
                    f"qty={qty or ''} notional={notional or ''} — "
                    f"{err_type}: {submit_err}",
                    level="critical",
                )
            except Exception:
                pass

            # Do NOT retry — re-raise so caller knows the order failed
            raise

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
