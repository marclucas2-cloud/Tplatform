"""
Automated Trade Journal for live and paper trading.

Records every trade with full execution details, P&L, slippage,
and metadata for post-mortem analysis and tax reporting.

Storage: SQLite database in data/live_journal.db (live) or data/paper_journal.db (paper)

Usage:
    from core.trade_journal import TradeJournal

    journal = TradeJournal(mode="PAPER")
    journal.record_trade_open(
        trade_id=None,  # auto-generated
        strategy="ORB_5MIN_V2",
        instrument="AAPL",
        instrument_type="EQUITY",
        direction="LONG",
        quantity=10,
        entry_price_requested=175.50,
        entry_price_filled=175.55,
        stop_loss=174.00,
        take_profit=178.00,
    )
    journal.record_trade_close(
        trade_id="PAPER-2026-0001",
        exit_price_requested=177.80,
        exit_price_filled=177.75,
        exit_reason="TP_HIT",
        commission=0.10,
    )
    summary = journal.get_daily_summary()
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

# Instrument-type specifics for P&L calculations
FX_PIP_VALUES = {
    # Major pairs: standard lot = 100,000 units, pip = 0.0001
    # We store per-unit pip value — caller passes quantity in units
    "EUR/USD": 0.0001,
    "GBP/USD": 0.0001,
    "USD/JPY": 0.01,
    "USD/CHF": 0.0001,
    "AUD/USD": 0.0001,
    "NZD/USD": 0.0001,
    "USD/CAD": 0.0001,
    "EUR/GBP": 0.0001,
    "EUR/JPY": 0.01,
    "GBP/JPY": 0.01,
    "EUR/NOK": 0.0001,
}

FUTURES_MULTIPLIERS = {
    "ES": 50.0,    # E-mini S&P 500
    "NQ": 20.0,    # E-mini NASDAQ 100
    "YM": 5.0,     # E-mini Dow
    "RTY": 50.0,   # E-mini Russell 2000
    "CL": 1000.0,  # Crude Oil
    "GC": 100.0,   # Gold
    "SI": 5000.0,  # Silver
    "ZB": 1000.0,  # Treasury Bond
    "ZN": 1000.0,  # 10-Year Note
    "6E": 125000.0,  # Euro FX
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    timestamp_signal TEXT,
    timestamp_order_sent TEXT,
    timestamp_filled TEXT,
    timestamp_closed TEXT,
    latency_signal_to_fill_ms INTEGER,
    strategy TEXT NOT NULL,
    instrument TEXT NOT NULL,
    instrument_type TEXT NOT NULL DEFAULT 'EQUITY',
    direction TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price_requested REAL,
    entry_price_filled REAL,
    slippage_entry_bps REAL,
    exit_price_requested REAL,
    exit_price_filled REAL,
    slippage_exit_bps REAL,
    stop_loss REAL,
    take_profit REAL,
    pnl_gross REAL,
    commission REAL DEFAULT 0.0,
    pnl_net REAL,
    pnl_pct REAL,
    holding_seconds INTEGER,
    regime TEXT,
    confluence_score REAL,
    conviction_level TEXT,
    exit_reason TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN'
)
"""

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)",
    "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)",
    "CREATE INDEX IF NOT EXISTS idx_trades_instrument ON trades(instrument)",
    "CREATE INDEX IF NOT EXISTS idx_trades_timestamp_filled ON trades(timestamp_filled)",
]

VALID_DIRECTIONS = {"LONG", "SHORT"}
VALID_INSTRUMENT_TYPES = {"EQUITY", "FX", "FUTURES"}
VALID_EXIT_REASONS = {"TP_HIT", "SL_HIT", "EOD_CLOSE", "KILL_SWITCH", "MANUAL", "SIGNAL"}
VALID_STATUSES = {"OPEN", "CLOSED", "CANCELLED"}
VALID_MODES = {"LIVE", "PAPER"}


