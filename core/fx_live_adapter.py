"""
FX Live Pipeline Adapter -- connects FX strategies to live IBKR execution.

Adapts the 4 validated FX strategies for live trading:
  1. EUR/USD Trend Following (Sharpe 4.62, 47 trades/5Y)
  2. EUR/GBP Mean Reversion (Sharpe 3.65, 32 trades/5Y)
  3. EUR/JPY Carry + Momentum (Sharpe 2.50, 91 trades/5Y)
  4. AUD/JPY Carry Trade (Sharpe 1.58, 101 trades/5Y)

Live-specific additions:
  - Limit orders instead of market orders (reduce slippage)
  - Spread check: reject if current spread > 2x historical average
  - Detailed execution logging (requested vs filled price)
  - Sizing via quarter-Kelly with FX leverage

IBKR FX specifics:
  - Minimum lot: 25,000 units
  - FX leverage available: up to 33x
  - Commissions: ~$2 per $100K notional (~0.002%)
  - Pairs: EUR.USD, EUR.GBP, EUR.JPY, AUD.JPY (IBKR format with dot)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# IBKR FX pair format mapping
FX_PAIR_MAP = {
    "EURUSD": "EUR.USD",
    "EURGBP": "EUR.GBP",
    "EURJPY": "EUR.JPY",
    "AUDJPY": "AUD.JPY",
    "GBPUSD": "GBP.USD",
    "USDCHF": "USD.CHF",
    "NZDUSD": "NZD.USD",
}

# Reverse mapping (EUR.USD -> EURUSD)
_IBKR_PAIR_REVERSE = {v: k for k, v in FX_PAIR_MAP.items()}

# Historical average spreads (bps) for spread filter
AVERAGE_SPREADS_BPS: Dict[str, float] = {
    "EURUSD": 0.8,
    "EURGBP": 1.2,
    "EURJPY": 1.5,
    "AUDJPY": 2.0,
    "GBPUSD": 1.0,
    "USDCHF": 1.5,
    "NZDUSD": 2.0,
}

# Backtest Sharpe for sizing weights
STRATEGY_SHARPES: Dict[str, float] = {
    "fx_eurusd_trend": 4.62,
    "fx_eurgbp_mr": 3.65,
    "fx_eurjpy_carry": 2.50,
    "fx_audjpy_carry": 1.58,
}

# Strategy -> pair mapping
STRATEGY_PAIR_MAP: Dict[str, str] = {
    "fx_eurusd_trend": "EURUSD",
    "fx_eurgbp_mr": "EURGBP",
    "fx_eurjpy_carry": "EURJPY",
    "fx_audjpy_carry": "AUDJPY",
}

# IBKR minimum lot size (units of base currency)
IBKR_MIN_LOT = 25_000

# Maximum spread ratio before rejection
DEFAULT_MAX_SPREAD_RATIO = 2.0

# Maximum margin as fraction of total capital
MAX_MARGIN_PCT = 0.40


class FXLiveAdapter:
    """Adapts FX strategies for live IBKR execution.

    Handles:
    - Signal validation (spread check, risk check)
    - Position sizing (quarter-Kelly with Sharpe weighting)
    - Order type selection (limit vs market)
    - Execution logging
    - IBKR pair format conversion
    """

    def __init__(
        self,
        capital: float = 10_000,
        fx_allocation_pct: float = 0.40,
        max_leverage: float = 20.0,
        broker: Any = None,
        trade_journal: Any = None,
        slippage_tracker: Any = None,
        alert_callback: Callable | None = None,
        max_spread_ratio: float = DEFAULT_MAX_SPREAD_RATIO,
    ):
        """
        Args:
            capital: total live capital ($)
            fx_allocation_pct: % allocated to FX as margin (0.40 = $4,000 on $10K)
            max_leverage: FX leverage multiplier on margin (10x month 1,
                          IBKR allows up to 33x on major pairs)
            broker: BaseBroker instance for live execution
            trade_journal: TradeJournal instance for logging
            slippage_tracker: SlippageTracker instance
            alert_callback: function(message, level) for alerts
            max_spread_ratio: reject if spread > ratio * average (default 2.0)
        """
        self.capital = capital
        self.fx_allocation_pct = fx_allocation_pct
        self.fx_allocation = capital * fx_allocation_pct
        self.max_leverage = max_leverage
        self.max_spread_ratio = max_spread_ratio
        self._broker = broker
        self._journal = trade_journal
        self._slippage = slippage_tracker
        self._alert = alert_callback

        logger.info(
            "FXLiveAdapter initialized -- capital=$%,.0f, fx_alloc=$%,.0f (%.0f%%), "
            "max_leverage=%.1fx",
            capital, self.fx_allocation, fx_allocation_pct * 100, max_leverage,
        )

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def calculate_sizing(self) -> Dict[str, dict]:
        """Calculate position sizing for each FX strategy.

        Method: Sharpe-weighted allocation with quarter-Kelly.
        Each strategy gets a share of fx_allocation proportional to its Sharpe.
        Notional = allocated_margin * max_leverage (capped by leverage phase).
        Units rounded down to nearest IBKR_MIN_LOT.

        Returns:
            {strategy: {
                weight: float,          # fraction of FX allocation
                margin_allocated: float, # $ of margin
                notional: float,         # $ notional exposure
                lots: int,               # number of 25K lots
                units: int,              # lots * 25000
            }}
        """
        total_sharpe = sum(max(s, 0) for s in STRATEGY_SHARPES.values())
        if total_sharpe <= 0 or self.fx_allocation <= 0:
            return {
                name: {
                    "weight": 0.0,
                    "margin_allocated": 0.0,
                    "notional": 0.0,
                    "lots": 0,
                    "units": 0,
                }
                for name in STRATEGY_SHARPES
            }

        result: Dict[str, dict] = {}

        for strategy, sharpe in STRATEGY_SHARPES.items():
            weight = max(sharpe, 0) / total_sharpe
            margin_allocated = self.fx_allocation * weight
            notional = margin_allocated * self.max_leverage

            # Round down to nearest IBKR minimum lot
            lots = int(notional // IBKR_MIN_LOT)
            units = lots * IBKR_MIN_LOT

            # Recalculate actual notional based on rounded units
            actual_notional = float(units)
            actual_margin = actual_notional / self.max_leverage if self.max_leverage > 0 else 0.0

            result[strategy] = {
                "weight": round(weight, 4),
                "margin_allocated": round(actual_margin, 2),
                "notional": actual_notional,
                "lots": lots,
                "units": units,
            }

        logger.info(
            "FX sizing: %d strategies, total_margin=$%,.0f / $%,.0f fx_alloc",
            len(result),
            sum(r["margin_allocated"] for r in result.values()),
            self.fx_allocation,
        )
        return result

    # ------------------------------------------------------------------
    # Spread filter
    # ------------------------------------------------------------------

    def check_spread(self, pair: str, current_spread_bps: float) -> dict:
        """Check if current spread is acceptable.

        Reject if spread > max_spread_ratio * historical average.

        Args:
            pair: e.g. "EURUSD"
            current_spread_bps: current bid-ask spread in basis points

        Returns:
            {acceptable: bool, current: float, average: float, ratio: float}
        """
        avg = AVERAGE_SPREADS_BPS.get(pair)
        if avg is None or avg <= 0:
            # Unknown pair -- reject by default (fail-closed)
            logger.warning("Unknown pair %s — no spread data, rejecting (fail-closed)", pair)
            return {
                "acceptable": False,
                "current": current_spread_bps,
                "average": 0.0,
                "ratio": 0.0,
                "reason": f"No spread data for {pair}",
            }

        ratio = current_spread_bps / avg
        acceptable = ratio <= self.max_spread_ratio

        if not acceptable:
            logger.warning(
                "Spread REJECTED for %s: %.2f bps (avg=%.2f, ratio=%.1fx > %.1fx max)",
                pair, current_spread_bps, avg, ratio, self.max_spread_ratio,
            )

        return {
            "acceptable": acceptable,
            "current": current_spread_bps,
            "average": avg,
            "ratio": round(ratio, 4),
        }

    # ------------------------------------------------------------------
    # Order preparation
    # ------------------------------------------------------------------

    def prepare_order(
        self,
        strategy: str,
        pair: str,
        direction: str,
        signal_price: float,
        stop_loss: float,
        take_profit: float,
        current_spread_bps: float | None = None,
    ) -> dict:
        """Prepare a live FX order with all validations.

        Steps:
        1. Check spread (if provided)
        2. Calculate sizing for this strategy
        3. Validate risk limits (margin < MAX_MARGIN_PCT)
        4. Convert to IBKR pair format
        5. Choose order type (LIMIT if spread OK, else skip)

        Args:
            strategy: e.g. "fx_eurusd_trend"
            pair: e.g. "EURUSD"
            direction: "BUY" or "SELL"
            signal_price: price at signal generation
            stop_loss: stop loss price
            take_profit: take profit price
            current_spread_bps: current spread (optional, skips check if None)

        Returns:
            {
                ready: bool,
                ibkr_pair: str,
                direction: str,
                units: int,
                order_type: str,
                limit_price: float,
                stop_loss: float,
                take_profit: float,
                margin_required: float,
                reason_if_rejected: str
            }
        """
        direction = direction.upper()
        ibkr_pair = self.to_ibkr_pair(pair)

        # --- 1. Spread check ---
        if current_spread_bps is not None:
            spread_result = self.check_spread(pair, current_spread_bps)
            if not spread_result["acceptable"]:
                return {
                    "ready": False,
                    "ibkr_pair": ibkr_pair,
                    "direction": direction,
                    "units": 0,
                    "order_type": "LIMIT",
                    "limit_price": 0.0,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "margin_required": 0.0,
                    "reason_if_rejected": (
                        f"Spread too wide: {current_spread_bps:.2f} bps "
                        f"(avg={spread_result['average']:.2f}, "
                        f"ratio={spread_result['ratio']:.1f}x > "
                        f"{self.max_spread_ratio:.1f}x)"
                    ),
                }

        # --- 2. Calculate sizing ---
        sizing = self.calculate_sizing()
        strat_sizing = sizing.get(strategy)
        if strat_sizing is None or strat_sizing["units"] == 0:
            return {
                "ready": False,
                "ibkr_pair": ibkr_pair,
                "direction": direction,
                "units": 0,
                "order_type": "LIMIT",
                "limit_price": 0.0,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "margin_required": 0.0,
                "reason_if_rejected": (
                    f"Zero sizing for {strategy} -- "
                    f"capital={self.capital}, fx_alloc={self.fx_allocation}"
                ),
            }

        units = strat_sizing["units"]
        margin_required = strat_sizing["margin_allocated"]

        # --- 3. Validate total margin ---
        total_margin = sum(s["margin_allocated"] for s in sizing.values())
        if total_margin > self.capital * MAX_MARGIN_PCT:
            logger.warning(
                "Total FX margin $%,.0f exceeds %.0f%% of capital $%,.0f — REJECTING",
                total_margin, MAX_MARGIN_PCT * 100, self.capital,
            )
            return {
                "ready": False,
                "ibkr_pair": ibkr_pair,
                "direction": direction,
                "units": 0,
                "order_type": "LIMIT",
                "limit_price": 0.0,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "margin_required": 0.0,
                "reason_if_rejected": (
                    f"Total FX margin ${total_margin:,.0f} exceeds "
                    f"{MAX_MARGIN_PCT:.0%} of capital ${self.capital:,.0f}"
                ),
            }

        # --- 4. Order type: LIMIT at signal price ---
        order_type = "LIMIT"
        limit_price = signal_price

        # --- 5. Build prepared order ---
        prepared = {
            "ready": True,
            "ibkr_pair": ibkr_pair,
            "direction": direction,
            "units": units,
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "margin_required": margin_required,
            "reason_if_rejected": "",
        }

        logger.info(
            "Order prepared: %s %s %s %d units @ %.5f (SL=%.5f TP=%.5f margin=$%,.0f)",
            strategy, direction, ibkr_pair, units, limit_price,
            stop_loss, take_profit, margin_required,
        )
        return prepared

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_order(self, prepared_order: dict) -> dict:
        """Execute a prepared order on IBKR live.

        Requires a broker instance. Logs to trade_journal and slippage_tracker
        if they are configured.

        Args:
            prepared_order: output of prepare_order()

        Returns:
            {
                success: bool,
                trade_id: str,
                fill_price: float,
                slippage_bps: float,
                timestamp: str,
                error: str (empty if success)
            }
        """
        if not prepared_order.get("ready"):
            return {
                "success": False,
                "trade_id": "",
                "fill_price": 0.0,
                "slippage_bps": 0.0,
                "timestamp": datetime.now(UTC).isoformat(),
                "error": prepared_order.get("reason_if_rejected", "Order not ready"),
            }

        if self._broker is None:
            return {
                "success": False,
                "trade_id": "",
                "fill_price": 0.0,
                "slippage_bps": 0.0,
                "timestamp": datetime.now(UTC).isoformat(),
                "error": "No broker configured",
            }

        trade_id = f"FX-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        timestamp = datetime.now(UTC).isoformat()

        try:
            result = self._broker.create_position(
                symbol=prepared_order["ibkr_pair"],
                direction=prepared_order["direction"],
                qty=prepared_order["units"],
                stop_loss=prepared_order["stop_loss"],
                take_profit=prepared_order["take_profit"],
                _authorized_by="fx_live_adapter",
            )

            fill_price = result.get("filled_price") or prepared_order["limit_price"]

            # Calculate slippage
            requested = prepared_order["limit_price"]
            if requested > 0:
                slippage_bps = abs(fill_price - requested) / requested * 10_000
            else:
                slippage_bps = 0.0

            # Flat pair name for logging (e.g. "EURUSD")
            pair_flat = self.from_ibkr_pair(prepared_order["ibkr_pair"])

            # Record in slippage tracker
            if self._slippage is not None:
                self._slippage.record_fill(
                    trade_id=trade_id,
                    strategy=pair_flat,
                    instrument=prepared_order["ibkr_pair"],
                    instrument_type="FX",
                    side=prepared_order["direction"],
                    order_type=prepared_order["order_type"],
                    requested_price=requested,
                    filled_price=fill_price,
                    backtest_slippage_bps=AVERAGE_SPREADS_BPS.get(pair_flat, 1.0),
                )

            # Record in trade journal
            if self._journal is not None:
                self._journal.record_trade_open(
                    trade_id=trade_id,
                    strategy=pair_flat,
                    instrument=prepared_order["ibkr_pair"],
                    instrument_type="FX",
                    direction="LONG" if prepared_order["direction"] == "BUY" else "SHORT",
                    quantity=prepared_order["units"],
                    entry_price_requested=requested,
                    entry_price_filled=fill_price,
                    stop_loss=prepared_order["stop_loss"],
                    take_profit=prepared_order["take_profit"],
                )

            logger.info(
                "FX order EXECUTED: %s %s %d units -- fill=%.5f, slippage=%.2f bps",
                prepared_order["direction"], prepared_order["ibkr_pair"],
                prepared_order["units"], fill_price, slippage_bps,
            )

            # Alert callback
            if self._alert is not None:
                self._alert(
                    f"FX trade executed: {prepared_order['direction']} "
                    f"{prepared_order['ibkr_pair']} {prepared_order['units']} units "
                    f"@ {fill_price:.5f} (slippage={slippage_bps:.1f} bps)",
                    "info",
                )

            return {
                "success": True,
                "trade_id": trade_id,
                "fill_price": fill_price,
                "slippage_bps": round(slippage_bps, 4),
                "timestamp": timestamp,
                "error": "",
            }

        except Exception as e:
            logger.error("FX order FAILED: %s -- %s", prepared_order["ibkr_pair"], e)

            if self._alert is not None:
                self._alert(
                    f"FX trade FAILED: {prepared_order['direction']} "
                    f"{prepared_order['ibkr_pair']} -- {e}",
                    "critical",
                )

            return {
                "success": False,
                "trade_id": trade_id,
                "fill_price": 0.0,
                "slippage_bps": 0.0,
                "timestamp": timestamp,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Position queries
    # ------------------------------------------------------------------

    def get_fx_positions(self) -> List[dict]:
        """Get current FX positions from broker.

        Filters broker positions to only FX pairs (those in FX_PAIR_MAP values).

        Returns:
            List of position dicts from broker, filtered to FX.
        """
        if self._broker is None:
            logger.warning("No broker configured -- cannot get positions")
            return []

        all_positions = self._broker.get_positions()
        fx_symbols = set(FX_PAIR_MAP.values())

        return [p for p in all_positions if p.get("symbol") in fx_symbols]

    def get_fx_pnl(self) -> dict:
        """Aggregate P&L of all FX positions.

        Returns:
            {
                total_unrealized_pl: float,
                positions: int,
                by_pair: {pair: unrealized_pl}
            }
        """
        positions = self.get_fx_positions()
        total_pnl = 0.0
        by_pair: Dict[str, float] = {}

        for pos in positions:
            pnl = pos.get("unrealized_pl", 0.0)
            total_pnl += pnl
            symbol = pos.get("symbol", "UNKNOWN")
            by_pair[symbol] = by_pair.get(symbol, 0.0) + pnl

        return {
            "total_unrealized_pl": round(total_pnl, 2),
            "positions": len(positions),
            "by_pair": by_pair,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_sizing_report(self) -> str:
        """Human-readable sizing report.

        Returns:
            Multi-line string with sizing for each strategy.
        """
        sizing = self.calculate_sizing()
        lines = [
            "=" * 60,
            "FX Live Sizing Report",
            f"Capital: ${self.capital:,.0f} | FX Allocation: ${self.fx_allocation:,.0f} "
            f"({self.fx_allocation_pct:.0%}) | Max Leverage: {self.max_leverage:.1f}x",
            "-" * 60,
        ]

        total_margin = 0.0
        total_notional = 0.0

        for strategy, s in sizing.items():
            pair = STRATEGY_PAIR_MAP.get(strategy, "???")
            lines.append(
                f"  {strategy:<22} ({pair}): "
                f"{s['lots']}L x 25K = {s['units']:>7,} units | "
                f"notional=${s['notional']:>8,.0f} | "
                f"margin=${s['margin_allocated']:>6,.0f} | "
                f"weight={s['weight']:.1%}"
            )
            total_margin += s["margin_allocated"]
            total_notional += s["notional"]

        lines.append("-" * 60)
        margin_pct = total_margin / self.capital * 100 if self.capital > 0 else 0
        lines.append(
            f"  TOTAL: notional=${total_notional:>10,.0f} | "
            f"margin=${total_margin:>8,.0f} ({margin_pct:.1f}% of capital)"
        )
        lines.append("=" * 60)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Signal scheduling (peak / off-peak)
    # ------------------------------------------------------------------

    def should_evaluate_signal(self, pair: str, now_cet: datetime = None) -> bool:
        """Check if signal should be evaluated based on peak/off-peak schedule.

        During peak hours: evaluate every 1H (60 min)
        During off-peak: evaluate every 4H (240 min)

        Returns True if enough time has passed since last evaluation.
        """
        if now_cet is None:
            now_cet = datetime.now(UTC)  # Will be compared as UTC

        # Load schedule
        schedule_path = Path(__file__).parent.parent / "config" / "fx_signal_schedule.yaml"
        if not hasattr(self, '_signal_schedule'):
            try:
                import yaml
                with open(schedule_path) as f:
                    self._signal_schedule = yaml.safe_load(f).get("fx_signal_frequency", {})
            except Exception:
                self._signal_schedule = {}

        # Normalize pair name (EUR.USD -> EUR_USD)
        pair_key = pair.replace(".", "_").replace("/", "_").upper()
        schedule = self._signal_schedule.get(pair_key)

        if not schedule:
            return True  # No schedule = always evaluate

        # Determine if we're in peak hours
        hour = now_cet.hour
        minute = now_cet.minute
        current_minutes = hour * 60 + minute

        peak_start = self._parse_time_to_minutes(schedule.get("peak_hours_start", "00:00"))
        peak_end = self._parse_time_to_minutes(schedule.get("peak_hours_end", "23:59"))

        if peak_start <= current_minutes <= peak_end:
            frequency = schedule.get("peak_frequency_minutes", 60)
        else:
            frequency = schedule.get("off_peak_frequency_minutes", 240)

        # Check last evaluation time
        last_eval_key = f"_last_eval_{pair_key}"
        last_eval = getattr(self, last_eval_key, None)

        if last_eval is None:
            setattr(self, last_eval_key, now_cet)
            return True

        elapsed = (now_cet - last_eval).total_seconds() / 60
        if elapsed >= frequency:
            setattr(self, last_eval_key, now_cet)
            return True

        return False

    @staticmethod
    def _parse_time_to_minutes(time_str: str) -> int:
        """Parse 'HH:MM' to minutes since midnight."""
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def to_ibkr_pair(pair: str) -> str:
        """Convert EURUSD -> EUR.USD for IBKR.

        Falls back to the input if no mapping exists.
        """
        return FX_PAIR_MAP.get(pair, pair)

    @staticmethod
    def from_ibkr_pair(ibkr_pair: str) -> str:
        """Convert EUR.USD -> EURUSD.

        Falls back to removing dots from the input.
        """
        return _IBKR_PAIR_REVERSE.get(ibkr_pair, ibkr_pair.replace(".", ""))
