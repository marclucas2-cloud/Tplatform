"""
SlippageTracker — measures real slippage on every live trade.

Compares requested price vs filled price, aggregates by strategy
and instrument type, and alerts when slippage exceeds thresholds.

Storage: SQLite table 'slippage_log' in data/execution_metrics.db
"""
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "execution_metrics.db"

# Thresholds
ALERT_WARNING_MULTIPLIER = 2.0   # > 2x backtest assumption → WARNING
ALERT_CRITICAL_MULTIPLIER = 3.0  # > 3x backtest assumption → CRITICAL


def _default_alert_callback() -> Callable | None:
    """Try to import Telegram send_alert as default callback."""
    try:
        from core.telegram_alert import send_alert
        return send_alert
    except ImportError:
        return None


class SlippageTracker:
    """Tracks real slippage on every live trade.

    Compares requested price vs filled price, aggregates by strategy
    and instrument type, and alerts when slippage exceeds thresholds.

    Storage: SQLite table 'slippage_log' in data/execution_metrics.db
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS slippage_log (
        trade_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        strategy TEXT NOT NULL,
        instrument TEXT NOT NULL,
        instrument_type TEXT NOT NULL DEFAULT 'EQUITY',
        side TEXT NOT NULL,
        order_type TEXT NOT NULL DEFAULT 'MARKET',
        requested_price REAL NOT NULL,
        filled_price REAL NOT NULL,
        slippage_bps REAL NOT NULL,
        backtest_slippage_bps REAL NOT NULL DEFAULT 2.0,
        ratio_real_vs_backtest REAL NOT NULL,
        market_spread_bps REAL,
        volume_at_fill REAL
    )
    """

    def __init__(self, db_path: Path | None = None,
                 alert_callback: Callable | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if alert_callback is not None:
            self.alert_callback = alert_callback
        else:
            self.alert_callback = _default_alert_callback()

        self._init_db()
        logger.info("SlippageTracker initialized — db=%s", self.db_path)

    def _init_db(self):
        """Create slippage_log table if not exists."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(self.SCHEMA)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a new connection with row_factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------------------
    # Record
    # -------------------------------------------------------------------------
    def record_fill(
        self,
        trade_id: str,
        strategy: str,
        instrument: str,
        instrument_type: str,
        side: str,
        order_type: str,
        requested_price: float,
        filled_price: float,
        backtest_slippage_bps: float = 2.0,
        market_spread_bps: float | None = None,
        volume_at_fill: float | None = None,
        quantity: float | None = None,
    ) -> Dict[str, Any]:
        """Record a fill and calculate slippage.

        slippage_bps = |filled - requested| / requested * 10000
        direction_adjusted: positive = adverse (cost), negative = favorable

        Alert if slippage > 2x backtest assumption.

        Returns dict with computed slippage metrics.
        """
        if requested_price <= 0:
            raise ValueError(f"requested_price must be > 0, got {requested_price}")

        # Raw slippage in bps
        raw_bps = abs(filled_price - requested_price) / requested_price * 10_000

        # Direction-adjusted: positive = adverse (cost), negative = favorable
        # BUY: adverse if filled > requested (you paid more)
        # SELL: adverse if filled < requested (you got less)
        side_upper = side.upper()
        if side_upper == "BUY":
            slippage_bps = (filled_price - requested_price) / requested_price * 10_000
        elif side_upper == "SELL":
            slippage_bps = (requested_price - filled_price) / requested_price * 10_000
        else:
            # Fallback: treat as absolute
            slippage_bps = raw_bps

        # Ratio vs backtest assumption
        if backtest_slippage_bps > 0:
            ratio = slippage_bps / backtest_slippage_bps
        else:
            ratio = 0.0 if slippage_bps == 0 else float("inf")

        timestamp = datetime.now(UTC).isoformat()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO slippage_log
                   (trade_id, timestamp, strategy, instrument, instrument_type,
                    side, order_type, requested_price, filled_price,
                    slippage_bps, backtest_slippage_bps, ratio_real_vs_backtest,
                    market_spread_bps, volume_at_fill)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, timestamp, strategy, instrument,
                 instrument_type.upper(), side_upper, order_type.upper(),
                 requested_price, filled_price, slippage_bps,
                 backtest_slippage_bps, ratio, market_spread_bps,
                 volume_at_fill),
            )
            conn.commit()

        # NOTE: per-unit cost estimate, multiply by quantity for full cost
        total_cost_per_unit = slippage_bps / 10_000 * requested_price
        # If quantity available, compute full position cost
        qty = float(quantity) if quantity is not None else 1.0
        total_cost_position = total_cost_per_unit * qty

        result = {
            "trade_id": trade_id,
            "slippage_bps": round(slippage_bps, 4),
            "raw_bps": round(raw_bps, 4),
            "ratio_real_vs_backtest": round(ratio, 4),
            "direction": "adverse" if slippage_bps > 0 else "favorable",
            "per_unit_cost_bps": round(total_cost_per_unit, 4),
            "total_cost_position": round(total_cost_position, 4),
        }

        logger.info(
            "Fill recorded: %s %s %s — slippage=%.2f bps (ratio=%.2fx backtest)",
            strategy, side_upper, instrument, slippage_bps, ratio,
        )

        # Alert on excessive slippage per-trade
        if ratio >= ALERT_CRITICAL_MULTIPLIER:
            msg = (
                f"SLIPPAGE CRITIQUE — {strategy}\n"
                f"Trade: {trade_id} ({instrument})\n"
                f"Slippage: {slippage_bps:.1f} bps "
                f"(backtest: {backtest_slippage_bps:.1f} bps, ratio: {ratio:.1f}x)\n"
                f"Recommandation: PAUSE strategy"
            )
            logger.critical(msg)
            if self.alert_callback:
                self.alert_callback(msg, level="critical")
        elif ratio >= ALERT_WARNING_MULTIPLIER:
            msg = (
                f"Slippage eleve — {strategy}\n"
                f"Trade: {trade_id} ({instrument})\n"
                f"Slippage: {slippage_bps:.1f} bps "
                f"(backtest: {backtest_slippage_bps:.1f} bps, ratio: {ratio:.1f}x)"
            )
            logger.warning(msg)
            if self.alert_callback:
                self.alert_callback(msg, level="warning")

        return result

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    def get_summary(
        self,
        period: str = "7d",
        strategy: str | None = None,
    ) -> Dict[str, Any]:
        """Slippage summary by strategy, instrument_type, order_type.

        Returns dict with:
        - by_strategy: {strategy: avg_slippage_bps}
        - by_instrument_type: {type: avg_slippage_bps}
        - by_order_type: {type: avg_slippage_bps}
        - ratio_real_vs_backtest: float (overall average)
        - worst_trades: top 5 worst slippage
        - total_cost_from_slippage: in dollars
        """
        cutoff = self._period_cutoff(period)

        conn = self._get_conn()
        try:
            base_where = "WHERE timestamp >= ?"
            params: list = [cutoff]
            if strategy:
                base_where += " AND strategy = ?"
                params.append(strategy)

            # By strategy
            rows = conn.execute(
                f"SELECT strategy, AVG(slippage_bps) as avg_bps "
                f"FROM slippage_log {base_where} GROUP BY strategy",
                params,
            ).fetchall()
            by_strategy = {r["strategy"]: round(r["avg_bps"], 4) for r in rows}

            # By instrument_type
            rows = conn.execute(
                f"SELECT instrument_type, AVG(slippage_bps) as avg_bps "
                f"FROM slippage_log {base_where} GROUP BY instrument_type",
                params,
            ).fetchall()
            by_instrument_type = {r["instrument_type"]: round(r["avg_bps"], 4) for r in rows}

            # By order_type
            rows = conn.execute(
                f"SELECT order_type, AVG(slippage_bps) as avg_bps "
                f"FROM slippage_log {base_where} GROUP BY order_type",
                params,
            ).fetchall()
            by_order_type = {r["order_type"]: round(r["avg_bps"], 4) for r in rows}

            # Overall ratio
            row = conn.execute(
                f"SELECT AVG(ratio_real_vs_backtest) as avg_ratio "
                f"FROM slippage_log {base_where}",
                params,
            ).fetchone()
            ratio_overall = round(row["avg_ratio"], 4) if row and row["avg_ratio"] is not None else 0.0

            # Worst 5 trades
            rows = conn.execute(
                f"SELECT trade_id, strategy, instrument, slippage_bps, "
                f"       requested_price, filled_price, side, timestamp "
                f"FROM slippage_log {base_where} "
                f"ORDER BY slippage_bps DESC LIMIT 5",
                params,
            ).fetchall()
            worst_trades = [dict(r) for r in rows]

            # Total cost from slippage (sum of adverse slippage in dollars)
            rows = conn.execute(
                f"SELECT slippage_bps, requested_price, side FROM slippage_log {base_where}",
                params,
            ).fetchall()
            # NOTE: per-unit cost estimate, multiply by quantity for full cost
            total_cost = 0.0
            for r in rows:
                # slippage_bps already direction-adjusted
                bps = r["slippage_bps"]
                if bps > 0:  # Only adverse
                    total_cost += bps / 10_000 * r["requested_price"]

            return {
                "by_strategy": by_strategy,
                "by_instrument_type": by_instrument_type,
                "by_order_type": by_order_type,
                "ratio_real_vs_backtest": ratio_overall,
                "worst_trades": worst_trades,
                "total_cost_from_slippage": round(total_cost, 2),
            }
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Alerts
    # -------------------------------------------------------------------------
    def check_alerts(self) -> List[Dict[str, Any]]:
        """Check if any strategy's 7d average slippage exceeds thresholds.

        - > 2x backtest on a strategy -> WARNING
        - > 3x backtest on a strategy -> CRITICAL + recommend pause

        Returns list of triggered alerts.
        """
        cutoff = self._period_cutoff("7d")

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT strategy, AVG(slippage_bps) as avg_bps, "
                "       AVG(backtest_slippage_bps) as avg_bt_bps, "
                "       AVG(ratio_real_vs_backtest) as avg_ratio, "
                "       COUNT(*) as n_trades "
                "FROM slippage_log WHERE timestamp >= ? "
                "GROUP BY strategy",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        alerts = []
        for r in rows:
            avg_ratio = r["avg_ratio"]
            strat = r["strategy"]

            if avg_ratio >= ALERT_CRITICAL_MULTIPLIER:
                alert = {
                    "strategy": strat,
                    "level": "critical",
                    "avg_slippage_bps": round(r["avg_bps"], 2),
                    "avg_ratio": round(avg_ratio, 2),
                    "n_trades": r["n_trades"],
                    "recommendation": "PAUSE strategy",
                }
                msg = (
                    f"SLIPPAGE CRITIQUE (7j) — {strat}\n"
                    f"Moyenne: {r['avg_bps']:.1f} bps "
                    f"(backtest: {r['avg_bt_bps']:.1f} bps, ratio: {avg_ratio:.1f}x)\n"
                    f"Trades: {r['n_trades']}\n"
                    f"Recommandation: PAUSE"
                )
                logger.critical(msg)
                if self.alert_callback:
                    self.alert_callback(msg, level="critical")
                alerts.append(alert)

            elif avg_ratio >= ALERT_WARNING_MULTIPLIER:
                alert = {
                    "strategy": strat,
                    "level": "warning",
                    "avg_slippage_bps": round(r["avg_bps"], 2),
                    "avg_ratio": round(avg_ratio, 2),
                    "n_trades": r["n_trades"],
                    "recommendation": "monitor closely",
                }
                msg = (
                    f"Slippage eleve (7j) — {strat}\n"
                    f"Moyenne: {r['avg_bps']:.1f} bps "
                    f"(backtest: {r['avg_bt_bps']:.1f} bps, ratio: {avg_ratio:.1f}x)\n"
                    f"Trades: {r['n_trades']}"
                )
                logger.warning(msg)
                if self.alert_callback:
                    self.alert_callback(msg, level="warning")
                alerts.append(alert)

        return alerts

    # -------------------------------------------------------------------------
    # Improvement report
    # -------------------------------------------------------------------------
    def get_improvement_report(self) -> Dict[str, Any]:
        """Compare market vs limit order slippage if both data available.

        Returns report with comparison and recommendations.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT order_type, AVG(slippage_bps) as avg_bps, COUNT(*) as n "
                "FROM slippage_log "
                "GROUP BY order_type"
            ).fetchall()
        finally:
            conn.close()

        by_type = {r["order_type"]: {"avg_bps": round(r["avg_bps"], 4), "n": r["n"]} for r in rows}

        report: Dict[str, Any] = {
            "by_order_type": by_type,
            "recommendations": [],
        }

        market = by_type.get("MARKET")
        limit = by_type.get("LIMIT")

        if market and limit:
            diff = market["avg_bps"] - limit["avg_bps"]
            report["market_vs_limit_diff_bps"] = round(diff, 4)
            if diff > 1.0:
                report["recommendations"].append(
                    f"Limit orders save ~{diff:.1f} bps vs market orders. "
                    f"Consider switching high-slippage strategies to limit orders."
                )
        else:
            report["recommendations"].append(
                "Insufficient data to compare market vs limit orders. "
                "Need fills with both order types."
            )

        # Check if any instrument_type has unusually high slippage
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT instrument_type, AVG(slippage_bps) as avg_bps, COUNT(*) as n "
                "FROM slippage_log GROUP BY instrument_type"
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            if r["avg_bps"] > 5.0 and r["n"] >= 5:
                report["recommendations"].append(
                    f"{r['instrument_type']} instruments averaging {r['avg_bps']:.1f} bps slippage. "
                    f"Consider adjusting backtest assumptions or order strategy."
                )

        return report

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _period_cutoff(period: str) -> str:
        """Convert period string like '7d' or '30d' to ISO cutoff timestamp."""
        if period.endswith("d"):
            days = int(period[:-1])
        elif period.endswith("h"):
            days = 0
            hours = int(period[:-1])
            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            return cutoff.isoformat()
        else:
            days = 7  # Default

        cutoff = datetime.now(UTC) - timedelta(days=days)
        return cutoff.isoformat()
