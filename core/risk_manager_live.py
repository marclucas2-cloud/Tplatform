"""
LiveRiskManager — Real-money risk management for IBKR $10K account.

Extends RiskManager with:
  - Tighter limits calibrated for $10K capital
  - Audit log of every risk check (separate file)
  - Progressive deleveraging (3 levels based on absolute DD)
  - Weekly circuit breaker
  - Margin monitoring with block at 85%
  - Max positions count enforcement
  - check_all_limits() returning a comprehensive dict

Does NOT modify any existing files or behavior.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Dict, List, Optional

import yaml

from core.risk_manager import RiskManager

logger = logging.getLogger(__name__)

# Audit log directory
AUDIT_LOG_DIR = Path(__file__).parent.parent / "logs" / "risk_audit"


class LiveRiskManager(RiskManager):
    """Risk manager calibrated for live trading with real capital.

    Loads limits from config/limits_live.yaml by default. All checks are
    logged to an audit file for post-trade review.
    """

    def __init__(self, limits_path=None):
        if limits_path is None:
            limits_path = Path(__file__).parent.parent / "config" / "limits_live.yaml"
        super().__init__(limits_path=limits_path)

        # Thread-safety lock for validate_order
        self._validate_lock = threading.Lock()

        # Live-specific config sections
        self.live_limits = self.limits
        self.capital = self.limits.get("capital", 10_000)
        self.mode = self.limits.get("mode", "LIVE")

        self.position_limits = self.limits.get("position_limits", {})
        self.margin_limits = self.limits.get("margin_limits", {})
        self.circuit_breakers_cfg = self.limits.get("circuit_breakers", {})
        self.kill_switch_cfg = self.limits.get("kill_switch", {})
        self.deleveraging_cfg = self.limits.get("deleveraging", {})
        self.sector_limits_cfg = self.limits.get("sector_limits", {})
        self.fx_limits_cfg = self.limits.get("fx_limits", {})
        self.futures_limits_cfg = self.limits.get("futures_limits", {})
        self.combined_limits_cfg = self.limits.get("combined_limits", {})

        # Ensure audit log directory exists
        AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"LiveRiskManager initialized — mode={self.mode}, "
            f"capital=${self.capital:,.0f}"
        )

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _audit_log(self, check_name: str, passed: bool, details: dict):
        """Append a risk check result to the daily audit log file."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "check": check_name,
            "passed": passed,
            "details": details,
        }
        log_file = AUDIT_LOG_DIR / f"audit_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    # ------------------------------------------------------------------
    # Public API — validate_order override
    # ------------------------------------------------------------------

    def validate_order(self, order: dict, portfolio: dict) -> Tuple[bool, str]:
        """Validate an order against ALL live limits.

        Overrides parent to use live-calibrated limits and audit each check.

        Args:
            order: {symbol, direction, notional, strategy, asset_class}
            portfolio: {equity, positions: [...], cash}

        Returns:
            (passed: bool, message: str)
        """
        with self._validate_lock:
            checks = [
                ("position_limit", self._check_position_limit(order, portfolio)),
                ("strategy_limit", self._check_strategy_limit(order, portfolio)),
                ("exposure_long", self._check_exposure_long(order, portfolio)),
                ("exposure_short", self._check_exposure_short(order, portfolio)),
                ("gross_exposure", self._check_gross_exposure(order, portfolio)),
                ("max_positions", self._check_max_positions(order, portfolio)),
                ("cash_reserve", self._check_cash_reserve(order, portfolio)),
                ("sector_limit", self._check_sector_limit(order, portfolio)),
                ("margin_block", self._check_margin_block(order, portfolio)),
                ("fx_exposure", self._check_fx_exposure(order, portfolio)),
                ("futures_exposure", self._check_futures_exposure(order, portfolio)),
                ("combined_margin", self._check_combined_margin(order, portfolio)),
            ]

            for name, (passed, msg) in checks:
                self._audit_log(name, passed, {
                    "symbol": order.get("symbol", ""),
                    "notional": order.get("notional", 0),
                    "message": msg,
                })
                if not passed:
                    logger.warning(f"LIVE RISK REJECT [{name}]: {msg}")
                    return False, msg

            return True, "OK"

    # ------------------------------------------------------------------
    # check_all_limits — comprehensive snapshot
    # ------------------------------------------------------------------

    def check_all_limits(
        self,
        portfolio: dict,
        daily_pnl_pct: float = 0.0,
        hourly_pnl_pct: float = 0.0,
        weekly_pnl_pct: float = 0.0,
        trailing_5d_pnl_pct: float = 0.0,
        monthly_pnl_pct: float = 0.0,
        current_dd_pct: float = 0.0,
        margin_used_pct: float = 0.0,
    ) -> dict:
        """Run all risk checks and return a comprehensive status dict.

        Args:
            portfolio: {equity, positions: [...], cash}
            daily_pnl_pct: today's PnL as fraction of equity (negative = loss)
            hourly_pnl_pct: last hour PnL as fraction
            weekly_pnl_pct: this week's PnL as fraction
            trailing_5d_pnl_pct: trailing 5-day PnL as fraction
            monthly_pnl_pct: this month's PnL as fraction
            current_dd_pct: current drawdown from peak (positive = loss)
            margin_used_pct: current margin utilization (0.0-1.0)

        Returns:
            {
                passed: bool,
                checks: [{name, passed, message}],
                blocked_reason: str or None,
                deleveraging: {level, reduction_pct, message},
                actions: [str],
            }
        """
        results = {
            "passed": True,
            "checks": [],
            "blocked_reason": None,
            "deleveraging": {"level": 0, "reduction_pct": 0.0, "message": "OK"},
            "actions": [],
        }

        # --- Circuit breakers ---
        cb_daily = self._check_circuit_breaker_daily(daily_pnl_pct)
        results["checks"].append(cb_daily)
        if not cb_daily["passed"]:
            results["passed"] = False
            results["blocked_reason"] = cb_daily["message"]
            results["actions"].append("STOP_TRADING_TODAY")

        cb_hourly = self._check_circuit_breaker_hourly(hourly_pnl_pct)
        results["checks"].append(cb_hourly)
        if not cb_hourly["passed"]:
            results["passed"] = False
            if results["blocked_reason"] is None:
                results["blocked_reason"] = cb_hourly["message"]
            results["actions"].append("PAUSE_30_MIN")

        cb_weekly = self._check_circuit_breaker_weekly(weekly_pnl_pct)
        results["checks"].append(cb_weekly)
        if not cb_weekly["passed"]:
            # Weekly doesn't fully block, but reduces sizing
            results["actions"].append("REDUCE_SIZING_50")

        # --- Kill switches ---
        ks_5d = self._check_kill_switch_5d(trailing_5d_pnl_pct)
        results["checks"].append(ks_5d)
        if not ks_5d["passed"]:
            results["passed"] = False
            results["blocked_reason"] = ks_5d["message"]
            results["actions"].append("CLOSE_ALL_POSITIONS")

        ks_monthly = self._check_kill_switch_monthly(monthly_pnl_pct)
        results["checks"].append(ks_monthly)
        if not ks_monthly["passed"]:
            results["passed"] = False
            results["blocked_reason"] = ks_monthly["message"]
            results["actions"].append("CLOSE_ALL_REVIEW")

        # --- Margin monitoring ---
        margin_check = self._check_margin_alert(margin_used_pct)
        results["checks"].append(margin_check)
        if not margin_check["passed"]:
            results["actions"].append("MARGIN_ALERT")

        margin_block = self._check_margin_block_level(margin_used_pct)
        results["checks"].append(margin_block)
        if not margin_block["passed"]:
            results["passed"] = False
            if results["blocked_reason"] is None:
                results["blocked_reason"] = margin_block["message"]
            results["actions"].append("BLOCK_NEW_TRADES")

        # --- Deleveraging ---
        delev = self._check_deleveraging(current_dd_pct)
        results["deleveraging"] = delev
        results["checks"].append({
            "name": "deleveraging",
            "passed": delev["level"] == 0,
            "message": delev["message"],
        })
        if delev["level"] > 0:
            results["actions"].append(f"DELEVERAGE_L{delev['level']}")

        # --- Position count ---
        pos_count = self._check_position_count(portfolio)
        results["checks"].append(pos_count)

        # Audit everything
        for check in results["checks"]:
            self._audit_log(
                check["name"],
                check["passed"],
                {"message": check["message"]},
            )

        return results

    # ------------------------------------------------------------------
    # Live-calibrated private check overrides
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_cost(order_or_position: dict) -> float:
        """Return the margin-based cost for FX/FUTURES, or notional for EQUITY.

        FX uses margin_used, FUTURES uses initial_margin, EQUITY uses notional.
        This ensures existing per-position and exposure checks operate on the
        capital actually consumed rather than raw leveraged notional.
        """
        asset_class = order_or_position.get("asset_class", "EQUITY").upper()
        if asset_class == "FX":
            margin = abs(float(order_or_position.get("margin_used", 0)))
            initial = abs(float(order_or_position.get("initial_margin", 0)))
            notional = abs(float(order_or_position.get("notional", 0)))
            if margin == 0 and initial == 0 and notional > 0:
                estimated = notional * 0.03
                logger.warning(
                    "FX position %s: margin_used=0 AND initial_margin=0 but notional=$%.0f "
                    "— using estimated margin $%.0f (3%% of notional)",
                    order_or_position.get("symbol", "?"),
                    notional,
                    estimated,
                )
                return estimated
            if margin == 0:
                logger.warning(
                    "FX order/position missing margin_used field: %s — treated as 0 cost",
                    order_or_position.get("symbol", "?")
                )
            return margin
        elif asset_class == "FUTURES":
            margin = abs(float(order_or_position.get("initial_margin", 0)))
            if margin == 0:
                logger.warning(
                    "FUTURES order/position missing initial_margin field: %s — treated as 0 cost",
                    order_or_position.get("symbol", "?")
                )
            return margin
        else:
            return abs(float(order_or_position.get("notional", 0)))

    def _check_position_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Order effective cost / equity < max_position_pct (live: 15%).

        For FX/FUTURES, effective cost is margin; for EQUITY it is notional.
        """
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("max_position_pct", 0.15)
        order_cost = self._effective_cost(order)
        existing = sum(
            self._effective_cost(p)
            for p in portfolio.get("positions", [])
            if p.get("symbol") == order.get("symbol")
        )
        total = (existing + order_cost) / equity
        if total > limit:
            return False, (
                f"Position limit: {order.get('symbol', '?')} "
                f"total {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_strategy_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of strategy effective costs / equity < max_strategy_pct (live: 20%)."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("max_strategy_pct", 0.20)
        strategy = order.get("strategy", "")
        existing = sum(
            self._effective_cost(p)
            for p in portfolio.get("positions", [])
            if p.get("strategy") == strategy
        )
        order_cost = self._effective_cost(order)
        total = (existing + order_cost) / equity
        if total > limit:
            return False, (
                f"Strategy limit: {strategy} "
                f"total {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_exposure_long(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of long effective costs / equity < max_long_pct (live: 60%)."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("max_long_pct", 0.60)
        current_long = sum(
            self._effective_cost(p)
            for p in portfolio.get("positions", [])
            if p.get("side", "").upper() == "LONG"
        )
        direction = order.get("direction", "").upper()
        addition = self._effective_cost(order) if direction == "LONG" else 0
        total = (current_long + addition) / equity
        if total > limit:
            return False, (
                f"Long exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_exposure_short(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of short effective costs / equity < max_short_pct (live: 40%)."""
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("max_short_pct", 0.40)
        current_short = sum(
            self._effective_cost(p)
            for p in portfolio.get("positions", [])
            if p.get("side", "").upper() == "SHORT"
        )
        direction = order.get("direction", "").upper()
        addition = self._effective_cost(order) if direction == "SHORT" else 0
        total = (current_short + addition) / equity
        if total > limit:
            return False, (
                f"Short exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_gross_exposure(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sum of effective costs / equity < max_gross_pct (live: 120%).

        Uses margin for FX/FUTURES and notional for EQUITY.
        """
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("max_gross_pct", 1.20)
        current_gross = sum(
            self._effective_cost(p)
            for p in portfolio.get("positions", [])
        )
        order_cost = self._effective_cost(order)
        total = (current_gross + order_cost) / equity
        if total > limit:
            return False, (
                f"Gross exposure: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    def _check_cash_reserve(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Cash after order / equity > min_cash_pct (live: 15%).

        Uses effective cost (margin for FX/FUTURES) to determine cash impact.
        """
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.position_limits.get("min_cash_pct", 0.15)
        cash = float(portfolio.get("cash", 0))
        order_cost = self._effective_cost(order)
        remaining_cash_ratio = (cash - order_cost) / equity
        if remaining_cash_ratio < limit:
            return False, (
                f"Cash reserve: {remaining_cash_ratio:.1%} < min {limit:.0%}"
            )
        return True, "OK"

    def _check_sector_limit(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Sector exposure < max_sector_pct (live: 30%).

        Uses effective cost (margin for FX/FUTURES).
        """
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"
        limit = self.sector_limits_cfg.get("max_sector_pct", 0.30)
        symbol = order.get("symbol", "")
        order_sector = self._symbol_to_sector.get(symbol, "other")

        sector_total = 0.0
        for p in portfolio.get("positions", []):
            p_symbol = p.get("symbol", "")
            p_sector = self._symbol_to_sector.get(p_symbol, "other")
            if p_sector == order_sector:
                sector_total += self._effective_cost(p)

        order_cost = self._effective_cost(order)
        total = (sector_total + order_cost) / equity
        if total > limit:
            return False, (
                f"Sector limit [{order_sector}]: {total:.1%} > max {limit:.0%}"
            )
        return True, "OK"

    # ------------------------------------------------------------------
    # Multi-asset exposure helper
    # ------------------------------------------------------------------

    def _get_exposure_by_type(self, portfolio: dict) -> Dict[str, float]:
        """Compute exposure breakdown by asset class.

        Returns:
            {
                "equity_exposure": sum of notional for EQUITY positions,
                "fx_margin": sum of margin_used for FX positions,
                "fx_notional": sum of notional for FX positions,
                "futures_margin": sum of initial_margin for FUTURES positions,
                "total_margin": equity_exposure + fx_margin + futures_margin,
            }
        """
        equity_exposure = 0.0
        fx_margin = 0.0
        fx_notional = 0.0
        futures_margin = 0.0

        for p in portfolio.get("positions", []):
            asset_class = p.get("asset_class", "EQUITY").upper()
            if asset_class == "FX":
                fx_margin += abs(float(p.get("margin_used", 0)))
                fx_notional += abs(float(p.get("notional", 0)))
            elif asset_class == "FUTURES":
                futures_margin += abs(float(p.get("initial_margin", 0)))
            else:
                equity_exposure += abs(float(p.get("notional", 0)))

        return {
            "equity_exposure": equity_exposure,
            "fx_margin": fx_margin,
            "fx_notional": fx_notional,
            "futures_margin": futures_margin,
            "total_margin": equity_exposure + fx_margin + futures_margin,
        }

    # ------------------------------------------------------------------
    # FX / Futures / Combined margin checks
    # ------------------------------------------------------------------

    def _check_fx_exposure(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """For FX orders: check margin < max_fx_margin_pct,
        notional < max_fx_notional_pct, and single pair margin < max_single_pair_margin_pct.
        Non-FX orders pass automatically.
        """
        asset_class = order.get("asset_class", "EQUITY").upper()
        if asset_class != "FX":
            return True, "OK"

        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        exposure = self._get_exposure_by_type(portfolio)
        order_margin = abs(float(order.get("margin_used", 0)))
        order_notional = abs(float(order.get("notional", 0)))

        # Check total FX margin
        max_fx_margin_pct = self.fx_limits_cfg.get("max_fx_margin_pct", 0.40)
        total_fx_margin = (exposure["fx_margin"] + order_margin) / equity
        if total_fx_margin > max_fx_margin_pct:
            return False, (
                f"FX margin limit: {total_fx_margin:.1%} > max {max_fx_margin_pct:.0%}"
            )

        # Check total FX notional
        max_fx_notional_pct = self.fx_limits_cfg.get("max_fx_notional_pct", 15.0)
        total_fx_notional = (exposure["fx_notional"] + order_notional) / equity
        if total_fx_notional > max_fx_notional_pct:
            return False, (
                f"FX notional limit: {total_fx_notional:.1%} > max {max_fx_notional_pct:.0%}"
            )

        # Check single pair notional
        max_single_notional = self.fx_limits_cfg.get("max_single_pair_notional", 40000)
        order_notional_abs = abs(float(order.get("notional", 0)))
        if order.get("asset_class", "").upper() == "FX" and order_notional_abs > max_single_notional:
            return False, f"FX single pair notional: ${order_notional_abs:,.0f} > max ${max_single_notional:,.0f}"

        # Check single pair margin
        max_single_pair_margin_pct = self.fx_limits_cfg.get("max_single_pair_margin_pct", 0.15)
        symbol = order.get("symbol", "")
        existing_pair_margin = sum(
            abs(float(p.get("margin_used", 0)))
            for p in portfolio.get("positions", [])
            if p.get("symbol") == symbol and p.get("asset_class", "").upper() == "FX"
        )
        pair_margin_ratio = (existing_pair_margin + order_margin) / equity
        if pair_margin_ratio > max_single_pair_margin_pct:
            return False, (
                f"FX single pair margin [{symbol}]: {pair_margin_ratio:.1%} "
                f"> max {max_single_pair_margin_pct:.0%}"
            )

        return True, "OK"

    def _check_futures_exposure(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """For FUTURES orders: check margin < max_futures_margin_pct,
        symbol in allowed_contracts, and contracts_per_symbol <= max.
        Non-FUTURES orders pass automatically.
        """
        asset_class = order.get("asset_class", "EQUITY").upper()
        if asset_class != "FUTURES":
            return True, "OK"

        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        symbol = order.get("symbol", "")

        # Check allowed contracts
        allowed = self.futures_limits_cfg.get("allowed_contracts", [])
        if allowed and symbol not in allowed:
            return False, (
                f"Futures contract not allowed: {symbol} "
                f"(allowed: {', '.join(allowed)})"
            )

        exposure = self._get_exposure_by_type(portfolio)
        order_margin = abs(float(order.get("initial_margin", 0)))

        # Check total futures margin
        max_futures_margin_pct = self.futures_limits_cfg.get("max_futures_margin_pct", 0.35)
        total_futures_margin = (exposure["futures_margin"] + order_margin) / equity
        if total_futures_margin > max_futures_margin_pct:
            return False, (
                f"Futures margin limit: {total_futures_margin:.1%} "
                f"> max {max_futures_margin_pct:.0%}"
            )

        # Check single contract type margin
        max_single_margin_pct = self.futures_limits_cfg.get("max_single_contract_margin_pct", 0.20)
        order_margin_abs = abs(float(order.get("initial_margin", 0)))
        if order_margin_abs > equity * max_single_margin_pct:
            return False, f"Futures single contract margin: ${order_margin_abs:,.0f} > max {max_single_margin_pct:.0%} of equity"

        # Check contracts per symbol
        max_per_symbol = self.futures_limits_cfg.get("max_contracts_per_symbol", 2)
        existing_count = sum(
            1 for p in portfolio.get("positions", [])
            if p.get("symbol") == symbol and p.get("asset_class", "").upper() == "FUTURES"
        )
        order_qty = int(order.get("qty", 1))
        if existing_count + order_qty > max_per_symbol:
            return False, (
                f"Futures contracts per symbol [{symbol}]: "
                f"{existing_count + order_qty} > max {max_per_symbol}"
            )

        return True, "OK"

    def _check_combined_margin(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Check total margin (equity_notional + fx_margin + futures_margin)
        < max_total_margin_pct AND cash >= min_cash_pct after the order.
        """
        equity = portfolio.get("equity", 0)
        if equity <= 0:
            return False, "Equity <= 0"

        exposure = self._get_exposure_by_type(portfolio)
        asset_class = order.get("asset_class", "EQUITY").upper()

        # Determine the margin contribution of the new order
        if asset_class == "FX":
            order_margin = abs(float(order.get("margin_used", 0)))
        elif asset_class == "FUTURES":
            order_margin = abs(float(order.get("initial_margin", 0)))
        else:
            order_margin = abs(float(order.get("notional", 0)))

        # Check total margin
        max_total_margin_pct = self.combined_limits_cfg.get("max_total_margin_pct", 0.80)
        total_margin = (exposure["total_margin"] + order_margin) / equity
        if total_margin > max_total_margin_pct:
            return False, (
                f"Combined margin limit: {total_margin:.1%} "
                f"> max {max_total_margin_pct:.0%}"
            )

        # Check minimum cash after order
        min_cash_pct = self.combined_limits_cfg.get("min_cash_pct", 0.20)
        cash = float(portfolio.get("cash", 0))
        remaining_cash_ratio = (cash - order_margin) / equity
        if remaining_cash_ratio < min_cash_pct:
            return False, (
                f"Combined min cash: {remaining_cash_ratio:.1%} "
                f"< min {min_cash_pct:.0%} after order"
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # Live-only checks
    # ------------------------------------------------------------------

    def _check_max_positions(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Enforce max_positions count (live: 6)."""
        max_pos = self.position_limits.get("max_positions", 6)
        existing_symbols = {p.get("symbol") for p in portfolio.get("positions", [])}
        current_count = len(existing_symbols)
        # If the order adds a new symbol, count increases
        symbol = order.get("symbol", "")
        new_count = current_count if symbol in existing_symbols else current_count + 1
        if new_count > max_pos:
            return False, (
                f"Max positions: {new_count} > max {max_pos}"
            )
        return True, "OK"

    def _check_margin_block(
        self, order: dict, portfolio: dict
    ) -> Tuple[bool, str]:
        """Block new trades if margin used > block_margin_pct (85%)."""
        margin_used = float(portfolio.get("margin_used_pct", 0.0))
        block_pct = self.margin_limits.get("block_margin_pct", 0.85)
        if margin_used > block_pct:
            return False, (
                f"Margin block: {margin_used:.1%} > block threshold {block_pct:.0%}"
            )
        return True, "OK"

    # ------------------------------------------------------------------
    # Circuit breakers (live-calibrated)
    # ------------------------------------------------------------------

    def check_circuit_breaker(
        self, daily_pnl_pct: float, hourly_pnl_pct: float = None
    ) -> Tuple[bool, str]:
        """Override parent circuit breaker with live thresholds.

        Args:
            daily_pnl_pct: daily PnL as fraction (negative = loss)
            hourly_pnl_pct: hourly PnL as fraction (optional)

        Returns:
            (triggered: bool, message: str)
        """
        daily_limit = self.circuit_breakers_cfg.get("daily_loss_pct", 0.015)
        hourly_limit = self.circuit_breakers_cfg.get("hourly_loss_pct", 0.01)

        if daily_pnl_pct < -daily_limit:
            msg = (
                f"LIVE CIRCUIT BREAKER DAILY: loss {daily_pnl_pct:.2%} "
                f"exceeds -{daily_limit:.1%} (${abs(daily_pnl_pct) * self.capital:,.0f})"
            )
            logger.critical(msg)
            self._audit_log("circuit_breaker_daily", False, {"message": msg})
            return True, msg

        if hourly_pnl_pct is not None and hourly_pnl_pct < -hourly_limit:
            msg = (
                f"LIVE CIRCUIT BREAKER HOURLY: loss {hourly_pnl_pct:.2%} "
                f"exceeds -{hourly_limit:.1%} (${abs(hourly_pnl_pct) * self.capital:,.0f})"
            )
            logger.warning(msg)
            self._audit_log("circuit_breaker_hourly", False, {"message": msg})
            return True, msg

        self._audit_log("circuit_breaker", True, {"daily": daily_pnl_pct, "hourly": hourly_pnl_pct})
        return False, "OK"

    def _check_circuit_breaker_daily(self, daily_pnl_pct: float) -> dict:
        """Check daily circuit breaker, return structured result."""
        limit = self.circuit_breakers_cfg.get("daily_loss_pct", 0.015)
        triggered = daily_pnl_pct < -limit
        msg = (
            f"Daily loss {daily_pnl_pct:.2%} exceeds -{limit:.1%}"
            if triggered else "OK"
        )
        return {"name": "circuit_breaker_daily", "passed": not triggered, "message": msg}

    def _check_circuit_breaker_hourly(self, hourly_pnl_pct: float) -> dict:
        """Check hourly circuit breaker, return structured result."""
        limit = self.circuit_breakers_cfg.get("hourly_loss_pct", 0.01)
        triggered = hourly_pnl_pct < -limit
        msg = (
            f"Hourly loss {hourly_pnl_pct:.2%} exceeds -{limit:.1%}"
            if triggered else "OK"
        )
        return {"name": "circuit_breaker_hourly", "passed": not triggered, "message": msg}

    def _check_circuit_breaker_weekly(self, weekly_pnl_pct: float) -> dict:
        """Check weekly circuit breaker (reduces sizing, doesn't fully block).

        Threshold: weekly_loss_pct (default 3%).
        """
        limit = self.circuit_breakers_cfg.get("weekly_loss_pct", 0.03)
        triggered = weekly_pnl_pct < -limit
        msg = (
            f"Weekly loss {weekly_pnl_pct:.2%} exceeds -{limit:.1%} — reduce sizing 50%"
            if triggered else "OK"
        )
        return {"name": "circuit_breaker_weekly", "passed": not triggered, "message": msg}

    # ------------------------------------------------------------------
    # Kill switches
    # ------------------------------------------------------------------

    def _check_kill_switch_5d(self, trailing_5d_pnl_pct: float) -> dict:
        """Kill switch: trailing 5-day loss > threshold -> close all."""
        limit = self.kill_switch_cfg.get("trailing_5d_loss_pct", 0.03)
        triggered = trailing_5d_pnl_pct < -limit
        msg = (
            f"KILL SWITCH 5D: trailing loss {trailing_5d_pnl_pct:.2%} "
            f"exceeds -{limit:.1%} — CLOSE ALL"
            if triggered else "OK"
        )
        if triggered:
            logger.critical(msg)
        return {"name": "kill_switch_5d", "passed": not triggered, "message": msg}

    def _check_kill_switch_monthly(self, monthly_pnl_pct: float) -> dict:
        """Kill switch: monthly loss > threshold -> close all, manual review."""
        limit = self.kill_switch_cfg.get("max_monthly_loss_pct", 0.05)
        triggered = monthly_pnl_pct < -limit
        msg = (
            f"KILL SWITCH MONTHLY: loss {monthly_pnl_pct:.2%} "
            f"exceeds -{limit:.1%} — CLOSE ALL + REVIEW"
            if triggered else "OK"
        )
        if triggered:
            logger.critical(msg)
        return {"name": "kill_switch_monthly", "passed": not triggered, "message": msg}

    # ------------------------------------------------------------------
    # Margin monitoring
    # ------------------------------------------------------------------

    def _check_margin_alert(self, margin_used_pct: float) -> dict:
        """Yellow alert when margin > max_margin_used_pct (70%)."""
        limit = self.margin_limits.get("max_margin_used_pct", 0.70)
        triggered = margin_used_pct > limit
        msg = (
            f"MARGIN ALERT: {margin_used_pct:.1%} > yellow threshold {limit:.0%}"
            if triggered else "OK"
        )
        if triggered:
            logger.warning(msg)
        return {"name": "margin_alert", "passed": not triggered, "message": msg}

    def _check_margin_block_level(self, margin_used_pct: float) -> dict:
        """Block new trades when margin > block_margin_pct (85%)."""
        limit = self.margin_limits.get("block_margin_pct", 0.85)
        triggered = margin_used_pct > limit
        msg = (
            f"MARGIN BLOCK: {margin_used_pct:.1%} > block threshold {limit:.0%}"
            if triggered else "OK"
        )
        if triggered:
            logger.critical(msg)
        return {"name": "margin_block", "passed": not triggered, "message": msg}

    # ------------------------------------------------------------------
    # Progressive deleveraging (absolute DD-based, 3 levels)
    # ------------------------------------------------------------------

    def check_progressive_deleveraging(
        self, current_dd_pct: float, max_dd_backtest: float = None
    ) -> Tuple[int, float, str]:
        """Override parent deleveraging with live absolute thresholds.

        Uses config/limits_live.yaml deleveraging section:
          - Level 1: dd > level_1_dd_pct -> reduce by level_1_action
          - Level 2: dd > level_2_dd_pct -> reduce by level_2_action
          - Level 3: dd > level_3_dd_pct -> close all (reduce 100%)

        Args:
            current_dd_pct: current drawdown as fraction (positive = loss)
            max_dd_backtest: ignored (kept for API compat, live uses absolute thresholds)

        Returns:
            (level: int 0-3, reduction_pct: float, message: str)
        """
        dd = abs(current_dd_pct)

        l3_dd = self.deleveraging_cfg.get("level_3_dd_pct", 0.02)
        l3_action = self.deleveraging_cfg.get("level_3_action", 1.00)
        l2_dd = self.deleveraging_cfg.get("level_2_dd_pct", 0.015)
        l2_action = self.deleveraging_cfg.get("level_2_action", 0.50)
        l1_dd = self.deleveraging_cfg.get("level_1_dd_pct", 0.01)
        l1_action = self.deleveraging_cfg.get("level_1_action", 0.30)

        if dd >= l3_dd:
            msg = (
                f"LIVE DELEVERAGING L3: DD {dd:.2%} >= {l3_dd:.1%} "
                f"(${dd * self.capital:,.0f}). Close all positions."
            )
            logger.critical(msg)
            self._audit_log("deleveraging", False, {"level": 3, "dd": dd, "message": msg})
            return 3, l3_action, msg

        if dd >= l2_dd:
            msg = (
                f"LIVE DELEVERAGING L2: DD {dd:.2%} >= {l2_dd:.1%} "
                f"(${dd * self.capital:,.0f}). Reduce {l2_action:.0%} exposure."
            )
            logger.warning(msg)
            self._audit_log("deleveraging", False, {"level": 2, "dd": dd, "message": msg})
            return 2, l2_action, msg

        if dd >= l1_dd:
            msg = (
                f"LIVE DELEVERAGING L1: DD {dd:.2%} >= {l1_dd:.1%} "
                f"(${dd * self.capital:,.0f}). Reduce {l1_action:.0%} exposure."
            )
            logger.warning(msg)
            self._audit_log("deleveraging", False, {"level": 1, "dd": dd, "message": msg})
            return 1, l1_action, msg

        self._audit_log("deleveraging", True, {"level": 0, "dd": dd})
        return 0, 0.0, "OK — drawdown within live limits"

    def _check_deleveraging(self, current_dd_pct: float) -> dict:
        """Structured deleveraging check for check_all_limits()."""
        level, reduction, msg = self.check_progressive_deleveraging(current_dd_pct)
        return {
            "level": level,
            "reduction_pct": reduction,
            "message": msg,
        }

    # ------------------------------------------------------------------
    # Position count check (for check_all_limits)
    # ------------------------------------------------------------------

    def _check_position_count(self, portfolio: dict) -> dict:
        """Check current position count vs max_positions."""
        max_pos = self.position_limits.get("max_positions", 6)
        current = len(portfolio.get("positions", []))
        passed = current <= max_pos
        msg = f"Positions: {current}/{max_pos}" if passed else (
            f"Too many positions: {current} > max {max_pos}"
        )
        return {"name": "position_count", "passed": passed, "message": msg}

    # ------------------------------------------------------------------
    # PDT (Pattern Day Trader) risk check
    # ------------------------------------------------------------------

    def check_pdt_risk(self, recent_trades: list, equity: float) -> dict:
        """Check Pattern Day Trader risk for US equity intraday trades.

        PDT rule: 4+ day trades in 5 business days with equity < $25K.
        FX and futures are EXEMPT from PDT.

        Args:
            recent_trades: list of recent trade dicts with {asset_class, open_date, close_date}
            equity: current equity

        Returns:
            {at_risk: bool, day_trades_5d: int, remaining: int, message: str}
        """
        if equity >= 25000:
            return {"at_risk": False, "day_trades_5d": 0, "remaining": 999,
                    "message": "Equity >= $25K — PDT not applicable"}

        from datetime import datetime as _dt, timedelta, timezone as _tz
        cutoff = _dt.now(_tz.utc) - timedelta(days=7)  # ~5 business days

        day_trades = 0
        for trade in recent_trades:
            # Only count US equity round-trips (FX and futures exempt)
            if trade.get("asset_class", "").upper() not in ("EQUITY",):
                continue
            open_date = trade.get("open_date", "")
            close_date = trade.get("close_date", "")
            if open_date and close_date:
                # Same day = day trade
                if str(open_date)[:10] == str(close_date)[:10]:
                    if _dt.fromisoformat(str(close_date)) > cutoff:
                        day_trades += 1

        remaining = max(0, 3 - day_trades)  # 3 allowed, 4th triggers PDT
        at_risk = day_trades >= 3

        result = {
            "at_risk": at_risk,
            "day_trades_5d": day_trades,
            "remaining": remaining,
            "message": f"PDT: {day_trades}/3 day trades in 5 days" +
                       (" — NEXT TRADE TRIGGERS PDT!" if at_risk else f" — {remaining} remaining"),
        }

        if at_risk:
            logger.warning(f"PDT WARNING: {result['message']}")
            self._audit_log("pdt_check", False, result)

        return result
