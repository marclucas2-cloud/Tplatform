"""
LIVE-001 — Crypto order manager for Binance live execution.

Handles:
  - Order submission with retry and error handling
  - Reduce-only enforcement for closing positions
  - Bracket order management (SL/TP)
  - Margin short hedging (borrow + sell pattern, NO futures/perp)
  - Pre-trade risk checks
"""
from __future__ import annotations

import logging
import time

from core.broker.base import BrokerError

logger = logging.getLogger(__name__)

# Binance error codes
BINANCE_ERRORS = {
    -1021: "Timestamp out of sync — resync NTP",
    -1015: "Rate limit — wait and retry",
    -2019: "Margin insufficient — reduce size",
    -4131: "Price out of limits — adjust price",
    -4164: "Order would immediately trigger — adjust stop price",
}


class CryptoOrderManager:
    """Manage order execution on Binance with safety checks."""

    def __init__(self, broker, risk_manager=None, alerter=None, order_tracker=None):
        self._broker = broker
        self._risk_manager = risk_manager
        self._alerter = alerter
        self._pending_orders: list[dict] = []
        self._filled_orders: list[dict] = []
        self._order_tracker = order_tracker

    def submit_order(
        self,
        symbol: str,
        direction: str,
        qty: float,
        strategy: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        leverage: int = 1,
        market_type: str = "spot",
        reduce_only: bool = False,
        max_retries: int = 3,
        _authorized_by: str = "",
    ) -> dict:
        """Submit an order with pre-trade checks and retry logic.

        Returns:
            Order result dict or error dict
        """
        if not _authorized_by:
            raise BrokerError("_authorized_by required")

        # CRO H-2: SL mandatory for new orders (not reduce-only closes)
        if not reduce_only and stop_loss is None:
            logger.warning(f"Order rejected: stop_loss MANDATORY for {symbol}")
            return {"error": "stop_loss_mandatory", "symbol": symbol}

        # Track order via OrderStateMachine if tracker available
        _osm = None
        if self._order_tracker:
            _osm = self._order_tracker.create_order(
                symbol=symbol, side=direction, quantity=qty,
                broker="binance", strategy=strategy,
            )

        # Reject futures/perp — not available on Binance France
        if market_type == "futures":
            logger.warning("Order rejected: futures/perp not available on Binance France")
            if _osm:
                self._order_tracker.reject(_osm.order_id)
            return {"error": "futures_perp_not_available_binance_france", "symbol": symbol}

        # Pre-trade risk check
        if self._risk_manager and not reduce_only:
            # Get price for notional calculation
            try:
                ticker = self._broker.get_ticker_24h(symbol) if hasattr(self._broker, "get_ticker_24h") else {}
                mark_price = ticker.get("last_price", 0) if ticker else 0
            except Exception:
                mark_price = 0
            notional = qty * mark_price if mark_price > 0 else qty * 1000

            ok, msg = self._risk_manager.check_position_size(notional)
            if not ok:
                logger.warning(f"Order rejected by risk: {msg}")
                if _osm:
                    self._order_tracker.reject(_osm.order_id)
                return {"error": f"risk_check_failed: {msg}", "symbol": symbol}

            # HIGH-4: Validate leverage against portfolio limits
            if leverage > 1 and hasattr(self._risk_manager, "check_leverage"):
                pos_for_check = [{"symbol": symbol, "leverage": leverage, "notional": notional}]
                lev_ok, lev_msg = self._risk_manager.check_leverage(pos_for_check)
                if not lev_ok:
                    logger.warning(f"Order rejected by leverage check: {lev_msg}")
                    if _osm:
                        self._order_tracker.reject(_osm.order_id)
                    return {"error": f"leverage_check_failed: {lev_msg}", "symbol": symbol}

            if self._risk_manager.kill_switch.is_killed:
                logger.warning("Order rejected: kill switch active")
                if _osm:
                    self._order_tracker.reject(_osm.order_id)
                return {"error": "kill_switch_active", "symbol": symbol}

        # Validate via OrderTracker
        if _osm:
            self._order_tracker.validate(_osm.order_id, risk_approved=True)

        # Retry loop
        last_error = None
        for attempt in range(max_retries):
            try:
                result = self._broker.create_position(
                    symbol=symbol,
                    direction=direction,
                    qty=qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    _authorized_by=_authorized_by,
                    leverage=leverage,
                    market_type=market_type,
                    reduce_only=reduce_only,
                )

                self._filled_orders.append(result)

                # Track SUBMITTED → FILLED in OrderTracker
                if _osm:
                    broker_id = str(result.get("orderId", result.get("order_id", "")))
                    self._order_tracker.submit(_osm.order_id, broker_id or "unknown")
                    has_sl = stop_loss is not None and stop_loss > 0
                    self._order_tracker.fill(
                        _osm.order_id,
                        has_sl=has_sl or reduce_only,
                        sl_order_id=result.get("sl_order_id"),
                    )

                # Alert on trade
                if self._alerter:
                    self._alerter.trade_executed(
                        symbol, direction, qty,
                        result.get("filled_price", 0), strategy,
                    )

                return result

            except BrokerError as e:
                last_error = e
                error_str = str(e)

                # Check for specific error codes
                for code, hint in BINANCE_ERRORS.items():
                    if str(code) in error_str:
                        logger.warning(f"Binance error {code}: {hint}")
                        if code == -1021:
                            # Timestamp sync issue — retry immediately
                            continue
                        elif code == -1015:
                            # Rate limit — wait longer
                            time.sleep(5 * (attempt + 1))
                            continue
                        elif code == -2019:
                            # Margin insufficient — reduce qty
                            qty = qty * 0.8
                            logger.info(f"Reducing qty to {qty:.4f}")
                            continue
                        elif code in (-4131, -4164):
                            # Price issues — skip
                            return {"error": error_str, "symbol": symbol}

                # Generic retry with backoff
                backoff = min(2 ** attempt, 10)
                logger.warning(f"Order attempt {attempt + 1} failed: {e}, retrying in {backoff}s")
                time.sleep(backoff)

        logger.error(f"Order failed after {max_retries} retries: {last_error}")
        if _osm:
            self._order_tracker.error(_osm.order_id)
        return {"error": str(last_error), "symbol": symbol}

    def submit_margin_hedge(
        self,
        symbol: str,
        qty: float,
        strategy: str = "margin_hedge",
        stop_loss: float | None = None,
        _authorized_by: str = "",
    ) -> dict:
        """Execute a margin hedge: SHORT via margin borrow + LONG spot (same qty).

        Binance France pattern: borrow asset -> sell on margin -> buy spot.
        NO futures/perp (blocked by French regulation).

        Returns:
            dict with both leg results
        """
        # Leg 1: SHORT via margin (borrow + sell)
        margin_result = self.submit_order(
            symbol=symbol,
            direction="SELL",
            qty=qty,
            strategy=strategy,
            leverage=1,
            market_type="margin",
            stop_loss=stop_loss,
            _authorized_by=_authorized_by,
        )

        if "error" in margin_result:
            return {"error": f"margin short leg failed: {margin_result['error']}"}

        # Leg 2: LONG the spot
        spot_result = self.submit_order(
            symbol=symbol,
            direction="BUY",
            qty=qty,
            strategy=strategy,
            market_type="spot",
            _authorized_by=_authorized_by,
        )

        if "error" in spot_result:
            # Margin short succeeded but spot failed — close margin to avoid naked short
            logger.warning(f"Spot leg failed, closing margin short: {spot_result['error']}")
            self.submit_order(
                symbol=symbol,
                direction="BUY",
                qty=qty,
                strategy=strategy,
                market_type="margin",
                reduce_only=True,
                _authorized_by=_authorized_by,
            )
            return {"error": f"spot leg failed, margin reversed: {spot_result['error']}"}

        return {
            "status": "OK",
            "margin_short": margin_result,
            "spot_long": spot_result,
            "symbol": symbol,
            "qty": qty,
            "strategy": strategy,
        }

    def close_margin_hedge(
        self,
        symbol: str,
        qty: float,
        _authorized_by: str = "",
    ) -> dict:
        """Close a margin hedge: BUY to cover margin short + SELL spot."""
        margin_result = self.submit_order(
            symbol=symbol,
            direction="BUY",
            qty=qty,
            strategy="margin_hedge",
            market_type="margin",
            reduce_only=True,
            _authorized_by=_authorized_by,
        )

        spot_result = self.submit_order(
            symbol=symbol,
            direction="SELL",
            qty=qty,
            strategy="margin_hedge",
            market_type="spot",
            _authorized_by=_authorized_by,
        )

        return {
            "margin_close": margin_result,
            "spot_close": spot_result,
        }

    def emergency_close_all(self, _authorized_by: str = "") -> dict:
        """Emergency: close all crypto positions."""
        if not _authorized_by:
            raise BrokerError("_authorized_by required for emergency close")

        logger.critical(f"EMERGENCY CLOSE ALL [{_authorized_by}]")

        results = self._broker.close_all_positions(_authorized_by=_authorized_by)

        if self._alerter:
            self._alerter.kill_switch_triggered(f"emergency_close by {_authorized_by}")

        return {
            "closed": len(results),
            "results": results,
        }

    def get_order_history(self, limit: int = 50) -> list[dict]:
        return self._filled_orders[-limit:]
