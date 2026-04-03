"""
CostTracker — tracks commissions and total costs per strategy.

Monitors cost_ratio (commissions / gross P&L) and alerts
when a strategy's costs eat too much of the edge.

Storage: SQLite table 'cost_log' in data/execution_metrics.db
"""
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "execution_metrics.db"

# Thresholds
COST_RATIO_WARNING = 0.30   # 30% — commissions eating 30% of gross P&L
COST_RATIO_KILL = 0.50      # 50% — recommend killing the strategy
MIN_TRADES_FOR_KILL = 30    # Need at least 30 trades for kill recommendation


def _default_alert_callback() -> Callable | None:
    """Try to import Telegram send_alert as default callback."""
    try:
        from core.telegram_alert import send_alert
        return send_alert
    except ImportError:
        return None


class CostTracker:
    """Tracks commissions and total costs per strategy.

    Monitors cost_ratio (commissions / gross P&L) and alerts
    when a strategy's costs eat too much of the edge.

    Storage: SQLite table 'cost_log' in data/execution_metrics.db
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS cost_log (
        trade_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        strategy TEXT NOT NULL,
        instrument TEXT NOT NULL,
        instrument_type TEXT NOT NULL DEFAULT 'EQUITY',
        commission REAL NOT NULL,
        notional_value REAL NOT NULL,
        pnl_gross REAL,
        cost_ratio REAL
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
        logger.info("CostTracker initialized — db=%s", self.db_path)

    def _init_db(self):
        """Create cost_log table if not exists."""
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
    def record_commission(
        self,
        trade_id: str,
        strategy: str,
        instrument: str,
        instrument_type: str,
        commission: float,
        notional_value: float,
        pnl_gross: float | None = None,
    ) -> Dict[str, Any]:
        """Record commission for a trade.

        Calculates cost_ratio if pnl_gross is provided and non-zero.

        Returns dict with recorded metrics.
        """
        if commission < 0:
            raise ValueError(f"commission must be >= 0, got {commission}")
        if notional_value <= 0:
            raise ValueError(f"notional_value must be > 0, got {notional_value}")

        # Cost ratio: commissions / gross P&L (only if P&L available and non-zero)
        cost_ratio = None
        if pnl_gross is not None and pnl_gross != 0:
            cost_ratio = commission / abs(pnl_gross)

        timestamp = datetime.now(UTC).isoformat()

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cost_log
                   (trade_id, timestamp, strategy, instrument, instrument_type,
                    commission, notional_value, pnl_gross, cost_ratio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, timestamp, strategy, instrument,
                 instrument_type.upper(), commission, notional_value,
                 pnl_gross, cost_ratio),
            )
            conn.commit()

        result = {
            "trade_id": trade_id,
            "commission": commission,
            "cost_ratio": round(cost_ratio, 4) if cost_ratio is not None else None,
            "cost_per_dollar_traded": round(commission / notional_value * 10_000, 2),
        }

        logger.info(
            "Commission recorded: %s %s — $%.4f (ratio=%s)",
            strategy, instrument, commission,
            f"{cost_ratio:.2%}" if cost_ratio is not None else "N/A",
        )

        return result

    # -------------------------------------------------------------------------
    # Cost report
    # -------------------------------------------------------------------------
    def get_cost_report(
        self,
        strategy: str | None = None,
        period: str = "30d",
    ) -> Dict[str, Any]:
        """Cost report per strategy.

        Returns:
        - total_commission: float
        - total_pnl_gross: float
        - total_pnl_net: float
        - cost_ratio: commissions / gross_pnl
        - avg_commission_per_trade: float
        - cost_per_dollar_traded: float
        """
        cutoff = self._period_cutoff(period)

        conn = self._get_conn()
        try:
            base_where = "WHERE timestamp >= ?"
            params: list = [cutoff]
            if strategy:
                base_where += " AND strategy = ?"
                params.append(strategy)

            row = conn.execute(
                f"SELECT SUM(commission) as total_comm, "
                f"       SUM(pnl_gross) as total_pnl, "
                f"       SUM(notional_value) as total_notional, "
                f"       COUNT(*) as n_trades "
                f"FROM cost_log {base_where}",
                params,
            ).fetchone()

            total_comm = row["total_comm"] or 0.0
            total_pnl = row["total_pnl"] or 0.0
            total_notional = row["total_notional"] or 0.0
            n_trades = row["n_trades"] or 0

            # Cost ratio (use absolute gross P&L to handle negative P&L)
            if total_pnl != 0:
                cost_ratio = total_comm / abs(total_pnl)
            else:
                cost_ratio = float("inf") if total_comm > 0 else 0.0

            # Per-trade average
            avg_per_trade = total_comm / n_trades if n_trades > 0 else 0.0

            # Cost per dollar traded (bps)
            cost_per_dollar = (
                total_comm / total_notional * 10_000 if total_notional > 0 else 0.0
            )

            # Breakdown by strategy
            rows = conn.execute(
                f"SELECT strategy, SUM(commission) as comm, "
                f"       SUM(pnl_gross) as pnl, COUNT(*) as n "
                f"FROM cost_log {base_where} GROUP BY strategy",
                params,
            ).fetchall()

            by_strategy = {}
            for r in rows:
                s_pnl = r["pnl"] or 0.0
                s_comm = r["comm"] or 0.0
                s_ratio = s_comm / abs(s_pnl) if s_pnl != 0 else (
                    float("inf") if s_comm > 0 else 0.0
                )
                by_strategy[r["strategy"]] = {
                    "commission": round(s_comm, 4),
                    "pnl_gross": round(s_pnl, 2),
                    "pnl_net": round(s_pnl - s_comm, 2),
                    "cost_ratio": round(s_ratio, 4),
                    "n_trades": r["n"],
                }

            return {
                "total_commission": round(total_comm, 4),
                "total_pnl_gross": round(total_pnl, 2),
                "total_pnl_net": round(total_pnl - total_comm, 2),
                "cost_ratio": round(cost_ratio, 4),
                "avg_commission_per_trade": round(avg_per_trade, 4),
                "cost_per_dollar_traded": round(cost_per_dollar, 2),
                "n_trades": n_trades,
                "by_strategy": by_strategy,
            }
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Alerts
    # -------------------------------------------------------------------------
    def check_cost_alerts(self) -> List[Dict[str, Any]]:
        """Alert if cost_ratio > 30% on a strategy.
        Kill strategy recommendation if cost_ratio > 50% on 30+ trades.

        Returns list of triggered alerts.
        """
        cutoff = self._period_cutoff("30d")

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT strategy, SUM(commission) as total_comm, "
                "       SUM(pnl_gross) as total_pnl, COUNT(*) as n_trades "
                "FROM cost_log WHERE timestamp >= ? "
                "GROUP BY strategy",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        alerts = []
        for r in rows:
            total_pnl = r["total_pnl"] or 0.0
            total_comm = r["total_comm"] or 0.0
            n_trades = r["n_trades"]
            strat = r["strategy"]

            if total_pnl == 0:
                cost_ratio = float("inf") if total_comm > 0 else 0.0
            else:
                cost_ratio = total_comm / abs(total_pnl)

            if cost_ratio >= COST_RATIO_KILL and n_trades >= MIN_TRADES_FOR_KILL:
                alert = {
                    "strategy": strat,
                    "level": "critical",
                    "cost_ratio": round(cost_ratio, 4),
                    "n_trades": n_trades,
                    "total_commission": round(total_comm, 2),
                    "total_pnl_gross": round(total_pnl, 2),
                    "recommendation": "KILL strategy — costs exceed 50% of edge",
                }
                msg = (
                    f"COUTS EXCESSIFS — {strat}\n"
                    f"Cost ratio: {cost_ratio:.0%} (seuil: 50%)\n"
                    f"Commission totale: ${total_comm:.2f}\n"
                    f"PnL brut: ${total_pnl:.2f}\n"
                    f"Trades: {n_trades}\n"
                    f"Recommandation: DESACTIVER cette strategie"
                )
                logger.critical(msg)
                if self.alert_callback:
                    self.alert_callback(msg, level="critical")
                alerts.append(alert)

            elif cost_ratio >= COST_RATIO_WARNING:
                alert = {
                    "strategy": strat,
                    "level": "warning",
                    "cost_ratio": round(cost_ratio, 4),
                    "n_trades": n_trades,
                    "total_commission": round(total_comm, 2),
                    "total_pnl_gross": round(total_pnl, 2),
                    "recommendation": "Monitor — costs at 30%+ of edge",
                }
                msg = (
                    f"Couts eleves — {strat}\n"
                    f"Cost ratio: {cost_ratio:.0%} (seuil: 30%)\n"
                    f"Commission totale: ${total_comm:.2f}\n"
                    f"PnL brut: ${total_pnl:.2f}\n"
                    f"Trades: {n_trades}"
                )
                logger.warning(msg)
                if self.alert_callback:
                    self.alert_callback(msg, level="warning")
                alerts.append(alert)

        return alerts

    # -------------------------------------------------------------------------
    # Viability
    # -------------------------------------------------------------------------
    def get_strategy_viability(
        self,
        strategy: str,
        min_trades: int = 30,
    ) -> Dict[str, Any]:
        """Is this strategy viable after real costs?

        Returns: {viable: bool, cost_ratio: float, break_even_sharpe: float,
                  n_trades: int, sufficient_data: bool}
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT SUM(commission) as total_comm, "
                "       SUM(pnl_gross) as total_pnl, "
                "       SUM(notional_value) as total_notional, "
                "       COUNT(*) as n_trades "
                "FROM cost_log WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        finally:
            conn.close()

        n_trades = row["n_trades"] or 0
        sufficient_data = n_trades >= min_trades

        total_comm = row["total_comm"] or 0.0
        total_pnl = row["total_pnl"] or 0.0

        if total_pnl != 0:
            cost_ratio = total_comm / abs(total_pnl)
        else:
            cost_ratio = float("inf") if total_comm > 0 else 0.0

        # Break-even Sharpe estimation:
        # If costs represent X% of gross P&L, the strategy needs at least
        # X/(1-X) more return to break even. Map to approximate Sharpe.
        if cost_ratio < 1.0:
            # Rough heuristic: if cost_ratio = 0.5, you need 2x gross -> Sharpe ~2.0
            break_even_sharpe = cost_ratio / (1 - cost_ratio) if cost_ratio < 1 else float("inf")
        else:
            break_even_sharpe = float("inf")

        viable = cost_ratio < COST_RATIO_KILL if sufficient_data else True

        return {
            "strategy": strategy,
            "viable": viable,
            "cost_ratio": round(cost_ratio, 4),
            "break_even_sharpe": round(break_even_sharpe, 4) if break_even_sharpe != float("inf") else float("inf"),
            "n_trades": n_trades,
            "sufficient_data": sufficient_data,
            "total_commission": round(total_comm, 2),
            "total_pnl_gross": round(total_pnl, 2),
            "total_pnl_net": round(total_pnl - total_comm, 2),
        }

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
            days = 30  # Default

        cutoff = datetime.now(UTC) - timedelta(days=days)
        return cutoff.isoformat()