class TradeJournal:
    """Automated trade journal for live and paper trading.

    Records every trade with full execution details, P&L,
    slippage, and metadata for post-mortem analysis and tax reporting.

    Storage: SQLite database in data/live_journal.db (live) or data/paper_journal.db (paper)
    """

    def __init__(self, mode: str = "LIVE", db_path: Optional[str | Path] = None):
        """Initialize the trade journal.

        Args:
            mode: "LIVE" or "PAPER". Determines default db path and trade ID prefix.
            db_path: Override path for the SQLite database. If None, uses
                     data/live_journal.db or data/paper_journal.db.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of {VALID_MODES}")

        self.mode = mode

        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            filename = "live_journal.db" if mode == "LIVE" else "paper_journal.db"
            self.db_path = DATA_DIR / filename
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        logger.info(f"TradeJournal initialized: mode={mode}, db={self.db_path}")

    def _init_db(self):
        """Create tables and indexes if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(CREATE_TABLE_SQL)
            for idx_sql in CREATE_INDEX_SQL:
                conn.execute(idx_sql)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new connection with row_factory set."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _next_trade_id(self) -> str:
        """Generate sequential trade ID: {MODE}-{YYYY}-{NNNN}."""
        year = datetime.now(timezone.utc).strftime("%Y")
        prefix = f"{self.mode}-{year}-"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT trade_id FROM trades WHERE trade_id LIKE ? ORDER BY trade_id DESC LIMIT 1",
                (f"{prefix}%",),
            ).fetchone()
        if row:
            last_seq = int(row[0].split("-")[-1])
            return f"{prefix}{last_seq + 1:04d}"
        return f"{prefix}0001"

    # ─── Record operations ──────────────────────────────────────────────

    def record_trade_open(
        self,
        trade_id: Optional[str],
        strategy: str,
        instrument: str,
        instrument_type: str,
        direction: str,
        quantity: float,
        entry_price_requested: float,
        entry_price_filled: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        regime: Optional[str] = None,
        confluence_score: Optional[float] = None,
        conviction_level: Optional[str] = None,
        timestamp_signal: Optional[str] = None,
        timestamp_order_sent: Optional[str] = None,
        timestamp_filled: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """Record a new trade opening.

        Args:
            trade_id: Unique trade identifier. If None, auto-generated as
                      {MODE}-{YYYY}-{sequential}.
            strategy: Strategy name (e.g. "ORB_5MIN_V2").
            instrument: Ticker/pair (e.g. "AAPL", "EUR/USD", "ES").
            instrument_type: One of EQUITY, FX, FUTURES.
            direction: LONG or SHORT.
            quantity: Number of shares/contracts/units.
            entry_price_requested: Price requested at signal time.
            entry_price_filled: Actual fill price from broker.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            regime: Market regime at signal time.
            confluence_score: Confluence score (0-1).
            conviction_level: Conviction level (e.g. "HIGH", "MEDIUM", "LOW").
            timestamp_signal: ISO timestamp of signal generation.
            timestamp_order_sent: ISO timestamp of order submission.
            timestamp_filled: ISO timestamp of fill.
            notes: Free-form notes.

        Returns:
            trade_id of the recorded trade.
        """
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Invalid direction '{direction}'. Must be one of {VALID_DIRECTIONS}")
        if instrument_type not in VALID_INSTRUMENT_TYPES:
            raise ValueError(f"Invalid instrument_type '{instrument_type}'. Must be one of {VALID_INSTRUMENT_TYPES}")

        now_iso = datetime.now(timezone.utc).isoformat()
        if trade_id is None:
            trade_id = self._next_trade_id()
        if timestamp_signal is None:
            timestamp_signal = now_iso
        if timestamp_filled is None:
            timestamp_filled = now_iso

        # Calculate slippage in basis points
        slippage_entry_bps = self._calculate_slippage_bps(
            entry_price_requested, entry_price_filled, direction, "entry"
        )

        # Calculate latency if both timestamps provided
        latency_ms = None
        if timestamp_signal and timestamp_filled:
            latency_ms = self._calculate_latency_ms(timestamp_signal, timestamp_filled)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO trades (
                    trade_id, mode, timestamp_signal, timestamp_order_sent,
                    timestamp_filled, latency_signal_to_fill_ms,
                    strategy, instrument, instrument_type, direction, quantity,
                    entry_price_requested, entry_price_filled, slippage_entry_bps,
                    stop_loss, take_profit, regime, confluence_score, conviction_level,
                    notes, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade_id, self.mode, timestamp_signal, timestamp_order_sent,
                    timestamp_filled, latency_ms,
                    strategy, instrument, instrument_type, direction, quantity,
                    entry_price_requested, entry_price_filled, slippage_entry_bps,
                    stop_loss, take_profit, regime, confluence_score, conviction_level,
                    notes, "OPEN",
                ),
            )
            conn.commit()

        logger.info(f"Trade opened: {trade_id} {direction} {quantity} {instrument} @ {entry_price_filled}")
        return trade_id

    def record_trade_close(
        self,
        trade_id: str,
        exit_price_requested: float,
        exit_price_filled: float,
        exit_reason: str,
        commission: float = 0.0,
        timestamp_closed: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Record trade closing. Calculates P&L, slippage, holding time.

        Args:
            trade_id: ID of the trade to close.
            exit_price_requested: Desired exit price.
            exit_price_filled: Actual exit fill price.
            exit_reason: One of TP_HIT, SL_HIT, EOD_CLOSE, KILL_SWITCH, MANUAL, SIGNAL.
            commission: Total commission for the round-trip.
            timestamp_closed: ISO timestamp of close. If None, uses now.
            notes: Additional notes to append.

        Returns:
            Dict with P&L details: pnl_gross, pnl_net, pnl_pct, holding_seconds, slippage_exit_bps.

        Raises:
            ValueError: If trade_id not found or already closed.
        """
        if exit_reason not in VALID_EXIT_REASONS:
            raise ValueError(f"Invalid exit_reason '{exit_reason}'. Must be one of {VALID_EXIT_REASONS}")

        now_iso = datetime.now(timezone.utc).isoformat()
        if timestamp_closed is None:
            timestamp_closed = now_iso

        # Fetch the open trade
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()

        if row is None:
            raise ValueError(f"Trade '{trade_id}' not found")
        if row["status"] == "CLOSED":
            raise ValueError(f"Trade '{trade_id}' is already closed")

        direction = row["direction"]
        quantity = row["quantity"]
        entry_price_filled = row["entry_price_filled"]
        instrument = row["instrument"]
        instrument_type = row["instrument_type"]

        # Calculate slippage exit
        slippage_exit_bps = self._calculate_slippage_bps(
            exit_price_requested, exit_price_filled, direction, "exit"
        )

        # Calculate P&L
        pnl_gross = self._calculate_pnl(
            direction, quantity, entry_price_filled, exit_price_filled,
            instrument, instrument_type,
        )
        pnl_net = pnl_gross - commission

        # P&L percentage (based on entry notional)
        entry_notional = self._calculate_notional(
            quantity, entry_price_filled, instrument, instrument_type
        )
        pnl_pct = (pnl_net / entry_notional * 100) if entry_notional > 0 else 0.0

        # Holding time
        holding_seconds = self._calculate_holding_seconds(row["timestamp_filled"], timestamp_closed)

        # Merge notes
        existing_notes = row["notes"] or ""
        merged_notes = existing_notes
        if notes:
            merged_notes = f"{existing_notes}\n{notes}" if existing_notes else notes

        # Update the trade
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE trades SET
                    exit_price_requested = ?,
                    exit_price_filled = ?,
                    slippage_exit_bps = ?,
                    pnl_gross = ?,
                    commission = ?,
                    pnl_net = ?,
                    pnl_pct = ?,
                    holding_seconds = ?,
                    exit_reason = ?,
                    timestamp_closed = ?,
                    notes = ?,
                    status = 'CLOSED'
                WHERE trade_id = ?""",
                (
                    exit_price_requested, exit_price_filled, slippage_exit_bps,
                    pnl_gross, commission, pnl_net, pnl_pct,
                    holding_seconds, exit_reason, timestamp_closed,
                    merged_notes, trade_id,
                ),
            )
            conn.commit()

        result = {
            "trade_id": trade_id,
            "pnl_gross": pnl_gross,
            "pnl_net": pnl_net,
            "pnl_pct": pnl_pct,
            "holding_seconds": holding_seconds,
            "slippage_exit_bps": slippage_exit_bps,
        }
        logger.info(
            f"Trade closed: {trade_id} reason={exit_reason} "
            f"pnl_net=${pnl_net:+.2f} ({pnl_pct:+.2f}%)"
        )
        return result

    def cancel_trade(self, trade_id: str, reason: str = "") -> None:
        """Cancel an open trade (e.g. order rejected, partial fill cancelled).

        Args:
            trade_id: ID of the trade to cancel.
            reason: Reason for cancellation.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Trade '{trade_id}' not found")
            if row[0] == "CLOSED":
                raise ValueError(f"Trade '{trade_id}' is already closed, cannot cancel")

            conn.execute(
                "UPDATE trades SET status = 'CANCELLED', notes = COALESCE(notes || '\n', '') || ?, "
                "timestamp_closed = ? WHERE trade_id = ?",
                (f"CANCELLED: {reason}", datetime.now(timezone.utc).isoformat(), trade_id),
            )
            conn.commit()
        logger.info(f"Trade cancelled: {trade_id} reason={reason}")

    # ─── Query operations ───────────────────────────────────────────────

    def get_trade(self, trade_id: str) -> Optional[dict]:
        """Get a single trade by ID.

        Returns:
            Dict with all trade fields, or None if not found.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def get_trades(
        self,
        strategy: Optional[str] = None,
        instrument_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status: Optional[str] = None,
        direction: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trades with filters.

        Args:
            strategy: Filter by strategy name.
            instrument_type: Filter by EQUITY, FX, FUTURES.
            start_date: ISO date string (inclusive), e.g. "2026-01-01".
            end_date: ISO date string (inclusive), e.g. "2026-12-31".
            status: Filter by OPEN, CLOSED, CANCELLED.
            direction: Filter by LONG, SHORT.
            limit: Maximum number of results (default 100).

        Returns:
            List of trade dicts, ordered by timestamp_filled DESC.
        """
        conditions = []
        params = []

        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        if instrument_type:
            conditions.append("instrument_type = ?")
            params.append(instrument_type)
        if start_date:
            conditions.append("timestamp_filled >= ?")
            params.append(start_date)
        if end_date:
            # Include full day
            conditions.append("timestamp_filled < ?")
            params.append(end_date + "T23:59:59.999999")
        if status:
            conditions.append("status = ?")
            params.append(status)
        if direction:
            conditions.append("direction = ?")
            params.append(direction)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM trades WHERE {where_clause} ORDER BY timestamp_filled DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_open_trades(self) -> list[dict]:
        """Get all currently open trades."""
        return self.get_trades(status="OPEN", limit=1000)

    # ─── Summary / reporting ────────────────────────────────────────────

    def get_daily_summary(self, date: Optional[str] = None) -> dict:
        """Summary for a specific day.

        Args:
            date: ISO date string, e.g. "2026-03-27". If None, uses today (UTC).

        Returns:
            Dict with: date, total_trades, closed_trades, open_trades,
            winners, losers, win_rate, pnl_gross, pnl_net, total_commission,
            avg_slippage_entry_bps, avg_slippage_exit_bps, best_trade, worst_trade,
            strategies_active.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        date_start = f"{date}T00:00:00"
        date_end = f"{date}T23:59:59.999999"

        with self._get_conn() as conn:
            # All trades opened or closed on this date
            trades = conn.execute(
                """SELECT * FROM trades
                   WHERE (timestamp_filled >= ? AND timestamp_filled <= ?)
                      OR (timestamp_closed >= ? AND timestamp_closed <= ?)""",
                (date_start, date_end, date_start, date_end),
            ).fetchall()

            # Closed trades for P&L
            closed = conn.execute(
                """SELECT * FROM trades
                   WHERE timestamp_closed >= ? AND timestamp_closed <= ?
                     AND status = 'CLOSED'""",
                (date_start, date_end),
            ).fetchall()

        trades = [dict(t) for t in trades]
        closed = [dict(c) for c in closed]

        winners = [t for t in closed if (t["pnl_net"] or 0) > 0]
        losers = [t for t in closed if (t["pnl_net"] or 0) < 0]
        flat = [t for t in closed if (t["pnl_net"] or 0) == 0]

        pnl_gross = sum(t["pnl_gross"] or 0 for t in closed)
        pnl_net = sum(t["pnl_net"] or 0 for t in closed)
        total_commission = sum(t["commission"] or 0 for t in closed)

        slippages_entry = [t["slippage_entry_bps"] for t in trades if t["slippage_entry_bps"] is not None]
        slippages_exit = [t["slippage_exit_bps"] for t in closed if t["slippage_exit_bps"] is not None]

        strategies = set(t["strategy"] for t in trades)

        best = max(closed, key=lambda t: t["pnl_net"] or 0) if closed else None
        worst = min(closed, key=lambda t: t["pnl_net"] or 0) if closed else None

        n_closed = len(closed)
        win_rate = (len(winners) / n_closed * 100) if n_closed > 0 else 0.0

        return {
            "date": date,
            "total_trades": len(trades),
            "closed_trades": n_closed,
            "open_trades": len([t for t in trades if t["status"] == "OPEN"]),
            "cancelled_trades": len([t for t in trades if t["status"] == "CANCELLED"]),
            "winners": len(winners),
            "losers": len(losers),
            "flat": len(flat),
            "win_rate": round(win_rate, 1),
            "pnl_gross": round(pnl_gross, 2),
            "pnl_net": round(pnl_net, 2),
            "total_commission": round(total_commission, 2),
            "avg_slippage_entry_bps": round(sum(slippages_entry) / len(slippages_entry), 2) if slippages_entry else 0.0,
            "avg_slippage_exit_bps": round(sum(slippages_exit) / len(slippages_exit), 2) if slippages_exit else 0.0,
            "best_trade": {"trade_id": best["trade_id"], "pnl_net": best["pnl_net"]} if best else None,
            "worst_trade": {"trade_id": worst["trade_id"], "pnl_net": worst["pnl_net"]} if worst else None,
            "strategies_active": sorted(strategies),
        }

    def get_weekly_summary(self) -> dict:
        """Weekly report: Sharpe, win rate, profit factor, slippage, costs.

        Covers the last 7 calendar days from today (UTC).

        Returns:
            Dict with: period, total_trades, win_rate, profit_factor, sharpe_ratio,
            avg_daily_pnl, total_pnl_net, total_commission, avg_slippage_entry_bps,
            avg_slippage_exit_bps, avg_holding_seconds, strategies_breakdown.
        """
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        end = now.isoformat()

        with self._get_conn() as conn:
            closed = conn.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' AND timestamp_closed >= ? AND timestamp_closed <= ?",
                (start, end),
            ).fetchall()

        closed = [dict(c) for c in closed]
        return self._build_period_summary(closed, f"weekly ({start[:10]} to {end[:10]})")

    def get_monthly_summary(self) -> dict:
        """Monthly report with KPI for scaling decisions.

        Covers the last 30 calendar days from today (UTC).

        Returns:
            Same structure as weekly_summary but for 30 days, plus per-strategy breakdown.
        """
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        end = now.isoformat()

        with self._get_conn() as conn:
            closed = conn.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' AND timestamp_closed >= ? AND timestamp_closed <= ?",
                (start, end),
            ).fetchall()

        closed = [dict(c) for c in closed]
        return self._build_period_summary(closed, f"monthly ({start[:10]} to {end[:10]})")

    def get_pnl(self, period: str = "today") -> dict:
        """P&L for a given period.

        Args:
            period: One of "today", "mtd" (month-to-date), "ytd" (year-to-date),
                    "7d" (last 7 days), "30d" (last 30 days).

        Returns:
            Dict with: period, pnl_gross, pnl_net, total_commission,
            n_trades, win_rate.
        """
        now = datetime.now(timezone.utc)

        if period == "today":
            start = now.strftime("%Y-%m-%dT00:00:00")
        elif period == "mtd":
            start = now.strftime("%Y-%m-01T00:00:00")
        elif period == "ytd":
            start = now.strftime("%Y-01-01T00:00:00")
        elif period == "7d":
            start = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        elif period == "30d":
            start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        else:
            raise ValueError(f"Invalid period '{period}'. Must be one of: today, mtd, ytd, 7d, 30d")

        end = now.isoformat()

        with self._get_conn() as conn:
            closed = conn.execute(
                "SELECT pnl_gross, pnl_net, commission FROM trades "
                "WHERE status = 'CLOSED' AND timestamp_closed >= ? AND timestamp_closed <= ?",
                (start, end),
            ).fetchall()

            winners = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE status = 'CLOSED' AND timestamp_closed >= ? AND timestamp_closed <= ? "
                "AND pnl_net > 0",
                (start, end),
            ).fetchone()[0]

        closed = [dict(c) for c in closed]
        n_trades = len(closed)
        pnl_gross = sum(c["pnl_gross"] or 0 for c in closed)
        pnl_net = sum(c["pnl_net"] or 0 for c in closed)
        total_commission = sum(c["commission"] or 0 for c in closed)
        win_rate = (winners / n_trades * 100) if n_trades > 0 else 0.0

        return {
            "period": period,
            "pnl_gross": round(pnl_gross, 2),
            "pnl_net": round(pnl_net, 2),
            "total_commission": round(total_commission, 2),
            "n_trades": n_trades,
            "win_rate": round(win_rate, 1),
        }

    # ─── Private helpers ────────────────────────────────────────────────

    def _build_period_summary(self, closed_trades: list[dict], label: str) -> dict:
        """Build a summary dict from a list of closed trades."""
        n = len(closed_trades)
        if n == 0:
            return {
                "period": label,
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "avg_daily_pnl": 0.0,
                "total_pnl_net": 0.0,
                "total_commission": 0.0,
                "avg_slippage_entry_bps": 0.0,
                "avg_slippage_exit_bps": 0.0,
                "avg_holding_seconds": 0,
                "strategies_breakdown": {},
            }

        pnls = [t["pnl_net"] or 0 for t in closed_trades]
        gross_wins = sum(p for p in pnls if p > 0)
        gross_losses = abs(sum(p for p in pnls if p < 0))
        winners = sum(1 for p in pnls if p > 0)

        # Profit factor
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)

        # Simple Sharpe approximation (annualized from daily, assuming ~252 trading days)
        # Group by date to get daily P&L
        daily_pnl: dict[str, float] = {}
        for t in closed_trades:
            date_key = (t["timestamp_closed"] or "")[:10]
            daily_pnl[date_key] = daily_pnl.get(date_key, 0) + (t["pnl_net"] or 0)

        daily_values = list(daily_pnl.values())
        if len(daily_values) >= 2:
            mean_daily = sum(daily_values) / len(daily_values)
            std_daily = (sum((v - mean_daily) ** 2 for v in daily_values) / (len(daily_values) - 1)) ** 0.5
            sharpe = (mean_daily / std_daily * math.sqrt(252)) if std_daily > 0 else 0.0
        elif len(daily_values) == 1:
            sharpe = 0.0  # Not enough data
        else:
            sharpe = 0.0

        slippages_entry = [t["slippage_entry_bps"] for t in closed_trades if t["slippage_entry_bps"] is not None]
        slippages_exit = [t["slippage_exit_bps"] for t in closed_trades if t["slippage_exit_bps"] is not None]
        holdings = [t["holding_seconds"] for t in closed_trades if t["holding_seconds"] is not None]

        total_pnl = sum(pnls)
        total_commission = sum(t["commission"] or 0 for t in closed_trades)

        # Per-strategy breakdown
        strat_map: dict[str, list[float]] = {}
        for t in closed_trades:
            strat = t["strategy"]
            if strat not in strat_map:
                strat_map[strat] = []
            strat_map[strat].append(t["pnl_net"] or 0)

        strategies_breakdown = {}
        for strat, strat_pnls in strat_map.items():
            s_wins = sum(1 for p in strat_pnls if p > 0)
            strategies_breakdown[strat] = {
                "n_trades": len(strat_pnls),
                "pnl_net": round(sum(strat_pnls), 2),
                "win_rate": round(s_wins / len(strat_pnls) * 100, 1) if strat_pnls else 0.0,
            }

        n_days = max(len(daily_values), 1)

        return {
            "period": label,
            "total_trades": n,
            "win_rate": round(winners / n * 100, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "sharpe_ratio": round(sharpe, 2),
            "avg_daily_pnl": round(total_pnl / n_days, 2),
            "total_pnl_net": round(total_pnl, 2),
            "total_commission": round(total_commission, 2),
            "avg_slippage_entry_bps": round(sum(slippages_entry) / len(slippages_entry), 2) if slippages_entry else 0.0,
            "avg_slippage_exit_bps": round(sum(slippages_exit) / len(slippages_exit), 2) if slippages_exit else 0.0,
            "avg_holding_seconds": round(sum(holdings) / len(holdings)) if holdings else 0,
            "strategies_breakdown": strategies_breakdown,
        }

    @staticmethod
    def _calculate_slippage_bps(
        price_requested: float,
        price_filled: float,
        direction: str,
        side: str,
    ) -> float:
        """Calculate slippage in basis points (adverse direction = positive).

        Positive slippage = unfavorable (paid more or received less than requested).
        Negative slippage = favorable (price improvement).

        Args:
            price_requested: Requested price.
            price_filled: Actual fill price.
            direction: LONG or SHORT.
            side: 'entry' or 'exit'.
        """
        if price_requested == 0:
            return 0.0

        # For LONG entry: filled > requested = bad (positive slippage)
        # For LONG exit:  filled < requested = bad (positive slippage)
        # For SHORT entry: filled < requested = bad (positive slippage)
        # For SHORT exit:  filled > requested = bad (positive slippage)
        raw_diff = price_filled - price_requested

        if direction == "LONG":
            adverse = raw_diff if side == "entry" else -raw_diff
        else:  # SHORT
            adverse = -raw_diff if side == "entry" else raw_diff

        return round(adverse / price_requested * 10000, 2)  # Convert to bps

    @staticmethod
    def _calculate_pnl(
        direction: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        instrument: str,
        instrument_type: str,
    ) -> float:
        """Calculate gross P&L based on instrument type.

        - EQUITY: (exit - entry) * quantity * direction_sign
        - FX: (exit - entry) * quantity / pip_value (pip-based)
        - FUTURES: (exit - entry) * quantity * multiplier
        """
        if direction == "LONG":
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price

        if instrument_type == "EQUITY":
            return round(price_diff * quantity, 2)

        elif instrument_type == "FX":
            # For FX, P&L = price_diff * quantity
            # quantity is in base currency units
            return round(price_diff * quantity, 2)

        elif instrument_type == "FUTURES":
            # Extract root symbol (e.g. "ESH26" -> "ES")
            root = ""
            for ch in instrument:
                if ch.isalpha():
                    root += ch
                else:
                    break
            multiplier = FUTURES_MULTIPLIERS.get(root, 1.0)
            return round(price_diff * quantity * multiplier, 2)

        return round(price_diff * quantity, 2)

    @staticmethod
    def _calculate_notional(
        quantity: float,
        price: float,
        instrument: str,
        instrument_type: str,
    ) -> float:
        """Calculate notional value for P&L percentage calculation."""
        if instrument_type == "FUTURES":
            root = ""
            for ch in instrument:
                if ch.isalpha():
                    root += ch
                else:
                    break
            multiplier = FUTURES_MULTIPLIERS.get(root, 1.0)
            return abs(quantity * price * multiplier)

        return abs(quantity * price)

    @staticmethod
    def _calculate_latency_ms(ts_signal: str, ts_filled: str) -> Optional[int]:
        """Calculate latency in milliseconds between signal and fill."""
        try:
            t_signal = datetime.fromisoformat(ts_signal)
            t_filled = datetime.fromisoformat(ts_filled)
            delta = (t_filled - t_signal).total_seconds() * 1000
            return max(0, int(delta))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _calculate_holding_seconds(ts_open: str, ts_close: str) -> Optional[int]:
        """Calculate holding time in seconds."""
        try:
            t_open = datetime.fromisoformat(ts_open)
            t_close = datetime.fromisoformat(ts_close)
            return max(0, int((t_close - t_open).total_seconds()))
        except (ValueError, TypeError):
            return None
