"""SlippageAnalytics -- advanced slippage analytics on top of SlippageTracker.

Reads from the same SQLite database (data/execution_metrics.db) without
modifying the existing SlippageTracker code.  Provides:
  - Per-strategy slippage analysis with recommendations
  - Time-of-day slippage profiling
  - Instrument-type comparison (EQUITY, FX, CRYPTO, FUTURES)
  - Total dollar cost of slippage
  - Order-type recommendation engine
  - HFT feeding detection
  - Telegram-friendly consolidated report
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "execution_metrics.db"

# ----- Thresholds -----
# If avg slippage > this multiple of avg spread, recommend LIMIT
SPREAD_MULT_LIMIT_THRESHOLD = 2.0
# Strategies spending more than this % of PnL on slippage get flagged
COST_VS_PROFIT_WARN_PCT = 10.0
# P95 slippage above this triggers REDUCE_SIZE
P95_REDUCE_SIZE_BPS = 10.0
# Minimum trades to make a recommendation
MIN_TRADES_FOR_RECOMMENDATION = 5

# Instrument type classification by ticker patterns
_INSTRUMENT_TYPE_MAP = {
    "FX": {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "USD/CAD",
            "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY", "EUR/CHF", "AUD/JPY"},
    "CRYPTO": {"BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC",
               "ADAUSDC", "DOGEUSDC", "AVAXUSDC", "DOTUSDC", "MATICUSDC",
               "BTCUSDT", "ETHUSDT"},
    "FUTURES": {"ES", "NQ", "YM", "RTY", "CL", "GC", "SI", "ZB", "ZN"},
}


def _classify_instrument(instrument: str, instrument_type: str) -> str:
    """Return normalized instrument type from DB column or ticker heuristics."""
    it = instrument_type.upper() if instrument_type else ""
    if it in ("EQUITY", "FX", "CRYPTO", "FUTURES"):
        return it
    # Fallback heuristic
    for itype, tickers in _INSTRUMENT_TYPE_MAP.items():
        if instrument.upper() in tickers:
            return itype
    return "EQUITY"


class SlippageAnalytics:
    """Advanced slippage analytics that reads from the existing SlippageTracker DB.

    Does NOT write to the database -- purely analytical.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        if not self.db_path.exists():
            logger.warning("SlippageAnalytics: DB not found at %s", self.db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _cutoff_iso(self, lookback_days: int) -> str:
        return (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()

    def _fetch_trades(self, lookback_days: int, extra_where: str = "",
                      params: tuple = ()) -> List[sqlite3.Row]:
        cutoff = self._cutoff_iso(lookback_days)
        query = (
            "SELECT * FROM slippage_log "
            f"WHERE timestamp >= ? {extra_where} "
            "ORDER BY timestamp DESC"
        )
        conn = self._get_conn()
        try:
            return conn.execute(query, (cutoff,) + params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _stats_from_bps(bps_values: List[float]) -> Dict[str, Any]:
        """Compute standard statistics from a list of bps values."""
        if not bps_values:
            return {
                "avg_slippage_bps": 0.0,
                "median_slippage_bps": 0.0,
                "p95_slippage_bps": 0.0,
                "std_slippage_bps": 0.0,
                "n_trades": 0,
            }
        sorted_vals = sorted(bps_values)
        n = len(sorted_vals)
        p95_idx = min(int(n * 0.95), n - 1)
        return {
            "avg_slippage_bps": round(statistics.mean(sorted_vals), 4),
            "median_slippage_bps": round(statistics.median(sorted_vals), 4),
            "p95_slippage_bps": round(sorted_vals[p95_idx], 4),
            "std_slippage_bps": round(statistics.stdev(sorted_vals), 4) if n > 1 else 0.0,
            "n_trades": n,
        }

    # ------------------------------------------------------------------
    # 1. Analyze by strategy
    # ------------------------------------------------------------------
    def analyze_by_strategy(self, strategy: str, lookback_days: int = 30) -> Dict[str, Any]:
        """Per-strategy slippage analysis with actionable recommendation.

        Returns:
            {avg_slippage_bps, median_slippage_bps, p95_slippage_bps, n_trades,
             cost_vs_profit_pct, recommendation: "OK"|"SWITCH_TO_LIMIT"|"REDUCE_SIZE"}
        """
        trades = self._fetch_trades(
            lookback_days,
            extra_where="AND strategy = ?",
            params=(strategy,),
        )
        bps_values = [float(t["slippage_bps"]) for t in trades]
        stats = self._stats_from_bps(bps_values)

        # Estimate cost vs profit (use backtest slippage as proxy for expected cost)
        total_adverse = sum(b for b in bps_values if b > 0)
        total_backtest = sum(float(t["backtest_slippage_bps"]) for t in trades)
        cost_vs_profit_pct = (
            round(total_adverse / total_backtest * 100, 2)
            if total_backtest > 0 else 0.0
        )

        # Recommendation logic
        recommendation = "OK"
        n = stats["n_trades"]
        if n >= MIN_TRADES_FOR_RECOMMENDATION:
            if stats["p95_slippage_bps"] > P95_REDUCE_SIZE_BPS:
                recommendation = "REDUCE_SIZE"
            elif stats["avg_slippage_bps"] > 3.0:
                # 3 bps average is high -- consider limit orders
                recommendation = "SWITCH_TO_LIMIT"
            elif (cost_vs_profit_pct > COST_VS_PROFIT_WARN_PCT
                  and stats["avg_slippage_bps"] > 1.5):
                # Only flag cost concern when absolute slippage is meaningful
                recommendation = "SWITCH_TO_LIMIT"

        stats["cost_vs_profit_pct"] = cost_vs_profit_pct
        stats["recommendation"] = recommendation
        return stats

    # ------------------------------------------------------------------
    # 2. Analyze by time of day
    # ------------------------------------------------------------------
    def analyze_by_time_of_day(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Group slippage by hour of day (UTC). Identify worst and best hours.

        Returns:
            {by_hour: {0: {avg_bps, n_trades}, ..., 23: ...},
             worst_hour, best_hour}
        """
        trades = self._fetch_trades(lookback_days)

        by_hour: Dict[int, List[float]] = {h: [] for h in range(24)}
        for t in trades:
            try:
                ts = t["timestamp"]
                # Handle ISO format with or without timezone info
                if "T" in ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                by_hour[dt.hour].append(float(t["slippage_bps"]))
            except (ValueError, TypeError):
                continue

        result_by_hour = {}
        for hour, values in by_hour.items():
            if values:
                result_by_hour[hour] = {
                    "avg_bps": round(statistics.mean(values), 4),
                    "n_trades": len(values),
                }
            else:
                result_by_hour[hour] = {"avg_bps": 0.0, "n_trades": 0}

        # Find worst and best hours (only among hours with trades)
        hours_with_trades = {h: d for h, d in result_by_hour.items() if d["n_trades"] > 0}
        if hours_with_trades:
            worst_hour = max(hours_with_trades, key=lambda h: hours_with_trades[h]["avg_bps"])
            best_hour = min(hours_with_trades, key=lambda h: hours_with_trades[h]["avg_bps"])
        else:
            worst_hour = None
            best_hour = None

        return {
            "by_hour": result_by_hour,
            "worst_hour": worst_hour,
            "best_hour": best_hour,
        }

    # ------------------------------------------------------------------
    # 3. Analyze by instrument type
    # ------------------------------------------------------------------
    def analyze_by_instrument_type(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Compare slippage across EQUITY, FX, CRYPTO, FUTURES.

        Returns:
            {by_type: {EQUITY: {avg_slippage_bps, median, p95, n_trades}, ...}}
        """
        trades = self._fetch_trades(lookback_days)

        by_type: Dict[str, List[float]] = {}
        for t in trades:
            itype = _classify_instrument(t["instrument"], t["instrument_type"])
            by_type.setdefault(itype, []).append(float(t["slippage_bps"]))

        result = {}
        for itype, values in by_type.items():
            result[itype] = self._stats_from_bps(values)

        return {"by_type": result}

    # ------------------------------------------------------------------
    # 4. Compute total slippage cost
    # ------------------------------------------------------------------
    def compute_total_slippage_cost(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Total dollar cost of slippage over the period.

        Only counts adverse slippage (positive bps).
        Per-unit cost = slippage_bps / 10_000 * requested_price
        (volume_at_fill used as quantity if available, else 1)

        Returns:
            {total_cost_usd, avg_per_trade_usd, pct_of_pnl, by_strategy: {...}}
        """
        trades = self._fetch_trades(lookback_days)

        total_cost = 0.0
        by_strategy: Dict[str, float] = {}
        n_adverse = 0

        for t in trades:
            bps = float(t["slippage_bps"])
            if bps <= 0:
                continue
            price = float(t["requested_price"])
            qty = float(t["volume_at_fill"]) if t["volume_at_fill"] is not None else 1.0
            cost = bps / 10_000 * price * qty
            total_cost += cost
            n_adverse += 1

            strat = t["strategy"]
            by_strategy[strat] = by_strategy.get(strat, 0.0) + cost

        # Round strategy costs
        by_strategy = {k: round(v, 2) for k, v in by_strategy.items()}

        avg_per_trade = round(total_cost / n_adverse, 4) if n_adverse > 0 else 0.0

        # Estimate PnL from backtest assumptions (rough proxy)
        # We use total_backtest_bps * price as a floor for expected cost;
        # pct_of_pnl = actual_cost / expected_cost * 100
        total_expected = 0.0
        for t in trades:
            bt_bps = float(t["backtest_slippage_bps"])
            price = float(t["requested_price"])
            qty = float(t["volume_at_fill"]) if t["volume_at_fill"] is not None else 1.0
            total_expected += bt_bps / 10_000 * price * qty

        pct_of_pnl = round(total_cost / total_expected * 100, 2) if total_expected > 0 else 0.0

        return {
            "total_cost_usd": round(total_cost, 2),
            "avg_per_trade_usd": avg_per_trade,
            "pct_of_pnl": pct_of_pnl,
            "n_adverse_trades": n_adverse,
            "by_strategy": by_strategy,
        }

    # ------------------------------------------------------------------
    # 5. Recommend order type
    # ------------------------------------------------------------------
    def recommend_order_type(self, ticker: str, side: str,
                             urgency: str = "NORMAL") -> Dict[str, Any]:
        """Recommend order type based on historical slippage for this ticker.

        Logic:
          - If avg_slippage > 2x avg_spread -> LIMIT (pegged-to-mid)
          - If ticker is highly liquid AND urgency is HIGH -> MARKET
          - Otherwise -> LIMIT with offset
          - Fallback when no data: LIMIT for NORMAL, MARKET for HIGH

        Returns:
            {order_type, limit_offset_bps, reason}
        """
        # Fetch all historical trades for this ticker (no lookback limit for ticker-specific)
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT slippage_bps, market_spread_bps, volume_at_fill "
                "FROM slippage_log WHERE instrument = ?",
                (ticker,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            # No historical data -- conservative default
            if urgency.upper() == "HIGH":
                return {
                    "order_type": "MARKET",
                    "limit_offset_bps": 0.0,
                    "reason": f"No historical data for {ticker}; MARKET due to HIGH urgency",
                }
            return {
                "order_type": "LIMIT",
                "limit_offset_bps": 2.0,
                "reason": f"No historical data for {ticker}; defaulting to LIMIT with 2 bps offset",
            }

        bps_values = [float(r["slippage_bps"]) for r in rows]
        avg_slippage = statistics.mean(bps_values)

        spread_values = [float(r["market_spread_bps"]) for r in rows
                         if r["market_spread_bps"] is not None and r["market_spread_bps"] > 0]
        avg_spread = statistics.mean(spread_values) if spread_values else 0.0

        volume_values = [float(r["volume_at_fill"]) for r in rows
                         if r["volume_at_fill"] is not None and r["volume_at_fill"] > 0]
        avg_volume = statistics.mean(volume_values) if volume_values else 0.0

        # High liquidity heuristic: volume > 10000 and low spread
        is_liquid = avg_volume > 10_000 and avg_spread < 2.0

        # Decision
        if urgency.upper() == "HIGH" and is_liquid:
            return {
                "order_type": "MARKET",
                "limit_offset_bps": 0.0,
                "reason": (f"{ticker} is liquid (avg_vol={avg_volume:.0f}, "
                           f"spread={avg_spread:.1f} bps); MARKET for HIGH urgency"),
            }

        if avg_spread > 0 and avg_slippage > SPREAD_MULT_LIMIT_THRESHOLD * avg_spread:
            offset = round(avg_spread * 0.5, 2)  # half-spread offset
            return {
                "order_type": "PEGGED_MID",
                "limit_offset_bps": offset,
                "reason": (f"Avg slippage ({avg_slippage:.1f} bps) > "
                           f"{SPREAD_MULT_LIMIT_THRESHOLD}x spread ({avg_spread:.1f} bps); "
                           f"use PEGGED_MID with {offset} bps offset"),
            }

        if avg_slippage > 3.0:
            offset = round(max(avg_slippage * 0.5, 1.0), 2)
            return {
                "order_type": "LIMIT",
                "limit_offset_bps": offset,
                "reason": (f"Avg slippage is elevated ({avg_slippage:.1f} bps); "
                           f"LIMIT with {offset} bps offset recommended"),
            }

        # Default: liquid enough and low slippage
        if urgency.upper() == "HIGH":
            return {
                "order_type": "MARKET",
                "limit_offset_bps": 0.0,
                "reason": f"Low slippage ({avg_slippage:.1f} bps) and HIGH urgency; MARKET is fine",
            }

        return {
            "order_type": "LIMIT",
            "limit_offset_bps": round(max(avg_slippage * 0.3, 0.5), 2),
            "reason": (f"Moderate slippage ({avg_slippage:.1f} bps); "
                       f"LIMIT recommended for NORMAL urgency"),
        }

    # ------------------------------------------------------------------
    # 6. Detect HFT feeding
    # ------------------------------------------------------------------
    def detect_hft_feeding(self, strategy: str,
                           lookback_days: int = 30) -> Dict[str, Any]:
        """Detect if a strategy is consistently losing to HFT.

        Signal: avg slippage consistently exceeds the market spread,
        meaning the strategy's signals are being front-run or faded.

        Returns:
            {is_feeding_hft, avg_slippage_vs_spread, recommendation}
        """
        trades = self._fetch_trades(
            lookback_days,
            extra_where="AND strategy = ?",
            params=(strategy,),
        )

        if not trades:
            return {
                "is_feeding_hft": False,
                "avg_slippage_vs_spread": 0.0,
                "n_trades": 0,
                "recommendation": f"No data for strategy '{strategy}'",
            }

        # Filter to trades with spread data
        spread_trades = [t for t in trades
                         if t["market_spread_bps"] is not None
                         and float(t["market_spread_bps"]) > 0]

        if len(spread_trades) < MIN_TRADES_FOR_RECOMMENDATION:
            avg_bps = statistics.mean([float(t["slippage_bps"]) for t in trades])
            return {
                "is_feeding_hft": False,
                "avg_slippage_vs_spread": 0.0,
                "n_trades": len(trades),
                "avg_slippage_bps": round(avg_bps, 4),
                "recommendation": (
                    f"Insufficient spread data ({len(spread_trades)} trades with spread). "
                    f"Record market_spread_bps for better HFT detection."
                ),
            }

        ratios = []
        for t in spread_trades:
            slip = float(t["slippage_bps"])
            spread = float(t["market_spread_bps"])
            if spread > 0:
                ratios.append(slip / spread)

        if not ratios:
            return {
                "is_feeding_hft": False,
                "avg_slippage_vs_spread": 0.0,
                "n_trades": len(trades),
                "recommendation": "No valid spread ratios computable",
            }

        avg_ratio = statistics.mean(ratios)
        is_feeding = avg_ratio > 1.0

        if is_feeding:
            if avg_ratio > 3.0:
                rec = (f"CRITICAL: strategy '{strategy}' avg slippage is "
                       f"{avg_ratio:.1f}x the spread. Strongly suggests HFT front-running. "
                       f"PAUSE and switch to limit orders or reduce signal frequency.")
            else:
                rec = (f"WARNING: strategy '{strategy}' avg slippage is "
                       f"{avg_ratio:.1f}x the spread. Possible HFT feeding. "
                       f"Consider using LIMIT orders or randomizing execution timing.")
        else:
            rec = (f"Strategy '{strategy}' slippage ({avg_ratio:.1f}x spread) "
                   f"is within normal range. No HFT concern detected.")

        return {
            "is_feeding_hft": is_feeding,
            "avg_slippage_vs_spread": round(avg_ratio, 4),
            "n_trades": len(trades),
            "n_with_spread": len(spread_trades),
            "recommendation": rec,
        }

    # ------------------------------------------------------------------
    # 7. Full slippage report
    # ------------------------------------------------------------------
    def get_slippage_report(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Complete slippage report combining all analytics.

        Returns a dict suitable for dashboard display or Telegram notification.
        """
        cost = self.compute_total_slippage_cost(lookback_days)
        time_analysis = self.analyze_by_time_of_day(lookback_days)
        type_analysis = self.analyze_by_instrument_type(lookback_days)

        # Per-strategy breakdown
        trades = self._fetch_trades(lookback_days)
        strategies = set(t["strategy"] for t in trades)
        by_strategy = {}
        for strat in strategies:
            by_strategy[strat] = self.analyze_by_strategy(strat, lookback_days)

        # Worst strategy
        worst_strategy = None
        worst_avg = 0.0
        for strat, stats in by_strategy.items():
            if stats["n_trades"] > 0 and stats["avg_slippage_bps"] > worst_avg:
                worst_avg = stats["avg_slippage_bps"]
                worst_strategy = strat

        # Action items
        action_items = []
        for strat, stats in by_strategy.items():
            if stats["recommendation"] != "OK":
                action_items.append(f"{strat}: {stats['recommendation']}")

        if time_analysis["worst_hour"] is not None:
            wh = time_analysis["worst_hour"]
            wh_bps = time_analysis["by_hour"][wh]["avg_bps"]
            if wh_bps > 5.0:
                action_items.append(
                    f"Avoid trading at {wh:02d}:00 UTC (avg {wh_bps:.1f} bps)")

        return {
            "lookback_days": lookback_days,
            "total_cost_usd": cost["total_cost_usd"],
            "pct_of_pnl": cost["pct_of_pnl"],
            "n_trades": len(trades),
            "worst_strategy": worst_strategy,
            "worst_strategy_avg_bps": round(worst_avg, 2),
            "worst_hour": time_analysis["worst_hour"],
            "best_hour": time_analysis["best_hour"],
            "by_strategy": by_strategy,
            "by_instrument_type": type_analysis["by_type"],
            "cost_by_strategy": cost["by_strategy"],
            "action_items": action_items,
        }

    # ------------------------------------------------------------------
    # 8. Telegram-friendly format
    # ------------------------------------------------------------------
    def format_telegram_report(self, lookback_days: int = 30) -> str:
        """Format slippage report for Telegram notification.

        Returns a plain-text string.
        """
        report = self.get_slippage_report(lookback_days)

        lines = [
            f"SLIPPAGE REPORT ({lookback_days}d)",
            f"Trades: {report['n_trades']}",
            f"Total cost: ${report['total_cost_usd']:.2f} "
            f"({report['pct_of_pnl']:.1f}% of expected cost)",
        ]

        if report["worst_strategy"]:
            lines.append(
                f"Worst strategy: {report['worst_strategy']} "
                f"(avg {report['worst_strategy_avg_bps']:.1f} bps)"
            )

        if report["worst_hour"] is not None:
            wh = report["worst_hour"]
            wh_data = report["by_strategy"]  # Access via full report
            # Get worst hour data from time analysis
            wh_bps = 0.0
            time_data = self.analyze_by_time_of_day(lookback_days)
            if wh in time_data["by_hour"]:
                wh_bps = time_data["by_hour"][wh]["avg_bps"]
            lines.append(f"Worst hour: {wh:02d}:00 UTC (avg {wh_bps:.1f} bps)")

        if report["best_hour"] is not None:
            bh = report["best_hour"]
            bh_bps = 0.0
            time_data = self.analyze_by_time_of_day(lookback_days)
            if bh in time_data["by_hour"]:
                bh_bps = time_data["by_hour"][bh]["avg_bps"]
            lines.append(f"Best hour: {bh:02d}:00 UTC (avg {bh_bps:.1f} bps)")

        if report["by_instrument_type"]:
            lines.append("--- By type ---")
            for itype, stats in sorted(report["by_instrument_type"].items()):
                lines.append(
                    f"  {itype}: avg {stats['avg_slippage_bps']:.1f} bps "
                    f"(n={stats['n_trades']})"
                )

        if report["action_items"]:
            lines.append("--- Actions ---")
            for item in report["action_items"]:
                lines.append(f"  - {item}")

        return "\n".join(lines)
