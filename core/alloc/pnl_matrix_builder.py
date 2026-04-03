"""
PnL Matrix Builder -- utility to build strategy PnL matrices from various sources.

Supports:
  - Backtest results (dict of DataFrames or named objects)
  - Live trades from a SQLite/PostgreSQL database
  - JSONL event files (one event per line)

Output format: DataFrame with index=dates, columns=strategy_names, values=daily_pnl.

Usage:
    from core.alloc.pnl_matrix_builder import PnLMatrixBuilder
    builder = PnLMatrixBuilder()
    matrix = builder.from_backtest_results(backtest_results)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


class PnLMatrixBuilder:
    """Build PnL matrices from various data sources."""

    def from_backtest_results(
        self,
        results: Dict[str, Any],
        pnl_column: str = "daily_pnl",
        date_column: str = "date",
    ) -> pd.DataFrame:
        """Build PnL matrix from backtest result objects.

        Accepts a dict where each value is one of:
          - pd.Series (index=dates, values=daily_pnl)
          - pd.DataFrame with a date column and pnl column
          - Object with a .equity_curve or .daily_pnl attribute (pd.Series)

        Args:
            results: {strategy_name: backtest_result}
            pnl_column: Column name for daily PnL in DataFrames.
            date_column: Column name for dates in DataFrames.

        Returns:
            DataFrame: index=dates, columns=strategy_names, values=daily_pnl.
        """
        series_dict: Dict[str, pd.Series] = {}

        for name, result in results.items():
            try:
                s = self._extract_pnl_series(result, pnl_column, date_column)
                if s is not None and len(s) > 0:
                    series_dict[name] = s
                else:
                    logger.warning(
                        "Skipping strategy '%s': no PnL data extracted", name
                    )
            except Exception as e:
                logger.error(
                    "Failed to extract PnL for strategy '%s': %s", name, e
                )

        if not series_dict:
            logger.warning("No valid PnL series found in backtest results")
            return pd.DataFrame()

        df = pd.DataFrame(series_dict)
        df = df.sort_index()
        df = df.fillna(0.0)

        logger.info(
            "Built PnL matrix from backtest: %d days x %d strategies",
            len(df), len(df.columns),
        )
        return df

    def from_live_trades(
        self,
        trades_db_path: str | Path,
        table_name: str = "trades",
        strategy_col: str = "strategy",
        pnl_col: str = "realized_pnl",
        date_col: str = "close_time",
    ) -> pd.DataFrame:
        """Build PnL matrix from a SQLite trades database.

        Expects a table with at minimum: strategy name, realized PnL, and
        a close timestamp. Groups by strategy and date to produce daily PnL.

        Args:
            trades_db_path: Path to the SQLite database.
            table_name: Name of the trades table.
            strategy_col: Column containing strategy names.
            pnl_col: Column containing realized PnL per trade.
            date_col: Column containing the trade close timestamp.

        Returns:
            DataFrame: index=dates, columns=strategy_names, values=daily_pnl.
        """
        db_path = Path(trades_db_path)
        if not db_path.exists():
            logger.error("Trades database not found: %s", db_path)
            return pd.DataFrame()

        try:
            conn = sqlite3.connect(str(db_path))
            query = f"""
                SELECT {strategy_col}, {date_col}, {pnl_col}
                FROM {table_name}
                WHERE {pnl_col} IS NOT NULL
                ORDER BY {date_col}
            """
            df = pd.read_sql_query(query, conn)
            conn.close()
        except Exception as e:
            logger.error("Failed to read trades database: %s", e)
            return pd.DataFrame()

        if df.empty:
            logger.warning("No trades found in database")
            return pd.DataFrame()

        # Parse dates and group by strategy + date
        df[date_col] = pd.to_datetime(df[date_col])
        df["trade_date"] = df[date_col].dt.date

        # Pivot: daily PnL per strategy
        daily_pnl = df.groupby(["trade_date", strategy_col])[pnl_col].sum().unstack(
            fill_value=0.0
        )
        daily_pnl.index = pd.to_datetime(daily_pnl.index)
        daily_pnl = daily_pnl.sort_index()

        logger.info(
            "Built PnL matrix from live trades: %d days x %d strategies",
            len(daily_pnl), len(daily_pnl.columns),
        )
        return daily_pnl

    def from_jsonl_events(
        self,
        events_path: str | Path,
        strategy_key: str = "strategy",
        pnl_key: str = "pnl",
        timestamp_key: str = "timestamp",
    ) -> pd.DataFrame:
        """Build PnL matrix from a JSONL event file.

        Each line is a JSON object representing a trade/event. We extract
        strategy name, PnL, and timestamp, then aggregate to daily.

        Args:
            events_path: Path to the JSONL file.
            strategy_key: JSON key for strategy name.
            pnl_key: JSON key for PnL value.
            timestamp_key: JSON key for timestamp (ISO format or epoch seconds).

        Returns:
            DataFrame: index=dates, columns=strategy_names, values=daily_pnl.
        """
        events_path = Path(events_path)
        if not events_path.exists():
            logger.error("Events file not found: %s", events_path)
            return pd.DataFrame()

        records: List[dict] = []

        try:
            with open(events_path, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        strategy = event.get(strategy_key)
                        pnl = event.get(pnl_key)
                        ts = event.get(timestamp_key)

                        if strategy is None or pnl is None or ts is None:
                            continue

                        # Parse timestamp
                        if isinstance(ts, (int, float)):
                            dt = datetime.fromtimestamp(ts)
                        else:
                            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

                        records.append({
                            "strategy": strategy,
                            "pnl": float(pnl),
                            "date": dt.date(),
                        })
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.debug("Skipping line %d: %s", line_num, e)
                        continue
        except Exception as e:
            logger.error("Failed to read JSONL events: %s", e)
            return pd.DataFrame()

        if not records:
            logger.warning("No valid events found in JSONL file")
            return pd.DataFrame()

        df = pd.DataFrame(records)

        # Aggregate by date + strategy
        daily_pnl = df.groupby(["date", "strategy"])["pnl"].sum().unstack(
            fill_value=0.0
        )
        daily_pnl.index = pd.to_datetime(daily_pnl.index)
        daily_pnl = daily_pnl.sort_index()

        logger.info(
            "Built PnL matrix from JSONL: %d days x %d strategies",
            len(daily_pnl), len(daily_pnl.columns),
        )
        return daily_pnl

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_pnl_series(
        result: Any,
        pnl_column: str,
        date_column: str,
    ) -> pd.Series | None:
        """Extract a PnL Series from various result formats."""

        # Already a Series
        if isinstance(result, pd.Series):
            return result

        # DataFrame: look for pnl column
        if isinstance(result, pd.DataFrame):
            if pnl_column in result.columns:
                s = result[pnl_column]
                if date_column in result.columns:
                    s.index = pd.to_datetime(result[date_column])
                return s
            # If the DataFrame has a DatetimeIndex and a single numeric column
            if len(result.columns) == 1:
                return result.iloc[:, 0]
            return None

        # Object with attributes
        if hasattr(result, "daily_pnl"):
            attr = result.daily_pnl
            if isinstance(attr, pd.Series):
                return attr
            if callable(attr):
                return attr()

        if hasattr(result, "equity_curve"):
            attr = result.equity_curve
            if isinstance(attr, pd.Series):
                # Convert equity curve to daily PnL via diff
                return attr.diff().dropna()
            if callable(attr):
                curve = attr()
                if isinstance(curve, pd.Series):
                    return curve.diff().dropna()

        # Dict with expected keys
        if isinstance(result, dict):
            if pnl_column in result:
                val = result[pnl_column]
                if isinstance(val, pd.Series):
                    return val
                if isinstance(val, list):
                    return pd.Series(val)

        return None
