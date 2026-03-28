"""
IBKR Bracket Order Manager — broker-side stop loss and take profit.

CRITICAL FOR LIVE TRADING:
Every live trade MUST have a bracket order (OCA group) with:
  - Parent order (entry)
  - Stop loss (child 1)
  - Take profit (child 2)

If the worker crashes, IBKR still executes the stops.
This is the last line of defense for capital protection.

Uses ib_insync's bracket order API.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os as _os
import uuid
from datetime import datetime, timezone
from pathlib import Path as _Path

# Fix Python 3.14: eventkit requires an event loop before ib_insync import
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

logger = logging.getLogger(__name__)

_BRACKETS_STATE_PATH = _Path(__file__).resolve().parent.parent.parent / "data" / "active_brackets.json"

# Futures contract multipliers (mirrors core/risk_manager.py)
FUTURES_MULTIPLIERS = {
    "MES": 5.0,
    "MNQ": 2.0,
    "MCL": 100.0,
    "MGC": 10.0,
}

# Minimum FX lot size on IBKR
FX_MIN_LOT = 25_000

# ---------------------------------------------------------------------------
# HARDEN-004: Tick sizes and buffers for futures
# ---------------------------------------------------------------------------
FUTURES_TICK_SIZES = {
    "MCL": 0.01,
    "MES": 0.25,
    "MNQ": 0.25,
    "MGC": 0.10,
}

FUTURES_SL_BUFFERS = {
    "MCL": 0.02,   # 2 ticks
    "MES": 1.00,   # 4 points (4 * 0.25)
    "MNQ": 0.50,   # 2 points
    "MGC": 0.20,   # 2 ticks
}

# FX pip offset for STP LMT (5 pips)
FX_SL_PIP_OFFSET = 0.0005

# Futures initial margin for maintenance check
FUTURES_INITIAL_MARGIN = {
    "MCL": 600,
    "MES": 1400,
    "MNQ": 1800,
    "MGC": 1100,
}

FUTURES_MAINTENANCE_MARGIN = {
    "MCL": 540,
    "MES": 1260,
    "MNQ": 1620,
    "MGC": 990,
}


class BracketOrderError(Exception):
    """Error specific to bracket order operations."""
    pass


class BracketOrderManager:
    """Creates and manages OCA bracket orders on IBKR.

    For equities: prices in dollars
    For FX: prices in pips, converted to absolute prices
    For futures: prices in points, multiplied by contract multiplier for P&L

    Every bracket creates an OCA (One-Cancels-All) group so that when
    either the stop-loss or take-profit fills, the other is automatically
    cancelled by IBKR — no dependency on the worker process.
    """

    def __init__(self, ib_connection=None):
        """
        Args:
            ib_connection: ib_insync.IB instance (or None for testing)
        """
        self._ib = ib_connection
        # Track active brackets: oca_group -> bracket info
        self._active_brackets: dict[str, dict] = {}
        self._load_brackets()

    # ------------------------------------------------------------------
    # Persistence — crash recovery
    # ------------------------------------------------------------------

    def _save_brackets(self):
        """Persist active brackets to disk for crash recovery."""
        try:
            _BRACKETS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = str(_BRACKETS_STATE_PATH) + ".tmp"
            with open(tmp_path, "w") as f:
                _json.dump(self._active_brackets, f, indent=2)
            _os.replace(tmp_path, str(_BRACKETS_STATE_PATH))
        except Exception as e:
            logger.error(f"Failed to save brackets state: {e}")

    def _load_brackets(self):
        """Load persisted brackets on startup."""
        try:
            if _BRACKETS_STATE_PATH.exists():
                with open(_BRACKETS_STATE_PATH, "r") as f:
                    self._active_brackets = _json.load(f)
                logger.info(f"Loaded {len(self._active_brackets)} persisted brackets")
        except Exception as e:
            logger.warning(f"Failed to load brackets state: {e}")

    # ------------------------------------------------------------------
    # Public API — create brackets
    # ------------------------------------------------------------------

    def create_bracket_order(
        self,
        symbol: str,
        direction: str,
        quantity: int | float,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        instrument_type: str = "EQUITY",
        order_type: str = "LIMIT",
        tif: str = "GTC",
    ) -> dict:
        """Create a bracket order (parent + SL + TP).

        Args:
            symbol: ticker symbol
            direction: "BUY" or "SELL"
            quantity: number of shares/contracts/lots
            entry_price: entry limit price
            stop_loss_price: stop loss trigger price
            take_profit_price: take profit limit price
            instrument_type: EQUITY, FX, FUTURES
            order_type: LIMIT, MARKET for parent
            tif: Time in force (GTC, DAY, IOC)

        Returns:
            dict with {parent_order_id, sl_order_id, tp_order_id, oca_group,
                       status, symbol, direction, quantity, entry_price,
                       stop_loss_price, take_profit_price, instrument_type}

        Raises:
            BracketOrderError: on validation failure or order submission error
        """
        # --- Defensive validations ---
        self._validate_quantity(quantity)
        self._validate_prices(direction, entry_price, stop_loss_price, take_profit_price)
        self._validate_instrument_type(instrument_type)

        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            raise BracketOrderError(
                f"Invalid direction: {direction}. Must be BUY or SELL."
            )

        if order_type.upper() not in ("LIMIT", "MARKET"):
            raise BracketOrderError(
                f"Invalid order_type: {order_type}. Must be LIMIT or MARKET."
            )

        if self._ib is None:
            raise BracketOrderError(
                "No IB connection. Cannot submit bracket orders without a live connection."
            )

        # Lazy import ib_insync types
        from ib_insync import (
            LimitOrder,
            MarketOrder,
            StopOrder,
        )

        contract = self._make_contract(symbol, instrument_type)

        # Qualify contract with IBKR
        try:
            self._ib.qualifyContracts(contract)
        except Exception as e:
            raise BracketOrderError(
                f"Failed to qualify contract {symbol} ({instrument_type}): {e}"
            )

        # OCA group name — unique per bracket
        oca_group = f"BRACKET_{symbol}_{uuid.uuid4().hex[:12]}"

        # Determine actions
        parent_action = direction
        child_action = "SELL" if direction == "BUY" else "BUY"

        # Ensure integer quantity for contracts and shorts
        qty = int(quantity)

        # Determine rounding precision based on instrument type
        rounding = 5 if instrument_type.upper() == "FX" else 2

        # --- Parent order ---
        if order_type.upper() == "MARKET":
            parent = MarketOrder(parent_action, qty)
        else:
            parent = LimitOrder(parent_action, qty, round(entry_price, rounding))

        parent.tif = tif
        parent.transmit = False  # Hold until all children are submitted
        parent.outsideRth = False

        try:
            parent_trade = self._ib.placeOrder(contract, parent)
            self._ib.sleep(0.5)
        except Exception as e:
            raise BracketOrderError(f"Failed to place parent order for {symbol}: {e}")

        parent_order_id = parent.orderId
        if parent_order_id is None or parent_order_id == 0:
            # Retry with longer wait
            self._ib.sleep(2.0)
            parent_order_id = parent.orderId
            if parent_order_id is None or parent_order_id == 0:
                raise BracketOrderError(
                    f"Failed to get parent order ID for {symbol} after 2.5s. "
                    f"IBKR did not confirm the order. Bracket NOT created."
                )

        # --- Stop Loss order (StopOrder) ---
        sl_order = StopOrder(child_action, qty, round(stop_loss_price, rounding))
        sl_order.parentId = parent_order_id
        sl_order.ocaGroup = oca_group
        sl_order.ocaType = 1  # Cancel remaining when one fills
        sl_order.tif = tif
        sl_order.transmit = False  # Wait for TP to be submitted

        try:
            self._ib.placeOrder(contract, sl_order)
        except Exception as e:
            # Attempt to cancel parent if SL fails
            logger.error(f"SL order failed for {symbol}, cancelling parent: {e}")
            self._ib.cancelOrder(parent)
            raise BracketOrderError(f"Failed to place SL order for {symbol}: {e}")

        sl_order_id = sl_order.orderId

        # --- Take Profit order (LimitOrder) ---
        tp_order = LimitOrder(child_action, qty, round(take_profit_price, rounding))
        tp_order.parentId = parent_order_id
        tp_order.ocaGroup = oca_group
        tp_order.ocaType = 1
        tp_order.tif = tif
        tp_order.transmit = True  # Final leg — transmit the entire bracket

        try:
            self._ib.placeOrder(contract, tp_order)
        except Exception as e:
            logger.error(f"TP order failed for {symbol}, cancelling bracket: {e}")
            self._ib.cancelOrder(parent)
            self._ib.cancelOrder(sl_order)
            raise BracketOrderError(f"Failed to place TP order for {symbol}: {e}")

        tp_order_id = tp_order.orderId

        # Verify all orders are accepted (not rejected asynchronously)
        self._ib.sleep(1.0)  # Allow time for IBKR to process
        open_order_ids = {t.order.orderId for t in self._ib.openTrades()}
        missing = []
        for label, oid in [("parent", parent_order_id), ("SL", sl_order_id), ("TP", tp_order_id)]:
            if oid not in open_order_ids:
                missing.append(f"{label}(id={oid})")

        if missing:
            # Cancel all orders in the bracket to avoid partial protection
            for trade in self._ib.openTrades():
                if trade.order.orderId in (parent_order_id, sl_order_id, tp_order_id):
                    try:
                        self._ib.cancelOrder(trade.order)
                    except Exception:
                        pass
            raise BracketOrderError(
                f"Bracket verification FAILED for {symbol}: "
                f"orders not confirmed by IBKR: {', '.join(missing)}. "
                f"All bracket orders cancelled for safety."
            )

        # Track the bracket
        bracket_info = {
            "oca_group": oca_group,
            "parent_order_id": parent_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "symbol": symbol,
            "direction": direction,
            "quantity": qty,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "instrument_type": instrument_type,
            "tif": tif,
            "status": "SUBMITTED",
        }
        self._active_brackets[oca_group] = bracket_info
        self._save_brackets()

        logger.info(
            f"BRACKET submitted: {direction} {qty}x {symbol} "
            f"@ {entry_price:.2f} | SL={stop_loss_price:.2f} TP={take_profit_price:.2f} "
            f"| OCA={oca_group}"
        )

        return bracket_info

    def create_equity_bracket(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """Convenience for equity bracket orders."""
        return self.create_bracket_order(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            instrument_type="EQUITY",
            order_type="LIMIT",
            tif="GTC",
        )

    def create_fx_bracket(
        self,
        pair: str,
        direction: str,
        lot_size: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """Convenience for FX bracket orders.

        IBKR FX minimum lot: 25,000 units.

        Args:
            pair: FX pair (e.g. "EURUSD")
            direction: "BUY" or "SELL"
            lot_size: number of units (must be >= 25,000)
            entry_price: entry price
            stop_loss_price: stop loss price
            take_profit_price: take profit price

        Raises:
            BracketOrderError: if lot_size < 25,000
        """
        if lot_size < FX_MIN_LOT:
            raise BracketOrderError(
                f"FX lot size {lot_size} below IBKR minimum ({FX_MIN_LOT}). "
                f"IBKR requires at least {FX_MIN_LOT} units for FX."
            )

        return self.create_bracket_order(
            symbol=pair,
            direction=direction,
            quantity=lot_size,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            instrument_type="FX",
            order_type="LIMIT",
            tif="GTC",
        )

    def create_futures_bracket(
        self,
        symbol: str,
        direction: str,
        contracts: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """Convenience for futures micro bracket orders.

        Validates that the symbol is a known futures contract before submitting.

        Args:
            symbol: futures symbol (e.g. "MES", "MNQ", "MCL", "MGC")
            direction: "BUY" or "SELL"
            contracts: number of contracts (integer)
            entry_price: entry price
            stop_loss_price: stop loss price
            take_profit_price: take profit price

        Raises:
            BracketOrderError: if symbol is not a known futures contract
        """
        if not isinstance(contracts, int) or contracts <= 0:
            raise BracketOrderError(
                f"Futures contracts must be a positive integer, got: {contracts}"
            )

        if symbol not in FUTURES_MULTIPLIERS:
            raise BracketOrderError(
                f"Unknown futures symbol: {symbol}. "
                f"Supported: {list(FUTURES_MULTIPLIERS.keys())}"
            )

        return self.create_bracket_order(
            symbol=symbol,
            direction=direction,
            quantity=contracts,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            instrument_type="FUTURES",
            order_type="LIMIT",
            tif="GTC",
        )

    # ------------------------------------------------------------------
    # Public API — modify / cancel
    # ------------------------------------------------------------------

    def modify_stop_loss(self, bracket_id: str, new_stop_price: float) -> dict:
        """Modify the stop loss of an existing bracket (trailing stop, etc.).

        Args:
            bracket_id: OCA group name identifying the bracket
            new_stop_price: new stop loss trigger price

        Returns:
            Updated bracket info dict

        Raises:
            BracketOrderError: if bracket not found or modification fails
        """
        if new_stop_price <= 0:
            raise BracketOrderError(
                f"Invalid stop price: {new_stop_price}. Must be positive."
            )

        bracket = self._get_bracket(bracket_id)

        if self._ib is None:
            raise BracketOrderError("No IB connection for modify_stop_loss.")

        from ib_insync import StopOrder

        # Find the SL order among open orders
        sl_order_id = bracket["sl_order_id"]
        open_trades = self._ib.openTrades()
        sl_trade = None
        for trade in open_trades:
            if trade.order.orderId == sl_order_id:
                sl_trade = trade
                break

        if sl_trade is None:
            raise BracketOrderError(
                f"SL order {sl_order_id} not found in open orders for bracket {bracket_id}."
            )

        # Modify the stop price
        sl_trade.order.auxPrice = round(new_stop_price, 2)
        self._ib.placeOrder(sl_trade.contract, sl_trade.order)

        bracket["stop_loss_price"] = new_stop_price
        self._save_brackets()
        logger.info(
            f"BRACKET SL modified: {bracket['symbol']} OCA={bracket_id} "
            f"new_SL={new_stop_price:.2f}"
        )

        return bracket

    def modify_take_profit(self, bracket_id: str, new_tp_price: float) -> dict:
        """Modify the take profit of an existing bracket.

        Args:
            bracket_id: OCA group name identifying the bracket
            new_tp_price: new take profit limit price

        Returns:
            Updated bracket info dict

        Raises:
            BracketOrderError: if bracket not found or modification fails
        """
        if new_tp_price <= 0:
            raise BracketOrderError(
                f"Invalid TP price: {new_tp_price}. Must be positive."
            )

        bracket = self._get_bracket(bracket_id)

        if self._ib is None:
            raise BracketOrderError("No IB connection for modify_take_profit.")

        from ib_insync import LimitOrder

        # Find the TP order among open orders
        tp_order_id = bracket["tp_order_id"]
        open_trades = self._ib.openTrades()
        tp_trade = None
        for trade in open_trades:
            if trade.order.orderId == tp_order_id:
                tp_trade = trade
                break

        if tp_trade is None:
            raise BracketOrderError(
                f"TP order {tp_order_id} not found in open orders for bracket {bracket_id}."
            )

        # Modify the limit price
        tp_trade.order.lmtPrice = round(new_tp_price, 2)
        self._ib.placeOrder(tp_trade.contract, tp_trade.order)

        bracket["take_profit_price"] = new_tp_price
        self._save_brackets()
        logger.info(
            f"BRACKET TP modified: {bracket['symbol']} OCA={bracket_id} "
            f"new_TP={new_tp_price:.2f}"
        )

        return bracket

    def cancel_bracket(self, bracket_id: str) -> dict:
        """Cancel all orders in a bracket group (parent + children).

        Args:
            bracket_id: OCA group name identifying the bracket

        Returns:
            {bracket_id, cancelled_orders: int, status: "CANCELLED"}

        Raises:
            BracketOrderError: if bracket not found
        """
        bracket = self._get_bracket(bracket_id)

        if self._ib is None:
            raise BracketOrderError("No IB connection for cancel_bracket.")

        order_ids = [
            bracket["parent_order_id"],
            bracket["sl_order_id"],
            bracket["tp_order_id"],
        ]

        cancelled = 0
        open_trades = self._ib.openTrades()
        for trade in open_trades:
            if trade.order.orderId in order_ids:
                try:
                    self._ib.cancelOrder(trade.order)
                    cancelled += 1
                except Exception as e:
                    logger.warning(
                        f"Failed to cancel order {trade.order.orderId} "
                        f"in bracket {bracket_id}: {e}"
                    )

        bracket["status"] = "CANCELLED"
        self._save_brackets()
        logger.info(
            f"BRACKET cancelled: {bracket['symbol']} OCA={bracket_id} "
            f"({cancelled} orders cancelled)"
        )

        return {
            "bracket_id": bracket_id,
            "cancelled_orders": cancelled,
            "status": "CANCELLED",
        }

    # ------------------------------------------------------------------
    # Public API — query
    # ------------------------------------------------------------------

    def get_active_brackets(self) -> list[dict]:
        """List all active bracket orders.

        Returns:
            list of {bracket_id, symbol, direction, qty, entry, sl, tp, status}
        """
        result = []
        for oca_group, info in self._active_brackets.items():
            if info["status"] not in ("CANCELLED", "FILLED"):
                result.append({
                    "bracket_id": oca_group,
                    "symbol": info["symbol"],
                    "direction": info["direction"],
                    "qty": info["quantity"],
                    "entry": info["entry_price"],
                    "sl": info["stop_loss_price"],
                    "tp": info["take_profit_price"],
                    "instrument_type": info["instrument_type"],
                    "status": info["status"],
                })
        return result

    def verify_bracket_integrity(self) -> dict:
        """Verify ALL open positions have associated bracket orders.

        Checks every position reported by IBKR and verifies there is
        a matching active bracket order protecting it.

        Returns:
            {all_protected: bool, unprotected: [symbols], details: [...]}

        CRITICAL: Alert if any position is unprotected.
        """
        if self._ib is None:
            raise BracketOrderError(
                "No IB connection for verify_bracket_integrity."
            )

        positions = self._ib.positions()
        if not positions:
            return {
                "all_protected": True,
                "unprotected": [],
                "details": [],
                "total_positions": 0,
            }

        # Build set of symbols with active brackets
        bracketed_symbols: set[str] = set()
        for info in self._active_brackets.values():
            if info["status"] not in ("CANCELLED", "FILLED"):
                bracketed_symbols.add(info["symbol"])

        unprotected = []
        details = []

        for pos in positions:
            symbol = pos.contract.symbol
            qty = float(pos.position) if hasattr(pos, "position") else 0
            if qty == 0:
                continue  # Skip flat positions

            has_bracket = symbol in bracketed_symbols
            detail = {
                "symbol": symbol,
                "qty": qty,
                "side": "long" if qty > 0 else "short",
                "protected": has_bracket,
            }
            details.append(detail)

            if not has_bracket:
                unprotected.append(symbol)
                logger.critical(
                    f"UNPROTECTED POSITION: {symbol} qty={qty} "
                    f"has NO bracket order! Capital at risk!"
                )

        # Also check that the actual SL/TP orders are still active
        open_order_ids = {t.order.orderId for t in self._ib.openTrades()}
        for info in self._active_brackets.values():
            if info["status"] not in ("CANCELLED", "FILLED"):
                sl_active = info["sl_order_id"] in open_order_ids
                tp_active = info["tp_order_id"] in open_order_ids
                if not sl_active or not tp_active:
                    symbol = info["symbol"]
                    if symbol not in unprotected:
                        unprotected.append(symbol)
                    missing_parts = []
                    if not sl_active:
                        missing_parts.append("SL")
                    if not tp_active:
                        missing_parts.append("TP")
                    logger.critical(
                        f"PARTIAL BRACKET: {symbol} OCA={info['oca_group']} "
                        f"missing {'+'.join(missing_parts)} in open orders!"
                    )

        all_protected = len(unprotected) == 0

        if all_protected:
            logger.info(
                f"Bracket integrity OK: {len(details)} positions, all protected."
            )
        else:
            logger.critical(
                f"BRACKET INTEGRITY FAILURE: {len(unprotected)}/{len(details)} "
                f"positions UNPROTECTED: {unprotected}"
            )

        return {
            "all_protected": all_protected,
            "unprotected": unprotected,
            "details": details,
            "total_positions": len(details),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_contract(self, symbol: str, instrument_type: str):
        """Create ib_insync Contract object for the given symbol/type.

        Args:
            symbol: ticker / pair / futures symbol
            instrument_type: EQUITY, FX, or FUTURES

        Returns:
            ib_insync contract object (Stock, Forex, or Future)

        Raises:
            BracketOrderError: if instrument_type is unsupported
        """
        instrument_type = instrument_type.upper()

        if instrument_type == "EQUITY":
            from ib_insync import Stock
            return Stock(symbol, "SMART", "USD")

        elif instrument_type == "FX":
            from ib_insync import Forex
            # IBKR Forex pairs: EURUSD → base=EUR, quote=USD
            # ib_insync Forex expects the pair as "EURUSD"
            return Forex(symbol)

        elif instrument_type == "FUTURES":
            from ib_insync import Future

            # Determine exchange from known symbols
            exchange_map = {
                "MES": "CME", "MNQ": "CME", "MCL": "NYMEX", "MGC": "COMEX",
                "ES": "CME", "NQ": "CME", "CL": "NYMEX", "GC": "COMEX",
            }
            exchange = exchange_map.get(symbol, "CME")

            return Future(
                symbol=symbol,
                exchange=exchange,
                currency="USD",
            )

        else:
            raise BracketOrderError(
                f"Unsupported instrument type: {instrument_type}. "
                f"Must be EQUITY, FX, or FUTURES."
            )

    def _validate_prices(
        self,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        """Validate that SL and TP are on correct sides of entry.

        LONG:  SL < entry < TP
        SHORT: TP < entry < SL

        Raises:
            BracketOrderError: if prices are invalid
        """
        if entry <= 0:
            raise BracketOrderError(
                f"Entry price must be positive, got: {entry}"
            )
        if stop_loss <= 0:
            raise BracketOrderError(
                f"Stop loss price must be positive, got: {stop_loss}"
            )
        if take_profit <= 0:
            raise BracketOrderError(
                f"Take profit price must be positive, got: {take_profit}"
            )

        direction = direction.upper()

        if direction == "BUY":
            # LONG: SL < entry < TP
            if stop_loss >= entry:
                raise BracketOrderError(
                    f"LONG bracket: stop_loss ({stop_loss}) must be BELOW "
                    f"entry ({entry}). Got SL >= entry."
                )
            if take_profit <= entry:
                raise BracketOrderError(
                    f"LONG bracket: take_profit ({take_profit}) must be ABOVE "
                    f"entry ({entry}). Got TP <= entry."
                )

        elif direction == "SELL":
            # SHORT: TP < entry < SL
            if stop_loss <= entry:
                raise BracketOrderError(
                    f"SHORT bracket: stop_loss ({stop_loss}) must be ABOVE "
                    f"entry ({entry}). Got SL <= entry."
                )
            if take_profit >= entry:
                raise BracketOrderError(
                    f"SHORT bracket: take_profit ({take_profit}) must be BELOW "
                    f"entry ({entry}). Got TP >= entry."
                )

    def _validate_quantity(self, quantity: int | float) -> None:
        """Validate that quantity is positive and non-zero.

        Raises:
            BracketOrderError: if quantity is invalid
        """
        if quantity is None:
            raise BracketOrderError("Quantity cannot be None.")
        if not isinstance(quantity, (int, float)):
            raise BracketOrderError(
                f"Quantity must be a number, got: {type(quantity).__name__}"
            )
        if quantity <= 0:
            raise BracketOrderError(
                f"Quantity must be positive, got: {quantity}"
            )

    def _validate_instrument_type(self, instrument_type: str) -> None:
        """Validate instrument type."""
        valid = ("EQUITY", "FX", "FUTURES")
        if instrument_type.upper() not in valid:
            raise BracketOrderError(
                f"Invalid instrument_type: {instrument_type}. Must be one of {valid}."
            )

    def _get_bracket(self, bracket_id: str) -> dict:
        """Retrieve a tracked bracket by its OCA group ID.

        Raises:
            BracketOrderError: if bracket not found
        """
        if bracket_id not in self._active_brackets:
            raise BracketOrderError(
                f"Bracket {bracket_id} not found. "
                f"Active brackets: {list(self._active_brackets.keys())}"
            )
        return self._active_brackets[bracket_id]


# =========================================================================
# HARDEN-004: FX-specific bracket handler
# =========================================================================

class FXBracketHandler:
    """FX-specific bracket logic with IBKR IDEALPRO specifics.

    Key differences from equity brackets:
    - Stop = STP LMT (not STP MKT) to avoid weekend gap slippage
    - Stop limit price = stop_price - FX_SL_PIP_OFFSET (for BUY)
                       or stop_price + FX_SL_PIP_OFFSET (for SELL)
    - TIF = GTC (survives weekend)
    - OCA group for auto-cancellation
    """

    def __init__(self, bracket_manager: BracketOrderManager):
        self._bm = bracket_manager

    def create_fx_bracket_v2(
        self,
        pair: str,
        direction: str,
        lot_size: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """Create FX bracket with STP LMT instead of STP MKT.

        Returns bracket_info dict with additional fx_specific fields
        including stop_limit_price.

        Args:
            pair: FX pair (e.g. "EURUSD")
            direction: "BUY" or "SELL"
            lot_size: number of units (must be >= FX_MIN_LOT)
            entry_price: entry limit price
            stop_loss_price: stop loss trigger price
            take_profit_price: take profit limit price

        Returns:
            dict with bracket info + stop_limit_price, order_type_sl="STP_LMT"

        Raises:
            BracketOrderError: on validation failure
        """
        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            raise BracketOrderError(
                f"Invalid direction: {direction}. Must be BUY or SELL."
            )

        # Validate lot size
        if lot_size < FX_MIN_LOT:
            raise BracketOrderError(
                f"FX lot size {lot_size} below IBKR minimum ({FX_MIN_LOT}). "
                f"IBKR requires at least {FX_MIN_LOT} units for FX."
            )

        # Validate prices via the bracket manager
        self._bm._validate_prices(direction, entry_price, stop_loss_price, take_profit_price)

        # Calculate stop limit price with pip offset
        if direction == "BUY":
            # BUY: SL is below entry -> stop limit even further below stop
            stop_limit_price = round(stop_loss_price - FX_SL_PIP_OFFSET, 5)
        else:
            # SELL: SL is above entry -> stop limit even further above stop
            stop_limit_price = round(stop_loss_price + FX_SL_PIP_OFFSET, 5)

        # OCA group name
        oca_group = f"BRACKET_{pair}_{uuid.uuid4().hex[:12]}"
        child_action = "SELL" if direction == "BUY" else "BUY"
        qty = int(lot_size)

        # If IB connection available, submit real orders with StopLimitOrder
        if self._bm._ib is not None:
            from ib_insync import LimitOrder, StopLimitOrder

            contract = self._bm._make_contract(pair, "FX")
            try:
                self._bm._ib.qualifyContracts(contract)
            except Exception as e:
                raise BracketOrderError(
                    f"Failed to qualify FX contract {pair}: {e}"
                )

            # Parent order (LIMIT entry)
            parent = LimitOrder(direction, qty, round(entry_price, 5))
            parent.tif = "GTC"
            parent.transmit = False
            parent.outsideRth = False

            try:
                self._bm._ib.placeOrder(contract, parent)
                self._bm._ib.sleep(0.5)
            except Exception as e:
                raise BracketOrderError(f"Failed to place FX parent order for {pair}: {e}")

            parent_order_id = parent.orderId

            # Stop Loss — STP LMT (the core fix for HARDEN-004)
            sl_order = StopLimitOrder(
                child_action, qty,
                round(stop_loss_price, 5),   # stop trigger price
                round(stop_limit_price, 5),  # limit price after trigger
            )
            sl_order.parentId = parent_order_id
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            sl_order.tif = "GTC"
            sl_order.transmit = False

            try:
                self._bm._ib.placeOrder(contract, sl_order)
            except Exception as e:
                logger.error(f"FX SL order failed for {pair}, cancelling parent: {e}")
                self._bm._ib.cancelOrder(parent)
                raise BracketOrderError(f"Failed to place FX SL order for {pair}: {e}")

            sl_order_id = sl_order.orderId

            # Take Profit — LimitOrder
            tp_order = LimitOrder(child_action, qty, round(take_profit_price, 5))
            tp_order.parentId = parent_order_id
            tp_order.ocaGroup = oca_group
            tp_order.ocaType = 1
            tp_order.tif = "GTC"
            tp_order.transmit = True  # Final leg — transmit entire bracket

            try:
                self._bm._ib.placeOrder(contract, tp_order)
            except Exception as e:
                logger.error(f"FX TP order failed for {pair}, cancelling bracket: {e}")
                self._bm._ib.cancelOrder(parent)
                self._bm._ib.cancelOrder(sl_order)
                raise BracketOrderError(f"Failed to place FX TP order for {pair}: {e}")

            tp_order_id = tp_order.orderId
        else:
            # No IB connection — dry-run / testing mode
            parent_order_id = None
            sl_order_id = None
            tp_order_id = None

        bracket_info = {
            "oca_group": oca_group,
            "parent_order_id": parent_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "symbol": pair,
            "direction": direction,
            "quantity": qty,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "stop_limit_price": stop_limit_price,
            "take_profit_price": take_profit_price,
            "instrument_type": "FX",
            "tif": "GTC",
            "order_type_sl": "STP_LMT",
            "status": "SUBMITTED" if self._bm._ib is not None else "DRY_RUN",
        }
        self._bm._active_brackets[oca_group] = bracket_info
        self._bm._save_brackets()

        logger.info(
            f"FX BRACKET v2 submitted: {direction} {qty}x {pair} "
            f"@ {entry_price:.5f} | SL={stop_loss_price:.5f} "
            f"(limit={stop_limit_price:.5f}) TP={take_profit_price:.5f} "
            f"| OCA={oca_group}"
        )

        return bracket_info

    def pre_weekend_check(self, alert_callback=None) -> dict:
        """Friday 16h ET: verify ALL FX positions have active brackets.

        Returns:
            {all_protected: bool, unprotected_pairs: list, checked_at: str}
        """
        checked_at = datetime.now(timezone.utc).isoformat()

        # Build set of FX symbols with active brackets
        protected_fx: set[str] = set()
        for info in self._bm._active_brackets.values():
            if (
                info.get("instrument_type") == "FX"
                and info.get("status") not in ("CANCELLED", "FILLED")
            ):
                protected_fx.add(info["symbol"])

        # Get FX positions from broker (if available)
        unprotected_pairs: list[str] = []

        if self._bm._ib is not None:
            positions = self._bm._ib.positions()
            for pos in positions:
                symbol = pos.contract.symbol
                qty = float(pos.position) if hasattr(pos, "position") else 0
                if qty == 0:
                    continue
                # Check if this looks like an FX position and is unprotected
                sec_type = getattr(pos.contract, "secType", "")
                if sec_type == "CASH" and symbol not in protected_fx:
                    unprotected_pairs.append(symbol)
        else:
            # No IB connection — check only against tracked brackets
            # In dry-run mode, we have no positions to check
            pass

        all_protected = len(unprotected_pairs) == 0

        if not all_protected:
            msg = (
                f"PRE-WEEKEND CHECK FAILED: {len(unprotected_pairs)} FX pairs "
                f"unprotected: {unprotected_pairs}"
            )
            logger.critical(msg)
            if alert_callback is not None:
                alert_callback(msg)
        else:
            logger.info(
                f"Pre-weekend check OK: {len(protected_fx)} FX pairs protected."
            )

        return {
            "all_protected": all_protected,
            "unprotected_pairs": unprotected_pairs,
            "checked_at": checked_at,
        }

    def check_position_bracket(self, pair: str) -> bool:
        """Check if a specific FX pair has an active bracket.

        Args:
            pair: FX pair symbol (e.g. "EURUSD")

        Returns:
            True if pair has an active bracket, False otherwise
        """
        for info in self._bm._active_brackets.values():
            if (
                info.get("symbol") == pair
                and info.get("instrument_type") == "FX"
                and info.get("status") not in ("CANCELLED", "FILLED")
            ):
                return True
        return False


# =========================================================================
# HARDEN-004: Futures-specific bracket handler
# =========================================================================

class FuturesBracketHandler:
    """Futures-specific bracket logic with tick-size awareness.

    Key differences:
    - Stop = STP LMT with tick-size-aware buffer
    - Buffer varies by contract (MCL: 2 ticks, MES: 4 points)
    - TIF = GTC (survives overnight session)
    - Maintenance margin check before entry
    """

    def __init__(self, bracket_manager: BracketOrderManager):
        self._bm = bracket_manager

    def create_futures_bracket_v2(
        self,
        symbol: str,
        direction: str,
        contracts: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """Create futures bracket with tick-aware STP LMT buffer.

        Returns bracket_info with buffer details.

        Args:
            symbol: futures symbol (e.g. "MES", "MCL")
            direction: "BUY" or "SELL"
            contracts: number of contracts (positive integer)
            entry_price: entry limit price
            stop_loss_price: stop loss trigger price
            take_profit_price: take profit limit price

        Returns:
            dict with bracket info + stop_limit_price, tick_size, buffer

        Raises:
            BracketOrderError: on validation failure
        """
        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            raise BracketOrderError(
                f"Invalid direction: {direction}. Must be BUY or SELL."
            )

        if not isinstance(contracts, int) or contracts <= 0:
            raise BracketOrderError(
                f"Futures contracts must be a positive integer, got: {contracts}"
            )

        if symbol not in FUTURES_TICK_SIZES:
            raise BracketOrderError(
                f"Unknown futures symbol: {symbol}. "
                f"Supported: {list(FUTURES_TICK_SIZES.keys())}"
            )

        # Validate prices
        self._bm._validate_prices(direction, entry_price, stop_loss_price, take_profit_price)

        # Get tick-aware buffer
        tick_size = self.get_tick_size(symbol)
        buffer = self.get_buffer(symbol)

        # Calculate stop limit price with buffer
        if direction == "BUY":
            # BUY: SL below entry -> stop limit further below stop
            stop_limit_price = round(stop_loss_price - buffer, 2)
        else:
            # SELL: SL above entry -> stop limit further above stop
            stop_limit_price = round(stop_loss_price + buffer, 2)

        # OCA group name
        oca_group = f"BRACKET_{symbol}_{uuid.uuid4().hex[:12]}"
        child_action = "SELL" if direction == "BUY" else "BUY"

        # If IB connection available, submit real orders
        if self._bm._ib is not None:
            from ib_insync import LimitOrder, StopLimitOrder

            contract = self._bm._make_contract(symbol, "FUTURES")
            try:
                self._bm._ib.qualifyContracts(contract)
            except Exception as e:
                raise BracketOrderError(
                    f"Failed to qualify futures contract {symbol}: {e}"
                )

            # Parent order
            parent = LimitOrder(direction, contracts, round(entry_price, 2))
            parent.tif = "GTC"
            parent.transmit = False
            parent.outsideRth = True  # Futures trade overnight

            try:
                self._bm._ib.placeOrder(contract, parent)
                self._bm._ib.sleep(0.5)
            except Exception as e:
                raise BracketOrderError(
                    f"Failed to place futures parent order for {symbol}: {e}"
                )

            parent_order_id = parent.orderId

            # Stop Loss — STP LMT with tick-aware buffer
            sl_order = StopLimitOrder(
                child_action, contracts,
                round(stop_loss_price, 2),
                round(stop_limit_price, 2),
            )
            sl_order.parentId = parent_order_id
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            sl_order.tif = "GTC"
            sl_order.transmit = False

            try:
                self._bm._ib.placeOrder(contract, sl_order)
            except Exception as e:
                logger.error(
                    f"Futures SL order failed for {symbol}, cancelling parent: {e}"
                )
                self._bm._ib.cancelOrder(parent)
                raise BracketOrderError(
                    f"Failed to place futures SL order for {symbol}: {e}"
                )

            sl_order_id = sl_order.orderId

            # Take Profit — LimitOrder
            tp_order = LimitOrder(
                child_action, contracts, round(take_profit_price, 2)
            )
            tp_order.parentId = parent_order_id
            tp_order.ocaGroup = oca_group
            tp_order.ocaType = 1
            tp_order.tif = "GTC"
            tp_order.transmit = True

            try:
                self._bm._ib.placeOrder(contract, tp_order)
            except Exception as e:
                logger.error(
                    f"Futures TP order failed for {symbol}, cancelling bracket: {e}"
                )
                self._bm._ib.cancelOrder(parent)
                self._bm._ib.cancelOrder(sl_order)
                raise BracketOrderError(
                    f"Failed to place futures TP order for {symbol}: {e}"
                )

            tp_order_id = tp_order.orderId
        else:
            # No IB connection — dry-run / testing mode
            parent_order_id = None
            sl_order_id = None
            tp_order_id = None

        bracket_info = {
            "oca_group": oca_group,
            "parent_order_id": parent_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "symbol": symbol,
            "direction": direction,
            "quantity": contracts,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "stop_limit_price": stop_limit_price,
            "take_profit_price": take_profit_price,
            "instrument_type": "FUTURES",
            "tif": "GTC",
            "order_type_sl": "STP_LMT",
            "tick_size": tick_size,
            "buffer": buffer,
            "status": "SUBMITTED" if self._bm._ib is not None else "DRY_RUN",
        }
        self._bm._active_brackets[oca_group] = bracket_info
        self._bm._save_brackets()

        logger.info(
            f"FUTURES BRACKET v2 submitted: {direction} {contracts}x {symbol} "
            f"@ {entry_price:.2f} | SL={stop_loss_price:.2f} "
            f"(limit={stop_limit_price:.2f}, buffer={buffer}) "
            f"TP={take_profit_price:.2f} | OCA={oca_group}"
        )

        return bracket_info

    def pre_maintenance_check(
        self, available_cash: float, alert_callback=None
    ) -> dict:
        """Check maintenance margin coverage before overnight session.

        For each active futures position, verifies that available cash
        covers maintenance_margin * 1.2 (20% safety buffer).

        Args:
            available_cash: current available cash in account
            alert_callback: optional callable for sending alerts

        Returns:
            {all_covered: bool, warnings: list[str], details: list[dict]}
        """
        warnings: list[str] = []
        details: list[dict] = []

        for oca_group, info in self._bm._active_brackets.items():
            if info.get("instrument_type") != "FUTURES":
                continue
            if info.get("status") in ("CANCELLED", "FILLED"):
                continue

            symbol = info["symbol"]
            qty = info["quantity"]
            maint_margin = FUTURES_MAINTENANCE_MARGIN.get(symbol, 0)
            required = maint_margin * qty * 1.2  # 20% safety buffer

            covered = available_cash >= required
            detail = {
                "symbol": symbol,
                "quantity": qty,
                "maintenance_margin": maint_margin,
                "required_with_buffer": required,
                "available_cash": available_cash,
                "covered": covered,
                "oca_group": oca_group,
            }
            details.append(detail)

            if not covered:
                msg = (
                    f"MARGIN WARNING: {symbol} x{qty} requires "
                    f"${required:.0f} (maint ${maint_margin} * {qty} * 1.2) "
                    f"but only ${available_cash:.0f} available"
                )
                warnings.append(msg)
                logger.warning(msg)

        all_covered = len(warnings) == 0

        if not all_covered and alert_callback is not None:
            alert_callback(
                f"PRE-MAINTENANCE CHECK: {len(warnings)} margin warnings — "
                + "; ".join(warnings)
            )
        elif all_covered:
            logger.info(
                f"Pre-maintenance check OK: {len(details)} futures positions, "
                f"all margin covered."
            )

        return {
            "all_covered": all_covered,
            "warnings": warnings,
            "details": details,
        }

    def get_tick_size(self, symbol: str) -> float:
        """Return the tick size for a futures symbol."""
        return FUTURES_TICK_SIZES.get(symbol, 0.01)

    def get_buffer(self, symbol: str) -> float:
        """Return the SL buffer for a futures symbol."""
        return FUTURES_SL_BUFFERS.get(symbol, 0.02)
