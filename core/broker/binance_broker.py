"""
BinanceBroker V2 — Broker adapter for Binance France (Spot + Margin + Earn).

NO Futures Perp (blocked by French regulation).
Supports:
  - Spot trading (buy/sell)
  - Margin Isolated (borrow/repay/short via margin)
  - Margin Cross (limited use)
  - Binance Earn (flexible lending for carry)
  - Borrow rate queries
  - Rate limiting (1200 req/min, weight-based)

Environment variables:
  BINANCE_API_KEY     : API key
  BINANCE_API_SECRET  : API secret
  BINANCE_TESTNET     : "true" for testnet (default: "true")
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from core.broker.base import BaseBroker, BrokerError

logger = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
SPOT_TESTNET = "https://testnet.binance.vision"
# Futures API used READ-ONLY for OI/funding data signals (not trading)
FUTURES_BASE = "https://fapi.binance.com"


class RateLimiter:
    """Weight-based rate limiter for Binance API."""

    def __init__(self, weight_limit: int = 1200, window: int = 60):
        self.weight_limit = weight_limit
        self.window = window
        self._entries: list[tuple[float, int]] = []  # (timestamp, weight)

    def acquire(self, weight: int = 1):
        now = time.time()
        self._entries = [(t, w) for t, w in self._entries if now - t < self.window]
        total = sum(w for _, w in self._entries)
        if total + weight > self.weight_limit * 0.8:
            sleep_time = self._entries[0][0] + self.window - now + 0.1
            if sleep_time > 0:
                logger.warning(f"Rate limit: sleeping {sleep_time:.1f}s (weight {total}/{self.weight_limit})")
                time.sleep(sleep_time)
        self._entries.append((time.time(), weight))


class BinanceBroker(BaseBroker):
    """Broker adapter for Binance France (spot + margin + earn).

    NO futures perp trading — only read-only access for signal data.
    """

    MODES = {
        "SPOT": "spot",
        "MARGIN_ISOLATED": "margin",
        "MARGIN_CROSS": "margin",
        "EARN_FLEXIBLE": "earn",
        "EARN_LOCKED": "earn",
    }

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool | None = None,
    ):
        self._api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self._api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        if testnet is None:
            testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        self._testnet = testnet
        # CRO SECURITY: guard explicite pour le mode LIVE
        if not testnet:
            live_confirmed = os.getenv("BINANCE_LIVE_CONFIRMED", "").lower() == "true"
            if not live_confirmed:
                logger.critical(
                    "BINANCE LIVE MODE — set BINANCE_LIVE_CONFIRMED=true to confirm"
                )
            logger.warning("BinanceBroker initialise en mode LIVE (pas testnet)")
        self._spot_base = SPOT_TESTNET if testnet else SPOT_BASE
        self._rate_limiter = RateLimiter(weight_limit=1200, window=60)
        self._session = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": self._api_key})
        # Track avg_price from fills (symbol -> avg_price)
        self._fill_prices: dict[str, float] = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _request(self, method: str, base: str, path: str, params: dict | None = None, signed: bool = False, weight: int = 1) -> Any:
        self._rate_limiter.acquire(weight)
        params = params or {}
        if signed:
            params = self._sign(params)
        url = f"{base}{path}"
        try:
            resp = self._session.request(method, url, params=params, timeout=10)
        except requests.RequestException as e:
            raise BrokerError(f"Binance request failed: {e}")
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", 60))
            logger.warning(f"Rate limit 429 — waiting {retry}s")
            time.sleep(retry)
            # Remove stale signature/timestamp before retry so _sign() regenerates them
            params.pop("timestamp", None)
            params.pop("signature", None)
            return self._request(method, base, path, params, signed=signed, weight=weight)
        if resp.status_code == 200 and resp.text.strip() in ('', '[]', '{}'):
            logger.warning(f"Binance returned empty response for {path}")
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise BrokerError(f"Binance API {resp.status_code}: {err}")
        return resp.json()

    def _get(self, path, params=None, signed=False, weight=1):
        return self._request("GET", self._spot_base, path, params, signed, weight)

    def _post(self, path, params=None, weight=1):
        return self._request("POST", self._spot_base, path, params, signed=True, weight=weight)

    def _delete(self, path, params=None, weight=1):
        return self._request("DELETE", self._spot_base, path, params, signed=True, weight=weight)

    def _futures_get(self, path, params=None, weight=1):
        """READ-ONLY futures API for signal data (OI, funding)."""
        return self._request("GET", FUTURES_BASE, path, params, signed=False, weight=weight)

    # ------------------------------------------------------------------
    # BaseBroker interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "binance"

    @property
    def is_paper(self) -> bool:
        return self._testnet

    def authenticate(self) -> dict:
        self._get("/api/v3/ping")
        account = self._get("/api/v3/account", signed=True, weight=10)
        balances = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in account.get("balances", []) if float(b["free"]) + float(b["locked"]) > 0}
        usdt = balances.get("USDT", 0)

        margin_equity = 0
        try:
            margin = self._get("/sapi/v1/margin/account", signed=True, weight=10)
            margin_equity = float(margin.get("totalNetAssetOfBtc", 0))
        except BrokerError:
            pass

        logger.info(f"Binance authenticated ({'TESTNET' if self._testnet else 'LIVE'}) — spot USDT={usdt:.2f}")
        return {"status": "ok", "equity": usdt, "cash": usdt, "buying_power": usdt, "currency": "USDT", "paper": self._testnet, "account_number": "binance", "spot_usdt": usdt, "margin_equity_btc": margin_equity}

    def get_account_info(self) -> dict:
        account = self._get("/api/v3/account", signed=True, weight=10)
        spot_usdt = 0.0
        spot_total_usd = 0.0
        for b in account.get("balances", []):
            free = float(b["free"])
            locked = float(b["locked"])
            total = free + locked
            if b["asset"] == "USDT":
                spot_usdt = total
                spot_total_usd += total
            elif total > 0:
                try:
                    ticker = self._get("/api/v3/ticker/price", {"symbol": b["asset"] + "USDT"})
                    spot_total_usd += total * float(ticker["price"])
                except BrokerError:
                    pass

        # Margin account
        margin_total = 0
        margin_borrowed = 0
        margin_interest = 0
        margin_level = 999
        try:
            margin = self._get("/sapi/v1/margin/account", signed=True, weight=10)
            margin_total = float(margin.get("totalAssetOfBtc", 0))
            margin_borrowed = float(margin.get("totalLiabilityOfBtc", 0))
            ml = margin.get("marginLevel")
            if ml:
                margin_level = float(ml)
        except BrokerError:
            pass

        equity = spot_total_usd
        return {
            "equity": round(equity, 2),
            "cash": round(spot_usdt, 2),
            "buying_power": round(spot_usdt, 2),
            "spot_usdt": round(spot_usdt, 2),
            "spot_total_usd": round(spot_total_usd, 2),
            "margin_level": round(margin_level, 2),
            "margin_borrowed_btc": margin_borrowed,
            "margin_interest_btc": margin_interest,
        }

    def get_positions(self) -> list[dict]:
        positions = []
        account = self._get("/api/v3/account", signed=True, weight=10)
        for b in account.get("balances", []):
            total = float(b["free"]) + float(b["locked"])
            if total <= 0 or b["asset"] in ("USDT", "BUSD", "USDC"):
                continue
            symbol = b["asset"] + "USDT"
            try:
                ticker = self._get("/api/v3/ticker/price", {"symbol": symbol})
                price = float(ticker["price"])
            except BrokerError:
                continue
            avg_entry = self._fill_prices.get(symbol, 0)
            unrealized_pl = round(total * (price - avg_entry), 2) if avg_entry > 0 else 0
            unrealized_plpc = round((price - avg_entry) / avg_entry * 100, 2) if avg_entry > 0 else 0
            positions.append({"symbol": symbol, "qty": total, "side": "LONG", "avg_entry": avg_entry, "market_val": round(total * price, 2), "unrealized_pl": unrealized_pl, "unrealized_plpc": unrealized_plpc, "current_price": price, "asset_type": "CRYPTO_SPOT"})

        # Margin positions
        try:
            isolated = self._get("/sapi/v1/margin/isolated/account", signed=True, weight=10)
            for asset_info in isolated.get("assets", []):
                symbol = asset_info.get("symbol", "")
                base = asset_info.get("baseAsset", {})
                quote = asset_info.get("quoteAsset", {})
                borrowed = float(base.get("borrowed", 0))
                net = float(base.get("netAsset", 0))
                if borrowed > 0:
                    try:
                        ticker = self._get("/api/v3/ticker/price", {"symbol": symbol})
                        price = float(ticker["price"])
                    except BrokerError:
                        continue
                    avg_entry = self._fill_prices.get(symbol, 0)
                    unrealized_pl = round(borrowed * (avg_entry - price), 2) if avg_entry > 0 else 0
                    positions.append({"symbol": symbol, "qty": -borrowed, "side": "SHORT", "avg_entry": avg_entry, "market_val": round(borrowed * price, 2), "unrealized_pl": unrealized_pl, "borrowed": borrowed, "interest": float(base.get("interest", 0)), "margin_level": float(asset_info.get("marginLevel", 0)), "current_price": price, "asset_type": "CRYPTO_MARGIN"})
                elif net > 0.0001:
                    try:
                        ticker = self._get("/api/v3/ticker/price", {"symbol": symbol})
                        price = float(ticker["price"])
                    except BrokerError:
                        continue
                    avg_entry = self._fill_prices.get(symbol, 0)
                    unrealized_pl = round(net * (price - avg_entry), 2) if avg_entry > 0 else 0
                    positions.append({"symbol": symbol, "qty": net, "side": "LONG", "avg_entry": avg_entry, "market_val": round(net * price, 2), "unrealized_pl": unrealized_pl, "margin_level": float(asset_info.get("marginLevel", 0)), "current_price": price, "asset_type": "CRYPTO_MARGIN"})
        except BrokerError:
            pass
        return positions

    def get_orders(self, status: str = "open", limit: int = 50) -> list[dict]:
        orders = []
        if status in ("open", "all"):
            raw = self._get("/api/v3/openOrders", signed=True, weight=40)
            for o in raw[:limit]:
                orders.append({"order_id": str(o.get("orderId", "")), "symbol": o.get("symbol", ""), "side": o.get("side", ""), "type": o.get("type", ""), "status": o.get("status", ""), "qty": float(o.get("origQty", 0)), "filled_qty": float(o.get("executedQty", 0)), "filled_price": float(o.get("price", 0)), "created_at": o.get("time", ""), "source": "spot"})
        return orders[:limit]

    def create_position(self, symbol: str, direction: str, qty: float | None = None, notional: float | None = None, stop_loss: float | None = None, take_profit: float | None = None, _authorized_by: str | None = None, market_type: str = "spot", **kwargs) -> dict:
        if not _authorized_by:
            raise BrokerError("_authorized_by is required for all orders")
        if market_type == "margin":
            return self._create_margin_position(symbol, direction, qty, stop_loss, take_profit, _authorized_by, **kwargs)
        return self._create_spot_position(symbol, direction, qty, notional, stop_loss, _authorized_by)

    def _create_spot_position(self, symbol, direction, qty, notional, stop_loss, authorized_by):
        side = direction.upper()
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": "MARKET"}
        if qty:
            params["quantity"] = str(qty)
        elif notional and side == "BUY":
            params["quoteOrderQty"] = str(notional)
        else:
            raise BrokerError("qty or notional required for spot orders")
        result = self._post("/api/v3/order", params)
        fills = result.get("fills", [])
        filled_qty = float(result.get("executedQty", 0))
        avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills) if fills else 0
        # Track avg_price from fills for position reporting
        if avg_price > 0 and side == "BUY":
            self._fill_prices[symbol] = avg_price
        elif side == "SELL" and symbol in self._fill_prices:
            del self._fill_prices[symbol]

        logger.info(f"Binance spot {side} {qty or notional} {symbol} @ {avg_price:.2f} [{authorized_by}]")

        # Attach stop-loss for spot positions (broker-side)
        sl_order_id = None
        if stop_loss and filled_qty > 0 and side == "BUY":
            try:
                sl_params = {
                    "symbol": symbol,
                    "side": "SELL",
                    "type": "STOP_LOSS_LIMIT",
                    "quantity": str(filled_qty),
                    "price": str(round(stop_loss * 0.995, 2)),
                    "stopPrice": str(round(stop_loss, 2)),
                    "timeInForce": "GTC",
                }
                sl_result = self._post("/api/v3/order", sl_params)
                sl_order_id = str(sl_result.get("orderId", ""))
                logger.info(f"Spot SL attached: {symbol} @ {stop_loss:.2f} [{authorized_by}]")
            except BrokerError as e:
                logger.warning(f"Spot SL order failed for {symbol}: {e}")

        return {"orderId": str(result.get("orderId", "")), "symbol": symbol, "side": side, "status": result.get("status", "FILLED"), "qty": filled_qty, "filled_qty": filled_qty, "filled_price": avg_price, "stop_loss": stop_loss, "sl_order_id": sl_order_id, "paper": self._testnet, "authorized_by": authorized_by, "market_type": "spot"}

    def _create_margin_position(self, symbol, direction, qty, stop_loss, take_profit, authorized_by, **kwargs):
        side = direction.upper()
        if side not in ("BUY", "SELL"):
            raise BrokerError(f"Invalid direction: {direction}")
        # For margin shorts: borrow + sell
        if side == "SELL":
            base_asset = symbol.replace("USDT", "")
            self.margin_borrow(base_asset, qty, symbol=symbol)
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": str(qty), "isIsolated": "TRUE", "sideEffectType": "MARGIN_BUY" if side == "BUY" else "NO_SIDE_EFFECT"}
        result = self._post("/sapi/v1/margin/order", params)
        filled_price = float(result.get("price", 0)) or float(result.get("cummulativeQuoteQty", 0)) / max(float(result.get("executedQty", 1)), 1e-8)
        # Track avg_price from fills for position reporting
        if filled_price > 0:
            self._fill_prices[symbol] = filled_price
        logger.info(f"Binance margin {side} {qty} {symbol} @ {filled_price:.2f} [{authorized_by}]")
        # Stop loss
        sl_id = None
        if stop_loss and side == "SELL":
            sl_params = {"symbol": symbol, "side": "BUY", "type": "STOP_LOSS_LIMIT", "quantity": str(qty), "price": str(round(stop_loss * 1.005, 2)), "stopPrice": str(round(stop_loss, 2)), "timeInForce": "GTC", "isIsolated": "TRUE"}
            try:
                sl_result = self._post("/sapi/v1/margin/order", sl_params)
                sl_id = str(sl_result.get("orderId", ""))
            except BrokerError as e:
                # CRO H-3: position margin SANS SL = risque non borne
                # Fermer immediatement la position
                logger.critical(f"SL order failed for margin {side} {symbol}: {e} — CLOSING POSITION")
                try:
                    close_params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": str(qty), "isIsolated": "TRUE", "sideEffectType": "AUTO_REPAY"}
                    self._post("/sapi/v1/margin/order", close_params)
                    logger.critical(f"Emergency close: margin SHORT {symbol} closed (no SL)")
                except Exception as e2:
                    logger.critical(f"EMERGENCY CLOSE FAILED for {symbol}: {e2}")
        elif stop_loss and side == "BUY":
            sl_params = {"symbol": symbol, "side": "SELL", "type": "STOP_LOSS_LIMIT", "quantity": str(qty), "price": str(round(stop_loss * 0.995, 2)), "stopPrice": str(round(stop_loss, 2)), "timeInForce": "GTC", "isIsolated": "TRUE"}
            try:
                sl_result = self._post("/sapi/v1/margin/order", sl_params)
                sl_id = str(sl_result.get("orderId", ""))
            except BrokerError as e:
                logger.critical(f"SL order failed for margin {side} {symbol}: {e} — CLOSING POSITION")
                try:
                    close_params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": str(qty), "isIsolated": "TRUE"}
                    self._post("/sapi/v1/margin/order", close_params)
                    logger.critical(f"Emergency close: margin LONG {symbol} closed (no SL)")
                except Exception as e2:
                    logger.critical(f"EMERGENCY CLOSE FAILED for {symbol}: {e2}")
        return {"orderId": str(result.get("orderId", "")), "symbol": symbol, "side": side, "status": result.get("status", "FILLED"), "qty": float(result.get("executedQty", 0)), "filled_qty": float(result.get("executedQty", 0)), "filled_price": filled_price, "stop_loss": stop_loss, "sl_order_id": sl_id, "paper": self._testnet, "authorized_by": authorized_by, "market_type": "margin"}

    def close_position(self, symbol: str, _authorized_by: str | None = None) -> dict:
        if not _authorized_by:
            raise BrokerError("_authorized_by is required")
        positions = self.get_positions()
        for p in positions:
            if p["symbol"] == symbol:
                qty = abs(float(p["qty"]))
                # Skip dust positions (< $1 notional)
                price = float(p.get("market_price", p.get("current_price", 0)))
                if qty * price < 1.0 and qty < 0.0001:
                    logger.info(f"Skip close {symbol}: dust position qty={qty}")
                    return {"orderId": None, "symbol": symbol, "status": "DUST_SKIP"}
                if p["side"] == "SHORT":
                    result = self._create_margin_position(symbol, "BUY", qty, None, None, _authorized_by)
                    base_asset = symbol.replace("USDT", "")
                    try:
                        self.margin_repay(base_asset, qty, symbol=symbol)
                    except BrokerError as e:
                        logger.warning(f"Repay failed: {e}")
                    return result
                else:
                    return self._create_spot_position(symbol, "SELL", qty, None, None, _authorized_by)
        return {"orderId": None, "symbol": symbol, "status": "NO_POSITION"}

    def close_all_positions(self, _authorized_by: str | None = None) -> list[dict]:
        if not _authorized_by:
            raise BrokerError("_authorized_by is required")
        results = []
        for p in self.get_positions():
            try:
                r = self.close_position(p["symbol"], _authorized_by=_authorized_by)
                results.append(r)
            except BrokerError as e:
                results.append({"symbol": p["symbol"], "error": str(e)})
        self.cancel_all_orders(_authorized_by=_authorized_by)
        return results

    def cancel_all_orders(self, _authorized_by: str | None = None) -> int:
        if not _authorized_by:
            raise BrokerError("_authorized_by is required for cancel_all_orders")
        count = 0
        orders = self._get("/api/v3/openOrders", signed=True, weight=40)
        for o in orders:
            try:
                self._delete("/api/v3/order", {"symbol": o["symbol"], "orderId": o["orderId"]})
                count += 1
            except BrokerError:
                pass
        return count

    def get_prices(self, symbol: str, timeframe: str = "1D", bars: int = 500, start: str = "", end: str = "") -> dict:
        interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1D": "1d", "1d": "1d"}
        interval = interval_map.get(timeframe, "1h")
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": min(bars, 1000)}
        if start:
            params["startTime"] = int(datetime.fromisoformat(start).timestamp() * 1000)
        if end:
            params["endTime"] = int(datetime.fromisoformat(end).timestamp() * 1000)
        raw = self._get("/api/v3/klines", params, weight=2)
        candles = [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in raw]
        return {"bars": candles, "symbol": symbol, "timeframe": timeframe}

    # ------------------------------------------------------------------
    # Margin-specific
    # ------------------------------------------------------------------

    def margin_borrow(self, asset: str, amount: float, symbol: str | None = None) -> dict:
        """Borrow an asset for margin trading (isolated)."""
        params: dict[str, Any] = {"asset": asset, "amount": str(amount), "isIsolated": "TRUE"}
        if symbol:
            params["symbol"] = symbol
        result = self._post("/sapi/v1/margin/loan", params)
        logger.info(f"Margin borrow: {amount} {asset} (pair {symbol})")
        return result

    def margin_repay(self, asset: str, amount: float, symbol: str | None = None) -> dict:
        """Repay a margin loan (principal + interest)."""
        params: dict[str, Any] = {"asset": asset, "amount": str(amount), "isIsolated": "TRUE"}
        if symbol:
            params["symbol"] = symbol
        result = self._post("/sapi/v1/margin/repay", params)
        logger.info(f"Margin repay: {amount} {asset}")
        return result

    def get_borrow_rate(self, asset: str) -> dict:
        """Get current borrow interest rate for an asset."""
        try:
            data = self._get("/sapi/v1/margin/interestRateHistory", {"asset": asset, "size": 1}, signed=True)
            if data:
                return {"asset": asset, "daily_rate": float(data[-1].get("dailyInterestRate", 0)), "hourly_rate": float(data[-1].get("dailyInterestRate", 0)) / 24, "annual_rate": float(data[-1].get("dailyInterestRate", 0)) * 365}
        except BrokerError:
            pass
        return {"asset": asset, "daily_rate": 0.0003, "hourly_rate": 0.0000125, "annual_rate": 0.1095}

    def get_margin_account(self, symbol: str | None = None) -> dict:
        """Get margin account info (isolated or cross)."""
        if symbol:
            data = self._get("/sapi/v1/margin/isolated/account", signed=True, weight=10)
            for a in data.get("assets", []):
                if a.get("symbol") == symbol:
                    return {"symbol": symbol, "margin_level": float(a.get("marginLevel", 0)), "base_borrowed": float(a.get("baseAsset", {}).get("borrowed", 0)), "base_interest": float(a.get("baseAsset", {}).get("interest", 0)), "base_net": float(a.get("baseAsset", {}).get("netAsset", 0)), "quote_free": float(a.get("quoteAsset", {}).get("free", 0))}
        return {}

    # ------------------------------------------------------------------
    # Earn
    # ------------------------------------------------------------------

    def subscribe_earn(self, product_id: str, amount: float) -> dict:
        """Subscribe to Binance Simple Earn (flexible)."""
        params = {"productId": product_id, "amount": str(amount)}
        return self._post("/sapi/v1/simple-earn/flexible/subscribe", params)

    def redeem_earn(self, product_id: str, amount: float | None = None) -> dict:
        """Redeem from Binance Simple Earn."""
        params: dict[str, Any] = {"productId": product_id}
        if amount:
            params["amount"] = str(amount)
        else:
            params["redeemAll"] = "true"
        return self._post("/sapi/v1/simple-earn/flexible/redeem", params)

    def get_earn_positions(self) -> list[dict]:
        """Get all Earn positions."""
        try:
            data = self._get("/sapi/v1/simple-earn/flexible/position", signed=True, weight=5)
            return [{"asset": p.get("asset", ""), "amount": float(p.get("totalAmount", 0)), "apy": float(p.get("latestAnnualPercentageRate", 0)), "rewards": float(p.get("totalRewards", 0)), "product_id": p.get("productId", "")} for p in data.get("rows", [])]
        except BrokerError:
            return []

    def get_earn_rates(self) -> list[dict]:
        """Get current Earn APY rates."""
        try:
            data = self._get("/sapi/v1/simple-earn/flexible/list", {"size": 100}, weight=5)
            return [{"asset": p.get("asset", ""), "apy": float(p.get("latestAnnualPercentageRate", 0)), "product_id": p.get("productId", "")} for p in data.get("rows", [])]
        except BrokerError:
            return []

    # ------------------------------------------------------------------
    # Read-only futures data (for signals, not trading)
    # ------------------------------------------------------------------

    def get_funding_rate_readonly(self, symbol: str) -> dict:
        """READ-ONLY: Get funding rate data for signals."""
        data = self._futures_get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return {"symbol": symbol, "funding_rate": float(data.get("lastFundingRate", 0)), "mark_price": float(data.get("markPrice", 0)), "next_funding_time": data.get("nextFundingTime", 0)}

    def get_open_interest_readonly(self, symbol: str) -> dict:
        """READ-ONLY: Get open interest for signals."""
        data = self._futures_get("/fapi/v1/openInterest", {"symbol": symbol})
        return {"symbol": symbol, "open_interest": float(data.get("openInterest", 0))}

    def get_ticker_24h(self, symbol: str) -> dict:
        data = self._get("/api/v3/ticker/24hr", {"symbol": symbol}, weight=1)
        return {"symbol": symbol, "price_change_pct": float(data.get("priceChangePercent", 0)), "volume": float(data.get("volume", 0)), "quote_volume": float(data.get("quoteVolume", 0)), "high": float(data.get("highPrice", 0)), "low": float(data.get("lowPrice", 0)), "last_price": float(data.get("lastPrice", 0))}

    def get_order_book(self, symbol: str, limit: int = 5) -> dict:
        data = self._get("/api/v3/depth", {"symbol": symbol, "limit": limit}, weight=limit // 100 + 1)
        bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
        asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
        spread_bps = 0
        if bids and asks:
            mid = (bids[0][0] + asks[0][0]) / 2
            spread_bps = round((asks[0][0] - bids[0][0]) / mid * 10000, 2) if mid > 0 else 999
        return {"symbol": symbol, "bids": bids, "asks": asks, "spread_bps": spread_bps}

    def __repr__(self):
        return f"BinanceBroker({'TESTNET' if self._testnet else 'LIVE'}, margin+spot+earn)"
