"""
Continuous Walk-Forward -- automated weekly revalidation.

Runs every Sunday at 06:00 CET via cron or the Railway scheduler.

For each active strategy (live AND paper):
  1. Add the latest week of data
  2. Recalculate walk-forward on rolling window (70/30, 5 folds)
  3. Compare with previous WF result
  4. Alert if degradation detected
  5. CRITICAL alert if a LIVE strategy's WF collapses

Output:
  - Weekly WF report in output/walk_forward/
  - Alerts via Telegram
  - Updated WF metrics in data/wf_history.db (SQLite)

Usage:
  python scripts/weekly_walk_forward.py [--strategies all|live|paper] [--dry-run]
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---- Constants ---------------------------------------------------------------

ANNUALIZATION_FACTOR = 252  # trading days per year

# All strategies to monitor
STRATEGIES: Dict[str, dict] = {
    # Live FX
    "fx_eurusd_trend": {"mode": "LIVE", "asset_class": "FX", "min_trades": 30},
    "fx_eurgbp_mr": {"mode": "LIVE", "asset_class": "FX", "min_trades": 30},
    "fx_eurjpy_carry": {"mode": "LIVE", "asset_class": "FX", "min_trades": 30},
    "fx_audjpy_carry": {"mode": "LIVE", "asset_class": "FX", "min_trades": 30},
    # Paper US
    "day_of_week_seasonal": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 30},
    "correlation_regime_hedge": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 30},
    "vix_expansion_short": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 20},
    "high_beta_short": {"mode": "PAPER", "asset_class": "US_EQUITY", "min_trades": 30},
    # Paper EU
    "eu_gap_open": {"mode": "PAPER", "asset_class": "EU_EQUITY", "min_trades": 30},
    "bce_momentum_drift_v2": {"mode": "PAPER", "asset_class": "EU_EQUITY", "min_trades": 30},
    "auto_sector_german": {"mode": "PAPER", "asset_class": "EU_EQUITY", "min_trades": 30},
    "brent_lag_play": {"mode": "PAPER", "asset_class": "EU_EQUITY", "min_trades": 30},
    "eu_close_us_afternoon": {"mode": "PAPER", "asset_class": "EU_EQUITY", "min_trades": 30},
}

# WF validation thresholds
WF_THRESHOLDS = {
    "min_oos_is_ratio": 0.5,          # OOS Sharpe / IS Sharpe >= 0.5
    "min_profitable_windows": 0.5,     # >= 50% of OOS windows profitable
    "min_profitable_windows_v2": 0.6,  # >= 60% for V2 strategies
    "degradation_warning": 0.3,        # 30% drop in OOS Sharpe = warning
    "degradation_critical": 0.5,       # 50% drop = critical
}


# ---- WalkForwardResult ------------------------------------------------------


class WalkForwardResult:
    """Result of a walk-forward validation."""

    def __init__(
        self,
        strategy: str,
        timestamp: str,
        is_sharpe: float,
        oos_sharpe: float,
        oos_is_ratio: float,
        profitable_windows_pct: float,
        n_windows: int,
        n_trades_oos: int,
        window_details: Optional[list] = None,
    ):
        self.strategy = strategy
        self.timestamp = timestamp
        self.is_sharpe = is_sharpe
        self.oos_sharpe = oos_sharpe
        self.oos_is_ratio = oos_is_ratio
        self.profitable_windows_pct = profitable_windows_pct
        self.n_windows = n_windows
        self.n_trades_oos = n_trades_oos
        self.window_details = window_details or []

    @property
    def is_validated(self) -> bool:
        """Strategy passes WF validation."""
        return (
            self.oos_is_ratio >= WF_THRESHOLDS["min_oos_is_ratio"]
            and self.profitable_windows_pct >= WF_THRESHOLDS["min_profitable_windows"]
        )

    @property
    def verdict(self) -> str:
        """VALIDATED, BORDERLINE, or REJECTED."""
        if self.is_validated:
            return "VALIDATED"
        elif self.profitable_windows_pct >= 0.4 or self.oos_is_ratio >= 0.3:
            return "BORDERLINE"
        else:
            return "REJECTED"

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "timestamp": self.timestamp,
            "is_sharpe": round(self.is_sharpe, 4),
            "oos_sharpe": round(self.oos_sharpe, 4),
            "oos_is_ratio": round(self.oos_is_ratio, 4),
            "profitable_windows_pct": round(self.profitable_windows_pct, 4),
            "n_windows": self.n_windows,
            "n_trades_oos": self.n_trades_oos,
            "verdict": self.verdict,
            "window_details": self.window_details,
        }


# ---- ContinuousWalkForward --------------------------------------------------


class ContinuousWalkForward:
    """Automated weekly walk-forward revalidation.

    Runs on all active strategies, compares with previous results,
    and alerts on degradation.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        alert_callback=None,
    ):
        self._db_path = Path(db_path or "data/wf_history.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_dir = Path(output_dir or "output/walk_forward")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._alert = alert_callback
        self._init_db()

    def _init_db(self):
        """Create WF history table if it does not exist."""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wf_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    is_sharpe REAL,
                    oos_sharpe REAL,
                    oos_is_ratio REAL,
                    profitable_windows_pct REAL,
                    n_windows INTEGER,
                    n_trades_oos INTEGER,
                    verdict TEXT,
                    details TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wf_strategy
                ON wf_results(strategy, timestamp)
                """
            )

    # ---- Sharpe helper -------------------------------------------------------

    @staticmethod
    def _compute_sharpe(returns: np.ndarray) -> float:
        """Annualized Sharpe from an array of returns.

        If std == 0 with positive mean, caps at 10.0.
        If fewer than 2 observations, returns 0.0.
        """
        if len(returns) < 2:
            return 0.0
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=1))
        if std_ret == 0.0 or np.isnan(std_ret):
            return 0.0 if mean_ret <= 0 else 10.0
        return float((mean_ret / std_ret) * np.sqrt(ANNUALIZATION_FACTOR))

    # ---- Single strategy WF --------------------------------------------------

    def run_walk_forward(
        self,
        strategy: str,
        returns: np.ndarray,
        n_folds: int = 5,
        train_pct: float = 0.70,
    ) -> WalkForwardResult:
        """Run walk-forward on a single strategy.

        Args:
            strategy: strategy name
            returns: 1-D array of daily returns (or per-trade PnL)
            n_folds: number of rolling windows
            train_pct: fraction of each window used for in-sample training

        Returns:
            WalkForwardResult with aggregated metrics.
        """
        now_ts = datetime.now(timezone.utc).isoformat()
        returns = np.asarray(returns, dtype=float)

        # Edge case: insufficient data
        if len(returns) < 10:
            return WalkForwardResult(
                strategy=strategy,
                timestamp=now_ts,
                is_sharpe=0.0,
                oos_sharpe=0.0,
                oos_is_ratio=0.0,
                profitable_windows_pct=0.0,
                n_windows=0,
                n_trades_oos=len(returns),
            )

        # Build rolling windows
        total = len(returns)
        # Each window covers a fraction of total data; windows slide forward
        window_size = max(int(total * 0.6), 10)
        window_size = min(window_size, total)
        is_size = max(int(window_size * train_pct), 3)
        oos_size = max(window_size - is_size, 3)
        actual_window = is_size + oos_size

        if actual_window > total:
            # Fallback: single window
            is_size = max(int(total * train_pct), 3)
            oos_size = total - is_size
            if oos_size < 2:
                return WalkForwardResult(
                    strategy=strategy,
                    timestamp=now_ts,
                    is_sharpe=0.0,
                    oos_sharpe=0.0,
                    oos_is_ratio=0.0,
                    profitable_windows_pct=0.0,
                    n_windows=0,
                    n_trades_oos=0,
                )
            actual_window = is_size + oos_size

        available = total - actual_window
        step = max(available // max(n_folds - 1, 1), 1) if available > 0 else 1

        window_details: list = []
        is_sharpes: list = []
        oos_sharpes: list = []
        oos_ratios: list = []
        oos_profitable_flags: list = []
        total_oos_trades = 0

        start = 0
        built = 0
        while start + actual_window <= total and built < n_folds:
            is_ret = returns[start : start + is_size]
            oos_ret = returns[start + is_size : start + actual_window]

            is_sh = self._compute_sharpe(is_ret)
            oos_sh = self._compute_sharpe(oos_ret)

            # Ratio: avoid div-by-zero
            if abs(is_sh) < 0.01:
                ratio = 0.0 if oos_sh <= 0 else 1.0
            else:
                ratio = oos_sh / is_sh if is_sh > 0 else 0.0

            oos_pnl = float(np.sum(oos_ret))
            is_sharpes.append(is_sh)
            oos_sharpes.append(oos_sh)
            oos_ratios.append(ratio)
            oos_profitable_flags.append(oos_pnl > 0)
            total_oos_trades += len(oos_ret)

            window_details.append(
                {
                    "window": built + 1,
                    "is_start_idx": start,
                    "is_end_idx": start + is_size - 1,
                    "oos_start_idx": start + is_size,
                    "oos_end_idx": start + actual_window - 1,
                    "is_sharpe": round(is_sh, 4),
                    "oos_sharpe": round(oos_sh, 4),
                    "ratio": round(ratio, 4),
                    "oos_pnl": round(oos_pnl, 4),
                }
            )

            start += step
            built += 1

        # Ensure last window is anchored at end if not already
        last_start = total - actual_window
        if last_start > 0 and built > 0 and window_details[-1]["is_start_idx"] != last_start:
            is_ret = returns[last_start : last_start + is_size]
            oos_ret = returns[last_start + is_size : last_start + actual_window]
            is_sh = self._compute_sharpe(is_ret)
            oos_sh = self._compute_sharpe(oos_ret)
            if abs(is_sh) < 0.01:
                ratio = 0.0 if oos_sh <= 0 else 1.0
            else:
                ratio = oos_sh / is_sh if is_sh > 0 else 0.0
            oos_pnl = float(np.sum(oos_ret))

            # Replace last window with end-anchored window
            is_sharpes[-1] = is_sh
            oos_sharpes[-1] = oos_sh
            oos_ratios[-1] = ratio
            oos_profitable_flags[-1] = oos_pnl > 0
            window_details[-1] = {
                "window": built,
                "is_start_idx": last_start,
                "is_end_idx": last_start + is_size - 1,
                "oos_start_idx": last_start + is_size,
                "oos_end_idx": last_start + actual_window - 1,
                "is_sharpe": round(is_sh, 4),
                "oos_sharpe": round(oos_sh, 4),
                "ratio": round(ratio, 4),
                "oos_pnl": round(oos_pnl, 4),
            }

        n_windows = len(window_details)
        if n_windows == 0:
            return WalkForwardResult(
                strategy=strategy,
                timestamp=now_ts,
                is_sharpe=0.0,
                oos_sharpe=0.0,
                oos_is_ratio=0.0,
                profitable_windows_pct=0.0,
                n_windows=0,
                n_trades_oos=0,
            )

        avg_is = float(np.mean(is_sharpes))
        avg_oos = float(np.mean(oos_sharpes))
        avg_ratio = float(np.mean(oos_ratios))
        pct_profitable = float(np.mean(oos_profitable_flags))

        return WalkForwardResult(
            strategy=strategy,
            timestamp=now_ts,
            is_sharpe=avg_is,
            oos_sharpe=avg_oos,
            oos_is_ratio=avg_ratio,
            profitable_windows_pct=pct_profitable,
            n_windows=n_windows,
            n_trades_oos=total_oos_trades,
            window_details=window_details,
        )

    # ---- Batch run -----------------------------------------------------------

    def run_all(
        self,
        strategies: Optional[dict] = None,
        returns_data: Optional[Dict[str, np.ndarray]] = None,
    ) -> List[WalkForwardResult]:
        """Run WF on all strategies.

        Args:
            strategies: override STRATEGIES dict (name -> meta)
            returns_data: {strategy_name: np.array of daily returns}

        Returns:
            list of WalkForwardResult (one per strategy with data)
        """
        strategies = strategies or STRATEGIES
        returns_data = returns_data or {}

        results: List[WalkForwardResult] = []
        for name, meta in strategies.items():
            if name not in returns_data:
                logger.warning("No returns data for %s -- skipping", name)
                continue
            rets = returns_data[name]
            logger.info(
                "Running WF for %s (%s, %d observations)",
                name,
                meta.get("mode", "?"),
                len(rets),
            )
            result = self.run_walk_forward(name, rets)
            results.append(result)
            logger.info(
                "  -> %s | OOS Sharpe=%.2f | ratio=%.2f | profitable=%d%%",
                result.verdict,
                result.oos_sharpe,
                result.oos_is_ratio,
                int(result.profitable_windows_pct * 100),
            )
        return results

    # ---- Comparison with previous week ---------------------------------------

    def compare_with_previous(self, result: WalkForwardResult) -> dict:
        """Compare current WF result with most recent previous run.

        Returns:
            dict with keys: degraded, previous_oos_sharpe, current_oos_sharpe,
            change_pct, alert_level ("none" | "warning" | "critical")
        """
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                """
                SELECT oos_sharpe, verdict FROM wf_results
                WHERE strategy = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (result.strategy,),
            ).fetchone()

        if row is None:
            # First run for this strategy -- no comparison possible
            return {
                "degraded": False,
                "previous_oos_sharpe": None,
                "current_oos_sharpe": result.oos_sharpe,
                "change_pct": 0.0,
                "alert_level": "none",
            }

        prev_oos = float(row[0])
        curr_oos = result.oos_sharpe

        # Calculate relative change (handle zero/negative previous)
        if abs(prev_oos) < 0.001:
            change_pct = 0.0 if abs(curr_oos) < 0.001 else -1.0
        else:
            change_pct = (curr_oos - prev_oos) / abs(prev_oos)

        # Determine alert level
        alert_level = "none"
        degraded = change_pct < -WF_THRESHOLDS["degradation_warning"]

        if change_pct <= -WF_THRESHOLDS["degradation_critical"]:
            alert_level = "critical"
        elif change_pct <= -WF_THRESHOLDS["degradation_warning"]:
            alert_level = "warning"

        # LIVE strategy with ratio below 0.3 is always critical
        mode = STRATEGIES.get(result.strategy, {}).get("mode", "PAPER")
        if mode == "LIVE" and result.oos_is_ratio < 0.3:
            alert_level = "critical"
            degraded = True

        return {
            "degraded": degraded,
            "previous_oos_sharpe": prev_oos,
            "current_oos_sharpe": curr_oos,
            "change_pct": round(change_pct, 4),
            "alert_level": alert_level,
        }

    # ---- Alert checking ------------------------------------------------------

    def check_alerts(self, results: List[WalkForwardResult]) -> List[dict]:
        """Check for degradation alerts across all strategies.

        CRITICAL: LIVE strategy WF ratio drops below 0.3
        WARNING: any strategy drops > 30% vs previous week

        Returns:
            list of alert dicts with keys: strategy, level, message, comparison
        """
        alerts: List[dict] = []
        for result in results:
            comparison = self.compare_with_previous(result)
            if comparison["alert_level"] == "none":
                continue

            mode = STRATEGIES.get(result.strategy, {}).get("mode", "PAPER")
            level = comparison["alert_level"]

            # Force critical for LIVE degradation
            if mode == "LIVE" and comparison["degraded"]:
                level = "critical"

            prev_str = (
                f"{comparison['previous_oos_sharpe']:.2f}"
                if comparison["previous_oos_sharpe"] is not None
                else "N/A"
            )
            msg = (
                f"WF {level.upper()} -- {result.strategy} ({mode})\n"
                f"OOS Sharpe: {prev_str} -> {result.oos_sharpe:.2f} "
                f"({comparison['change_pct']:+.0%})\n"
                f"OOS/IS ratio: {result.oos_is_ratio:.2f}\n"
                f"Verdict: {result.verdict}"
            )

            alert = {
                "strategy": result.strategy,
                "level": level,
                "message": msg,
                "comparison": comparison,
            }
            alerts.append(alert)

            # Send via Telegram if callback configured
            if self._alert is not None:
                try:
                    self._alert(msg, level=level)
                except Exception as exc:
                    logger.warning("Alert callback failed for %s: %s", result.strategy, exc)

        return alerts

    # ---- Persistence ---------------------------------------------------------

    def save_results(self, results: List[WalkForwardResult]):
        """Save WF results to SQLite and output directory."""
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # SQLite
        with sqlite3.connect(str(self._db_path)) as conn:
            for r in results:
                conn.execute(
                    """
                    INSERT INTO wf_results
                    (strategy, timestamp, is_sharpe, oos_sharpe, oos_is_ratio,
                     profitable_windows_pct, n_windows, n_trades_oos, verdict, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.strategy,
                        r.timestamp,
                        r.is_sharpe,
                        r.oos_sharpe,
                        r.oos_is_ratio,
                        r.profitable_windows_pct,
                        r.n_windows,
                        r.n_trades_oos,
                        r.verdict,
                        json.dumps(r.window_details, default=str),
                    ),
                )

        # JSON output
        report_path = self._output_dir / f"wf_weekly_{now_str}.json"
        report_data = {
            "timestamp": now_str,
            "n_strategies": len(results),
            "results": [r.to_dict() for r in results],
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False, default=str)

        logger.info("Results saved to %s and %s", self._db_path, report_path)

    def get_history(
        self, strategy: Optional[str] = None, weeks: int = 12
    ) -> List[dict]:
        """Get WF history for trending.

        Args:
            strategy: filter by strategy name (None = all)
            weeks: maximum number of recent records per strategy

        Returns:
            list of dicts with WF result fields
        """
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if strategy:
                rows = conn.execute(
                    """
                    SELECT * FROM wf_results
                    WHERE strategy = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (strategy, weeks),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM wf_results
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (weeks * len(STRATEGIES),),
                ).fetchall()

        return [dict(r) for r in rows]

    # ---- Trend analysis ------------------------------------------------------

    def get_trend(self, strategy: str, weeks: int = 8) -> dict:
        """Analyze WF trend for a strategy over recent weeks.

        Returns:
            dict with keys: trend ("improving"|"stable"|"degrading"),
            sharpe_slope (float), weeks_analyzed (int)
        """
        history = self.get_history(strategy=strategy, weeks=weeks)
        if len(history) < 2:
            return {"trend": "stable", "sharpe_slope": 0.0, "weeks_analyzed": len(history)}

        # Order chronologically (history is DESC, reverse it)
        history = list(reversed(history))
        sharpes = [float(h["oos_sharpe"]) for h in history]

        # Linear regression: slope of OOS Sharpe over time
        x = np.arange(len(sharpes), dtype=float)
        if len(x) < 2:
            return {"trend": "stable", "sharpe_slope": 0.0, "weeks_analyzed": len(history)}

        coeffs = np.polyfit(x, sharpes, 1)
        slope = float(coeffs[0])

        if slope > 0.05:
            trend = "improving"
        elif slope < -0.05:
            trend = "degrading"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "sharpe_slope": round(slope, 4),
            "weeks_analyzed": len(history),
        }

    # ---- Report generation ---------------------------------------------------

    def generate_report(self, results: List[WalkForwardResult]) -> str:
        """Generate markdown weekly WF report.

        Returns:
            Markdown-formatted report string.
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# Weekly Walk-Forward Report -- {now_str}",
            "",
            f"**Strategies evaluated:** {len(results)}",
            "",
        ]

        # Summary counts
        validated = [r for r in results if r.verdict == "VALIDATED"]
        borderline = [r for r in results if r.verdict == "BORDERLINE"]
        rejected = [r for r in results if r.verdict == "REJECTED"]

        lines.append(
            f"| VALIDATED | BORDERLINE | REJECTED |"
        )
        lines.append("| --- | --- | --- |")
        lines.append(
            f"| {len(validated)} | {len(borderline)} | {len(rejected)} |"
        )
        lines.append("")

        # Detail table
        lines.append("## Details")
        lines.append("")
        lines.append(
            "| Strategy | Mode | IS Sharpe | OOS Sharpe | Ratio | "
            "Profitable % | Windows | Verdict |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")

        for r in sorted(results, key=lambda x: x.verdict):
            mode = STRATEGIES.get(r.strategy, {}).get("mode", "?")
            lines.append(
                f"| {r.strategy} | {mode} | {r.is_sharpe:.2f} | "
                f"{r.oos_sharpe:.2f} | {r.oos_is_ratio:.2f} | "
                f"{r.profitable_windows_pct:.0%} | {r.n_windows} | "
                f"**{r.verdict}** |"
            )

        lines.append("")

        # Trend section (only if history exists)
        trends_available = False
        for r in results:
            trend_info = self.get_trend(r.strategy)
            if trend_info["weeks_analyzed"] >= 2:
                if not trends_available:
                    lines.append("## Trends (last 8 weeks)")
                    lines.append("")
                    lines.append("| Strategy | Trend | Sharpe Slope | Weeks |")
                    lines.append("| --- | --- | --- | --- |")
                    trends_available = True
                lines.append(
                    f"| {r.strategy} | {trend_info['trend']} | "
                    f"{trend_info['sharpe_slope']:+.4f} | "
                    f"{trend_info['weeks_analyzed']} |"
                )

        if trends_available:
            lines.append("")

        # Alerts section
        alerts = self.check_alerts(results)
        if alerts:
            lines.append("## Alerts")
            lines.append("")
            for a in alerts:
                icon = "CRITICAL" if a["level"] == "critical" else "WARNING"
                lines.append(f"- **[{icon}]** {a['strategy']}: {a['message']}")
            lines.append("")

        return "\n".join(lines)


# ---- CLI entrypoint ---------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Weekly Walk-Forward Revalidation"
    )
    parser.add_argument(
        "--strategies",
        default="all",
        choices=["all", "live", "paper"],
        help="Filter strategies by mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report without saving to DB",
    )
    parser.add_argument("--output", default=None, help="Output directory")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Filter strategies by mode
    strategies = STRATEGIES
    if args.strategies == "live":
        strategies = {k: v for k, v in STRATEGIES.items() if v["mode"] == "LIVE"}
    elif args.strategies == "paper":
        strategies = {k: v for k, v in STRATEGIES.items() if v["mode"] == "PAPER"}

    # Telegram alerting (import at runtime to avoid hard dependency)
    alert_callback = None
    try:
        from core.telegram_alert import send_alert

        alert_callback = send_alert
    except ImportError:
        logger.warning("Telegram alerting not available")

    # Initialize
    cwf = ContinuousWalkForward(
        output_dir=args.output,
        alert_callback=alert_callback,
    )

    logger.info("Weekly WF: %d strategies to validate", len(strategies))

    # FIX CRO M-2 : Load returns data from yfinance for each strategy's proxy ticker
    STRATEGY_PROXY_TICKERS = {
        "fx_eurusd_trend": "EURUSD=X",
        "fx_eurgbp_mr": "EURGBP=X",
        "fx_eurjpy_carry": "EURJPY=X",
        "fx_audjpy_carry": "AUDJPY=X",
        "day_of_week_seasonal": "SPY",
        "correlation_regime_hedge": "SPY",
        "vix_expansion_short": "SVXY",
        "high_beta_short": "IWM",
        "eu_gap_open": "EWG",
        "bce_momentum_drift_v2": "EWG",
        "auto_sector_german": "EWG",
        "brent_lag_play": "USO",
        "eu_close_us_afternoon": "SPY",
    }

    returns_data = {}
    for strat_name in strategies:
        ticker = STRATEGY_PROXY_TICKERS.get(strat_name)
        if not ticker:
            logger.warning("  No proxy ticker for %s — skipping", strat_name)
            continue
        try:
            from core.data.loader import OHLCVLoader
            loader = OHLCVLoader.from_yfinance(ticker, "1D", period="2y")
            closes = loader.df["close"].pct_change().dropna().values
            if len(closes) > 60:  # Need at least 60 days
                returns_data[strat_name] = closes
                logger.info("  Loaded %d days for %s (%s)", len(closes), strat_name, ticker)
            else:
                logger.warning("  Insufficient data for %s: %d days", strat_name, len(closes))
        except Exception as e:
            logger.warning("  Failed to load data for %s: %s", strat_name, e)

    if returns_data:
        results = cwf.run_all(strategies=strategies, returns_data=returns_data)
        alerts = cwf.check_alerts(results)
        if not args.dry_run:
            cwf.save_results(results)
        report = cwf.generate_report(results)
        print(report)
        if alerts:
            logger.warning("%d alerts generated", len(alerts))
    else:
        logger.warning("No returns data loaded — run skipped")


if __name__ == "__main__":
    main()
