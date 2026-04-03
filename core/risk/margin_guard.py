"""Margin Guard -- pre-trade margin validation and reporting.

Prevents orders that would cause margin calls or exceed utilization limits.
Handles FX, equities, and futures margin requirements.

Usage:
    guard = MarginGuard(capital=10000)
    result = guard.check_margin_available(broker_state, new_order)
    if not result["ok"]:
        reject_order(result)

    futures_check = guard.check_futures_margin("MES", qty=1)
    report = guard.get_margin_report(positions)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Import futures margin from ibkr_bracket (canonical source)
# Fallback values if import fails
_FUTURES_INITIAL_MARGIN_FALLBACK = {
    "MCL": 600,
    "MES": 1400,
    "MNQ": 1800,
    "MGC": 1100,
}

try:
    from core.broker.ibkr_bracket import FUTURES_INITIAL_MARGIN
except ImportError:
    FUTURES_INITIAL_MARGIN = _FUTURES_INITIAL_MARGIN_FALLBACK
    logger.debug("Using fallback FUTURES_INITIAL_MARGIN (ibkr_bracket not available)")


# Margin rates by asset class
# FX margin rate depends on pair and regulatory regime (ESMA retail: 3.33%-5%)
FX_MARGIN_RATES = {
    "EUR.USD": 0.033,   "EURUSD": 0.033,    # 30:1 leverage -> 3.33% margin
    "GBP.USD": 0.033,   "GBPUSD": 0.033,
    "USD.JPY": 0.033,   "USDJPY": 0.033,
    "EUR.GBP": 0.033,   "EURGBP": 0.033,
    "EUR.JPY": 0.033,   "EURJPY": 0.033,
    "AUD.JPY": 0.05,    "AUDJPY": 0.05,     # 20:1 -> 5% (minor cross)
    "NZD.USD": 0.05,    "NZDUSD": 0.05,
    "USD.CHF": 0.033,   "USDCHF": 0.033,
}
FX_MARGIN_DEFAULT = 0.05  # 5% for unknown FX pairs

# EU equity margin (typically 20% for IBKR margin accounts)
EU_EQUITY_MARGIN_RATE = 0.20

# US equity margin
US_EQUITY_MARGIN_RATE = 0.25  # Reg T

# Utilization thresholds
MARGIN_WARNING_PCT = 0.70
MARGIN_BLOCK_PCT = 0.85
MARGIN_CRITICAL_PCT = 0.95


class MarginGuard:
    """Pre-trade margin validation and utilization monitoring."""

    def __init__(
        self,
        capital: float = 10000,
        margin_warning_pct: float = MARGIN_WARNING_PCT,
        margin_block_pct: float = MARGIN_BLOCK_PCT,
        margin_critical_pct: float = MARGIN_CRITICAL_PCT,
    ):
        self.capital = capital
        self.margin_warning_pct = margin_warning_pct
        self.margin_block_pct = margin_block_pct
        self.margin_critical_pct = margin_critical_pct

    # ------------------------------------------------------------------
    # 1. Pre-trade margin check
    # ------------------------------------------------------------------

    def check_margin_available(
        self,
        broker_state: Dict[str, Any],
        new_order: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Pre-trade margin check: will this order cause a margin call?

        Args:
            broker_state: Current broker account state with keys:
                equity (or net_liquidation), margin_used, buying_power,
                positions (list of current positions).
            new_order: Proposed order with keys:
                symbol, side (BUY|SELL), quantity (or qty, contracts),
                price (or limit_price), asset_class (fx|equity|futures).

        Returns:
            {
                ok: bool,
                margin_required: float,
                margin_available: float,
                margin_after_trade: float,
                utilization_pct: float,
                utilization_after: float,
                level: str (OK|WARNING|BLOCK|CRITICAL),
                reason: str,
            }
        """
        equity = float(broker_state.get(
            "equity", broker_state.get("net_liquidation", self.capital)
        ))
        margin_used = float(broker_state.get("margin_used", 0))
        margin_available = equity - margin_used

        # Estimate margin for the new order
        order_margin = self._estimate_order_margin(new_order)

        margin_after = margin_used + order_margin
        utilization_now = margin_used / equity if equity > 0 else 1.0
        utilization_after = margin_after / equity if equity > 0 else 1.0

        # Determine level
        ok = True
        level = "OK"
        reason = "sufficient margin"

        if utilization_after >= self.margin_critical_pct:
            ok = False
            level = "CRITICAL"
            reason = (
                f"Would exceed critical margin: "
                f"{utilization_after:.1%} >= {self.margin_critical_pct:.0%}"
            )
        elif utilization_after >= self.margin_block_pct:
            ok = False
            level = "BLOCK"
            reason = (
                f"Would exceed block threshold: "
                f"{utilization_after:.1%} >= {self.margin_block_pct:.0%}"
            )
        elif utilization_after >= self.margin_warning_pct:
            ok = True  # Allow but warn
            level = "WARNING"
            reason = (
                f"Approaching margin limit: "
                f"{utilization_after:.1%} >= {self.margin_warning_pct:.0%}"
            )

        if margin_after > equity:
            ok = False
            level = "CRITICAL"
            reason = f"Margin call: required ${margin_after:.0f} > equity ${equity:.0f}"

        result = {
            "ok": ok,
            "margin_required": round(order_margin, 2),
            "margin_available": round(margin_available, 2),
            "margin_after_trade": round(margin_after, 2),
            "utilization_pct": round(utilization_now, 4),
            "utilization_after": round(utilization_after, 4),
            "level": level,
            "reason": reason,
        }

        if not ok:
            symbol = new_order.get("symbol", "?")
            logger.warning(
                f"MarginGuard BLOCKED: {symbol} {level} -- {reason} "
                f"(order_margin=${order_margin:.0f}, "
                f"available=${margin_available:.0f})"
            )

        return result

    # ------------------------------------------------------------------
    # 2. Futures-specific margin check
    # ------------------------------------------------------------------

    def check_futures_margin(
        self,
        contract: str,
        qty: int,
        equity: float | None = None,
        current_futures_margin: float = 0.0,
    ) -> Dict[str, Any]:
        """Check margin for a futures contract order.

        Args:
            contract: Futures symbol (e.g., "MES", "MCL").
            qty: Number of contracts (positive).
            equity: Account equity (defaults to self.capital).
            current_futures_margin: Already-used margin for futures.

        Returns:
            {
                ok: bool,
                contract: str,
                qty: int,
                margin_per_contract: float,
                margin_required: float,
                total_futures_margin_after: float,
                futures_margin_pct: float,
                reason: str,
            }
        """
        eq = equity if equity is not None else self.capital
        qty = abs(qty)
        contract_upper = contract.upper()

        margin_per = FUTURES_INITIAL_MARGIN.get(contract_upper, 0)
        if margin_per == 0:
            return {
                "ok": False,
                "contract": contract_upper,
                "qty": qty,
                "margin_per_contract": 0,
                "margin_required": 0,
                "total_futures_margin_after": current_futures_margin,
                "futures_margin_pct": current_futures_margin / eq if eq > 0 else 0,
                "reason": f"Unknown futures contract: {contract_upper}",
            }

        margin_required = margin_per * qty
        total_after = current_futures_margin + margin_required
        pct_after = total_after / eq if eq > 0 else 1.0

        # Futures-specific limit: max 35% of equity
        max_futures_pct = 0.35
        ok = pct_after <= max_futures_pct

        reason = "sufficient margin"
        if not ok:
            reason = (
                f"Futures margin {pct_after:.1%} would exceed "
                f"{max_futures_pct:.0%} limit"
            )
            logger.warning(
                f"MarginGuard futures BLOCKED: {contract_upper}x{qty} "
                f"margin=${margin_required:.0f} total_pct={pct_after:.1%}"
            )

        return {
            "ok": ok,
            "contract": contract_upper,
            "qty": qty,
            "margin_per_contract": margin_per,
            "margin_required": round(margin_required, 2),
            "total_futures_margin_after": round(total_after, 2),
            "futures_margin_pct": round(pct_after, 4),
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # 3. Margin report by asset class
    # ------------------------------------------------------------------

    def get_margin_report(
        self,
        positions: List[Dict[str, Any]],
        equity: float | None = None,
    ) -> Dict[str, Any]:
        """Generate margin utilization report by asset class.

        Each position dict should have:
            symbol, asset_class (fx|equity|eu_equity|futures),
            market_value (or notional for FX), quantity (or qty, contracts)

        Returns:
            {
                equity: float,
                total_margin_used: float,
                total_utilization_pct: float,
                by_asset_class: {
                    fx: {margin, pct, n_positions},
                    eu_equity: {...},
                    us_equity: {...},
                    futures: {...},
                },
                level: str,
                timestamp: str,
            }
        """
        eq = equity if equity is not None else self.capital

        margin_by_class = {
            "fx": {"margin": 0.0, "n_positions": 0, "notional": 0.0},
            "eu_equity": {"margin": 0.0, "n_positions": 0, "notional": 0.0},
            "us_equity": {"margin": 0.0, "n_positions": 0, "notional": 0.0},
            "futures": {"margin": 0.0, "n_positions": 0, "notional": 0.0},
        }

        for pos in positions:
            symbol = pos.get("symbol", "")
            asset_class = pos.get("asset_class", self._infer_asset_class(symbol))
            value = abs(float(pos.get(
                "market_value", pos.get("notional", pos.get("position_value", 0))
            )))
            qty = abs(int(pos.get("quantity", pos.get("qty", pos.get("contracts", 1)))))

            if asset_class == "fx":
                rate = FX_MARGIN_RATES.get(symbol, FX_MARGIN_DEFAULT)
                margin = value * rate
                bucket = "fx"
            elif asset_class in ("eu_equity", "eu"):
                margin = value * EU_EQUITY_MARGIN_RATE
                bucket = "eu_equity"
            elif asset_class == "futures":
                margin_per = FUTURES_INITIAL_MARGIN.get(symbol.upper(), 0)
                margin = margin_per * qty
                bucket = "futures"
            else:  # us_equity or default
                margin = value * US_EQUITY_MARGIN_RATE
                bucket = "us_equity"

            margin_by_class[bucket]["margin"] += margin
            margin_by_class[bucket]["n_positions"] += 1
            margin_by_class[bucket]["notional"] += value

        total_margin = sum(c["margin"] for c in margin_by_class.values())
        total_pct = total_margin / eq if eq > 0 else 0.0

        level = "OK"
        if total_pct >= self.margin_critical_pct:
            level = "CRITICAL"
        elif total_pct >= self.margin_block_pct:
            level = "BLOCK"
        elif total_pct >= self.margin_warning_pct:
            level = "WARNING"

        by_class = {}
        for cls, data in margin_by_class.items():
            by_class[cls] = {
                "margin": round(data["margin"], 2),
                "pct": round(data["margin"] / eq, 4) if eq > 0 else 0.0,
                "n_positions": data["n_positions"],
                "notional": round(data["notional"], 2),
            }

        return {
            "equity": eq,
            "total_margin_used": round(total_margin, 2),
            "total_utilization_pct": round(total_pct, 4),
            "by_asset_class": by_class,
            "level": level,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _estimate_order_margin(self, order: Dict[str, Any]) -> float:
        """Estimate margin required for a new order."""
        symbol = order.get("symbol", "")
        asset_class = order.get("asset_class", self._infer_asset_class(symbol))
        price = float(order.get("price", order.get("limit_price", 0)))
        qty = abs(float(order.get("quantity", order.get("qty", order.get("contracts", 0)))))

        if asset_class == "fx":
            notional = float(order.get("notional", price * qty))
            rate = FX_MARGIN_RATES.get(symbol, FX_MARGIN_DEFAULT)
            return notional * rate

        elif asset_class == "futures":
            contract = symbol.upper()
            n_contracts = abs(int(qty)) if qty else 1
            margin_per = FUTURES_INITIAL_MARGIN.get(contract, 0)
            return margin_per * n_contracts

        elif asset_class in ("eu_equity", "eu"):
            notional = price * qty
            return notional * EU_EQUITY_MARGIN_RATE

        else:  # us_equity
            notional = price * qty
            return notional * US_EQUITY_MARGIN_RATE

    def _infer_asset_class(self, symbol: str) -> str:
        """Infer asset class from symbol pattern."""
        sym = symbol.upper()

        # FX pairs contain a dot or are 6 chars (EURUSD)
        if "." in sym and len(sym) <= 8:
            parts = sym.split(".")
            if len(parts) == 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
                return "fx"
        if len(sym) == 6 and sym[:3].isalpha() and sym[3:].isalpha():
            # Common FX pair pattern like EURUSD
            known_currencies = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"}
            if sym[:3] in known_currencies and sym[3:] in known_currencies:
                return "fx"

        # Futures: 2-3 letter symbols in the margin dict
        if sym in FUTURES_INITIAL_MARGIN:
            return "futures"

        # EU equities (known tickers or exchange suffixes)
        eu_tickers = set(EU_EQUITY_VOL_MULTIPLIER.keys()) if hasattr(self, '_eu_vol_keys') else {
            "SAP", "SIE", "ALV", "BAS", "DTE", "BMW", "VOW3", "MBG", "ADS",
            "MUV2", "MC", "TTE", "AIR", "SAN", "BNP", "ASML", "DAX", "ESTX50", "CAC40",
        }
        if sym in eu_tickers:
            return "eu_equity"

        return "us_equity"
