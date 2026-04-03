"""
Walk-Forward Validation Framework — systématique pour toutes les stratégies.

Méthode : Rolling window avec split IS (In-Sample) / OOS (Out-of-Sample).
Pour chaque fenêtre :
  - Calculer les métriques sur la partie IS
  - Tester sur OOS (out-of-sample)
  - Comparer Sharpe IS vs OOS

Critères de validation :
  - Ratio OOS/IS Sharpe > 0.5
  - >= 50% des fenêtres OOS profitables (60% pour V2)
  - Sharpe OOS > 0 sur la majorité des fenêtres

Usage :
    from core.walk_forward_framework import WalkForwardValidator
    validator = WalkForwardValidator()
    result = validator.validate_strategy("Gold Fear Gauge", trades_df)
    results = validator.validate_all({"Gold Fear": df1, "ORB V2": df2})
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("walk_forward")

# ─── Constants ────────────────────────────────────────────────────────────────

ANNUALIZATION_FACTOR = 252  # Trading days per year
MIN_TRADES_PER_WINDOW = 5  # Minimum trades pour calculer un Sharpe fiable
MIN_TOTAL_TRADES = 15      # Minimum trades global pour valider

# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class WindowResult:
    """Result of one IS/OOS window."""
    window_idx: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_sharpe: float
    oos_sharpe: float
    is_trades: int
    oos_trades: int
    oos_pnl: float
    oos_return_pct: float
    ratio: float  # OOS Sharpe / IS Sharpe

    def to_dict(self) -> dict:
        return {
            "window": self.window_idx,
            "is_start": self.is_start,
            "is_end": self.is_end,
            "oos_start": self.oos_start,
            "oos_end": self.oos_end,
            "is_sharpe": round(self.is_sharpe, 4),
            "oos_sharpe": round(self.oos_sharpe, 4),
            "is_trades": self.is_trades,
            "oos_trades": self.oos_trades,
            "oos_pnl": round(self.oos_pnl, 2),
            "oos_return_pct": round(self.oos_return_pct, 4),
            "ratio": round(self.ratio, 4),
        }


@dataclass
class ValidationResult:
    """Full walk-forward validation result for a strategy."""
    strategy: str
    n_trades: int
    n_windows: int
    windows: list[WindowResult] = field(default_factory=list)
    avg_oos_sharpe: float = 0.0
    avg_is_sharpe: float = 0.0
    avg_ratio: float = 0.0
    pct_oos_profitable: float = 0.0
    pct_oos_sharpe_positive: float = 0.0
    verdict: str = "REJECTED"
    reason: str = ""
    data_source: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "n_trades": self.n_trades,
            "n_windows": self.n_windows,
            "windows": [w.to_dict() for w in self.windows],
            "avg_oos_sharpe": round(self.avg_oos_sharpe, 4),
            "avg_is_sharpe": round(self.avg_is_sharpe, 4),
            "avg_ratio": round(self.avg_ratio, 4),
            "pct_oos_profitable": round(self.pct_oos_profitable, 4),
            "pct_oos_sharpe_positive": round(self.pct_oos_sharpe_positive, 4),
            "verdict": self.verdict,
            "reason": self.reason,
            "data_source": self.data_source,
        }


# ─── Core validator ──────────────────────────────────────────────────────────


class WalkForwardValidator:
    """Walk-forward systématique pour toutes les stratégies.

    Méthode : Train IS / Test OOS, rolling window.
    Pour chaque fenêtre :
      - Calculer les métriques sur IS (in-sample)
      - Tester sur OOS (out-of-sample)
      - Comparer Sharpe IS vs OOS

    Critères de validation :
      - Ratio OOS/IS Sharpe > 0.5
      - >= 50% des fenêtres OOS profitables
      - Sharpe OOS moyen > 0
    """

    def __init__(
        self,
        is_ratio: float = 0.70,
        n_windows: int = 5,
        min_trades_per_window: int = MIN_TRADES_PER_WINDOW,
        min_total_trades: int = MIN_TOTAL_TRADES,
        initial_capital: float = 100_000.0,
        v2_threshold: bool = False,
    ):
        """
        Args:
            is_ratio: fraction of data for in-sample (0.70 = 70% IS, 30% OOS)
            n_windows: number of rolling windows
            min_trades_per_window: minimum trades in a window to be valid
            min_total_trades: minimum total trades to run validation
            initial_capital: capital de base pour calcul de return %
            v2_threshold: si True, applique le seuil V2 (60% fenêtres OOS profitable)
        """
        self.is_ratio = is_ratio
        self.n_windows = n_windows
        self.min_trades_per_window = min_trades_per_window
        self.min_total_trades = min_total_trades
        self.initial_capital = initial_capital
        self.pct_profitable_threshold = 0.60 if v2_threshold else 0.50
        self.ratio_threshold = 0.50

    @staticmethod
    def _compute_sharpe(pnl_series: pd.Series) -> float:
        """Compute annualized Sharpe ratio from a series of per-trade PnL."""
        if len(pnl_series) < 2:
            return 0.0
        mean_pnl = pnl_series.mean()
        std_pnl = pnl_series.std(ddof=1)
        if std_pnl == 0 or np.isnan(std_pnl):
            return 0.0 if mean_pnl <= 0 else 10.0  # Cap extreme Sharpe
        raw_sharpe = mean_pnl / std_pnl
        # Annualize: assume ~1 trade/day average, scale by sqrt(252)
        return float(raw_sharpe * np.sqrt(ANNUALIZATION_FACTOR))

    @staticmethod
    def _compute_daily_sharpe(daily_pnl: pd.Series) -> float:
        """Compute annualized Sharpe from daily PnL series."""
        if len(daily_pnl) < 2:
            return 0.0
        mean_daily = daily_pnl.mean()
        std_daily = daily_pnl.std(ddof=1)
        if std_daily == 0 or np.isnan(std_daily):
            return 0.0 if mean_daily <= 0 else 10.0
        return float((mean_daily / std_daily) * np.sqrt(ANNUALIZATION_FACTOR))

    def _prepare_trades(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        """Normalize trades DataFrame: ensure date column, sort by date."""
        df = trades_df.copy()

        # Normalize column names (lowercase)
        df.columns = [c.lower().strip() for c in df.columns]

        # Ensure 'date' column exists
        if "date" not in df.columns:
            if "entry_time" in df.columns:
                df["date"] = pd.to_datetime(df["entry_time"]).dt.date
            elif "exit_time" in df.columns:
                df["date"] = pd.to_datetime(df["exit_time"]).dt.date
            else:
                raise ValueError("No 'date', 'entry_time' or 'exit_time' column found")
        else:
            df["date"] = pd.to_datetime(df["date"]).dt.date

        # Ensure 'net_pnl' or 'pnl' column
        if "net_pnl" not in df.columns:
            if "pnl" in df.columns and "commission" in df.columns:
                df["net_pnl"] = df["pnl"] - df["commission"]
            elif "pnl" in df.columns:
                df["net_pnl"] = df["pnl"]
            else:
                raise ValueError("No 'net_pnl' or 'pnl' column found")

        # Ensure numeric
        df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)

        # Sort by date
        df = df.sort_values("date").reset_index(drop=True)

        return df

    def _build_windows(self, dates: list) -> list[tuple[list, list]]:
        """Build rolling IS/OOS date windows.

        Uses anchored walk-forward: each window uses a portion of dates for IS
        and the next portion for OOS, then rolls forward.

        For n_windows rolling windows, we size each window smaller than the
        total data so they can overlap and roll. The IS/OOS split within each
        window respects self.is_ratio.

        Returns list of (is_dates, oos_dates) tuples.
        """
        n_dates = len(dates)
        if n_dates < 10:
            return []

        desired_windows = self.n_windows

        # Minimum sizes for IS and OOS portions
        min_is = 5
        min_oos = 3
        min_window = min_is + min_oos  # 8

        # If data is very small, use a single window split
        if n_dates < min_window + desired_windows - 1:
            is_size = max(int(n_dates * self.is_ratio), min_is)
            oos_dates = dates[is_size:]
            if len(oos_dates) < min_oos:
                return []
            return [(dates[:is_size], oos_dates)]

        # Compute window size to allow desired_windows rolling windows.
        # With step S and window W: start positions are 0, S, 2S, ...
        # Last window starts at (n-1)*S, needs (n-1)*S + W <= n_dates
        # So W = n_dates - (desired_windows - 1) * S
        # We want to maximize W while keeping enough step overlap.
        # Strategy: set step = max(oos portion of window, 1 day)
        #   window_size = n_dates * fraction, step = window_size * (1 - is_ratio)

        # Target: each window covers ~60% of total data for good statistics
        target_window_frac = min(0.60, 1.0 / (1.0 + (desired_windows - 1) * 0.15))
        window_size = max(int(n_dates * target_window_frac), min_window)
        window_size = min(window_size, n_dates)  # Can't exceed total

        is_size = max(int(window_size * self.is_ratio), min_is)
        oos_size = max(window_size - is_size, min_oos)
        total_window = is_size + oos_size

        if total_window > n_dates:
            # Fallback: single window
            is_size = max(int(n_dates * self.is_ratio), min_is)
            oos_dates = dates[is_size:]
            if len(oos_dates) < min_oos:
                return []
            return [(dates[:is_size], oos_dates)]

        # Compute step to distribute windows evenly
        available = n_dates - total_window
        if available <= 0:
            # Only one window fits
            return [(dates[:is_size], dates[is_size:is_size + oos_size])]

        step = max(available // max(desired_windows - 1, 1), 1)

        windows = []
        start = 0
        while start + total_window <= n_dates and len(windows) < desired_windows:
            w_is = dates[start : start + is_size]
            w_oos = dates[start + is_size : start + is_size + oos_size]
            windows.append((w_is, w_oos))
            start += step

        # Ensure the last window is anchored at the end of the data
        last_start = n_dates - total_window
        if last_start > 0 and windows:
            last_is = dates[last_start : last_start + is_size]
            last_oos = dates[last_start + is_size : last_start + is_size + oos_size]
            if windows[-1][0][0] != last_is[0]:
                if len(windows) >= desired_windows:
                    windows[-1] = (last_is, last_oos)
                else:
                    windows.append((last_is, last_oos))

        return windows

    def validate_strategy(
        self,
        strategy_name: str,
        trades_df: pd.DataFrame,
        data_source: str = "",
    ) -> ValidationResult:
        """Run walk-forward validation on a single strategy.

        Args:
            strategy_name: nom de la stratégie
            trades_df: DataFrame avec colonnes [date, net_pnl] minimum
            data_source: path du fichier source (pour traçabilité)

        Returns:
            ValidationResult with verdict VALIDATED / BORDERLINE / REJECTED
        """
        result = ValidationResult(
            strategy=strategy_name,
            n_trades=0,
            n_windows=0,
            data_source=data_source,
        )

        # Prepare data
        try:
            df = self._prepare_trades(trades_df)
        except (ValueError, KeyError) as e:
            result.verdict = "REJECTED"
            result.reason = f"Data error: {e}"
            return result

        n_trades = len(df)
        result.n_trades = n_trades

        # Check minimum trades
        if n_trades < self.min_total_trades:
            result.verdict = "REJECTED"
            result.reason = (
                f"Insufficient trades: {n_trades} < {self.min_total_trades} minimum"
            )
            return result

        # Get unique dates and build daily PnL
        daily_pnl = df.groupby("date")["net_pnl"].sum()
        unique_dates = sorted(daily_pnl.index.tolist())

        # Build windows
        windows = self._build_windows(unique_dates)
        if not windows:
            result.verdict = "REJECTED"
            result.reason = (
                f"Cannot build windows: only {len(unique_dates)} trading days"
            )
            return result

        result.n_windows = len(windows)

        # Evaluate each window
        window_results: list[WindowResult] = []

        for w_idx, (is_dates, oos_dates) in enumerate(windows):
            is_set = set(is_dates)
            oos_set = set(oos_dates)

            is_trades = df[df["date"].isin(is_set)]
            oos_trades = df[df["date"].isin(oos_set)]

            is_pnl = is_trades["net_pnl"]
            oos_pnl = oos_trades["net_pnl"]

            # Daily PnL for Sharpe calculation (more robust)
            is_daily = daily_pnl[daily_pnl.index.isin(is_set)]
            oos_daily = daily_pnl[daily_pnl.index.isin(oos_set)]

            # Compute Sharpe ratios (daily-based for robustness)
            is_sharpe = self._compute_daily_sharpe(is_daily) if len(is_daily) >= 2 else 0.0
            oos_sharpe = self._compute_daily_sharpe(oos_daily) if len(oos_daily) >= 2 else 0.0

            # Compute ratio (handle edge cases)
            if abs(is_sharpe) < 0.01:
                # IS Sharpe ~0: can't compute meaningful ratio
                ratio = 0.0 if oos_sharpe <= 0 else 1.0
            else:
                ratio = oos_sharpe / is_sharpe if is_sharpe > 0 else 0.0

            total_oos_pnl = float(oos_pnl.sum()) if len(oos_pnl) > 0 else 0.0
            oos_return_pct = (total_oos_pnl / self.initial_capital) * 100

            wr = WindowResult(
                window_idx=w_idx + 1,
                is_start=str(is_dates[0]),
                is_end=str(is_dates[-1]),
                oos_start=str(oos_dates[0]),
                oos_end=str(oos_dates[-1]),
                is_sharpe=is_sharpe,
                oos_sharpe=oos_sharpe,
                is_trades=len(is_trades),
                oos_trades=len(oos_trades),
                oos_pnl=total_oos_pnl,
                oos_return_pct=oos_return_pct,
                ratio=ratio,
            )
            window_results.append(wr)

        result.windows = window_results

        # Aggregate metrics — adapt min trades threshold for low-frequency strategies
        effective_min_trades = self.min_trades_per_window
        low_data_flag = False
        if n_trades < 30:
            # Low-frequency strategy: relax per-window min to 2 trades
            effective_min_trades = 2
            low_data_flag = True

        valid_windows = [w for w in window_results if w.oos_trades >= effective_min_trades]

        if not valid_windows:
            result.verdict = "REJECTED"
            result.reason = (
                f"No window has >= {effective_min_trades} OOS trades "
                f"(low_data={low_data_flag}, total={n_trades})"
            )
            return result

        result.avg_oos_sharpe = float(np.mean([w.oos_sharpe for w in valid_windows]))
        result.avg_is_sharpe = float(np.mean([w.is_sharpe for w in valid_windows]))
        result.avg_ratio = float(np.mean([w.ratio for w in valid_windows]))

        n_profitable = sum(1 for w in valid_windows if w.oos_pnl > 0)
        n_sharpe_pos = sum(1 for w in valid_windows if w.oos_sharpe > 0)
        n_valid = len(valid_windows)

        result.pct_oos_profitable = n_profitable / n_valid
        result.pct_oos_sharpe_positive = n_sharpe_pos / n_valid

        # ─── Verdict logic ───────────────────────────────────────────────
        reasons = []

        # Criterion 1: OOS/IS ratio > 0.5
        ratio_ok = result.avg_ratio >= self.ratio_threshold
        if not ratio_ok:
            reasons.append(
                f"avg OOS/IS ratio {result.avg_ratio:.2f} < {self.ratio_threshold}"
            )

        # Criterion 2: >= 50% (or 60% for V2) windows OOS profitable
        pct_ok = result.pct_oos_profitable >= self.pct_profitable_threshold
        if not pct_ok:
            reasons.append(
                f"OOS profitable {result.pct_oos_profitable:.0%} < "
                f"{self.pct_profitable_threshold:.0%} threshold"
            )

        # Criterion 3: avg OOS Sharpe > 0
        sharpe_ok = result.avg_oos_sharpe > 0
        if not sharpe_ok:
            reasons.append(f"avg OOS Sharpe {result.avg_oos_sharpe:.2f} <= 0")

        low_data_note = " [LOW_DATA: <30 trades, results less reliable]" if low_data_flag else ""

        if ratio_ok and pct_ok and sharpe_ok:
            result.verdict = "VALIDATED"
            result.reason = (
                f"All criteria met: ratio={result.avg_ratio:.2f}, "
                f"profitable={result.pct_oos_profitable:.0%}, "
                f"OOS Sharpe={result.avg_oos_sharpe:.2f}{low_data_note}"
            )
        elif (pct_ok and sharpe_ok) or (ratio_ok and sharpe_ok):
            result.verdict = "BORDERLINE"
            result.reason = "Partial pass: " + "; ".join(reasons) + low_data_note
        else:
            result.verdict = "REJECTED"
            result.reason = ("; ".join(reasons) if reasons else "Failed all criteria") + low_data_note

        return result

    def validate_all(
        self,
        strategies_trades: dict[str, pd.DataFrame],
        data_sources: dict[str, str] | None = None,
    ) -> dict[str, ValidationResult]:
        """Validate all strategies.

        Args:
            strategies_trades: dict {strategy_name: trades_df}
            data_sources: optional dict {strategy_name: file_path}

        Returns:
            dict {strategy_name: ValidationResult}
        """
        if data_sources is None:
            data_sources = {}

        results = {}
        for name, trades_df in strategies_trades.items():
            source = data_sources.get(name, "")
            logger.info("Validating: %s (%d trades)", name, len(trades_df))
            result = self.validate_strategy(name, trades_df, data_source=source)
            results[name] = result
            logger.info(
                "  -> %s | %d windows | avg OOS Sharpe=%.2f | ratio=%.2f | %s",
                result.verdict,
                result.n_windows,
                result.avg_oos_sharpe,
                result.avg_ratio,
                result.reason,
            )

        return results


# ─── Runner helper ────────────────────────────────────────────────────────────


def load_trades_csv(path: str | Path) -> pd.DataFrame:
    """Load a trades CSV into a DataFrame, handling various formats."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Trades file not found: {path}")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Empty CSV: {path}")
    return df


def run_full_validation(
    output_dir: str | Path = "output/session_20260326",
    backtester_output_dir: str | Path = "archive/intraday-backtesterV2/output",
    save_path: str | Path = "output/walk_forward_results.json",
    initial_capital: float = 100_000.0,
) -> dict:
    """Run walk-forward validation on ALL US pipeline strategies.

    Maps each pipeline strategy to its trades CSV, runs validation,
    and saves results.

    Returns:
        dict with full results
    """
    base = Path(__file__).parent.parent
    output_dir = base / output_dir
    bt_output = base / backtester_output_dir
    save_path = base / save_path

    # ─── Strategy-to-CSV mapping ─────────────────────────────────────────
    # Pipeline strategies from paper_portfolio.py STRATEGIES dict
    strategy_csv_map: dict[str, list[tuple[str, Path]]] = {
        # === Daily / Monthly ===
        "Momentum 25 ETFs": [
            # No intraday trades CSV — monthly strategy
        ],
        "Pairs MU/AMAT": [
            # No intraday trades CSV — daily strategy
        ],
        "VRP SVXY/SPY/TLT": [
            # No intraday trades CSV — monthly strategy
        ],
        # === Intraday strategies ===
        "OpEx Gamma Pin": [
            ("session", output_dir / "trades_opex_weekly.csv"),
            ("backtester", bt_output / "trades_opex_gamma_pin.csv"),
            ("backtester", bt_output / "trades_opex_weekly_expansion.csv"),
        ],
        "Overnight Gap Continuation": [
            ("backtester", bt_output / "trades_overnight_gap_continuation.csv"),
        ],
        "Day-of-Week Seasonal": [
            ("backtester", bt_output / "trades_day-of-week_seasonal.csv"),
        ],
        "Late Day Mean Reversion": [
            ("backtester", bt_output / "trades_late_day_mean_reversion.csv"),
        ],
        "Crypto-Proxy Regime V2": [
            ("backtester", bt_output / "trades_crypto-proxy_regime_switch.csv"),
            ("session", output_dir / "trades_overnight_crypto.csv"),
        ],
        "ORB 5-Min V2": [
            ("backtester", bt_output / "trades_orb_5-min_breakout.csv"),
        ],
        "Mean Reversion V2": [
            ("backtester", bt_output / "trades_mean_reversion_bb_rsi.csv"),
            ("backtester", bt_output / "trades_mean_reversion_bb+rsi.csv"),
        ],
        "VWAP Micro-Deviation": [
            ("session", output_dir / "trades_vwap_micro_crypto.csv"),
            ("backtester", bt_output / "trades_vwap_micro_deviation.csv"),
            ("backtester", bt_output / "trades_vwap_micro_crypto.csv"),
        ],
        "Triple EMA Pullback": [
            ("backtester", bt_output / "trades_triple_ema_pullback.csv"),
        ],
        "Gold Fear Gauge": [
            ("session", output_dir / "trades_gold_fear.csv"),
            ("backtester", bt_output / "trades_gold_fear_gauge.csv"),
        ],
        "Correlation Regime Hedge": [
            ("session", output_dir / "trades_corr_hedge.csv"),
            ("backtester", bt_output / "trades_correlation_regime_hedge.csv"),
        ],
        "VIX Expansion Short": [
            ("session", output_dir / "trades_short_vix_short.csv"),
            ("backtester", bt_output / "trades_vix_expansion_short.csv"),
        ],
        "Failed Rally Short": [
            ("session", output_dir / "trades_short_failed_rally.csv"),
            ("backtester", bt_output / "trades_failed_rally_short.csv"),
        ],
        "Crypto Bear Cascade": [
            ("session", output_dir / "trades_short_crypto_bear.csv"),
            ("backtester", bt_output / "trades_crypto_bear_cascade.csv"),
        ],
        "EOD Sell Pressure V2": [
            ("session", output_dir / "trades_short_v2_eod_sell_v2.csv"),
            ("backtester", bt_output / "trades_eod_sell_pressure.csv"),
        ],
        "High-Beta Underperformance Short": [
            ("session", output_dir / "trades_short_p0_high_beta_underperf.csv"),
            ("backtester", bt_output / "trades_high_beta_underperf.csv"),
        ],
    }

    # ─── Load trades ─────────────────────────────────────────────────────
    strategies_trades: dict[str, pd.DataFrame] = {}
    data_sources: dict[str, str] = {}
    missing: list[str] = []

    for strat_name, candidates in strategy_csv_map.items():
        loaded = False
        for source_type, path in candidates:
            if path.exists():
                try:
                    df = load_trades_csv(path)
                    strategies_trades[strat_name] = df
                    data_sources[strat_name] = str(path)
                    loaded = True
                    break
                except (ValueError, pd.errors.EmptyDataError):
                    continue
        if not loaded:
            if not candidates:
                missing.append(f"{strat_name} (daily/monthly — no intraday CSV)")
            else:
                missing.append(f"{strat_name} (CSV not found)")

    # ─── Run validation ──────────────────────────────────────────────────
    validator = WalkForwardValidator(
        is_ratio=0.70,
        n_windows=5,
        initial_capital=initial_capital,
    )

    results = validator.validate_all(strategies_trades, data_sources)

    # ─── Build output ────────────────────────────────────────────────────
    output = {
        "meta": {
            "timestamp": datetime.now().isoformat(),
            "n_strategies_total": len(strategy_csv_map),
            "n_strategies_validated": sum(
                1 for r in results.values() if r.verdict == "VALIDATED"
            ),
            "n_strategies_borderline": sum(
                1 for r in results.values() if r.verdict == "BORDERLINE"
            ),
            "n_strategies_rejected": sum(
                1 for r in results.values() if r.verdict == "REJECTED"
            ),
            "n_strategies_missing_data": len(missing),
            "parameters": {
                "is_ratio": 0.70,
                "n_windows": 5,
                "min_trades_per_window": MIN_TRADES_PER_WINDOW,
                "min_total_trades": MIN_TOTAL_TRADES,
                "initial_capital": initial_capital,
                "pct_profitable_threshold": 0.50,
                "ratio_threshold": 0.50,
            },
        },
        "results": {name: r.to_dict() for name, r in results.items()},
        "missing_data": missing,
        "summary": {},
    }

    # Summary table
    summary = {}
    for name, r in results.items():
        summary[name] = {
            "verdict": r.verdict,
            "n_trades": r.n_trades,
            "n_windows": r.n_windows,
            "avg_oos_sharpe": round(r.avg_oos_sharpe, 2),
            "avg_is_sharpe": round(r.avg_is_sharpe, 2),
            "avg_ratio": round(r.avg_ratio, 2),
            "pct_oos_profitable": f"{r.pct_oos_profitable:.0%}",
            "reason": r.reason,
        }
    output["summary"] = summary

    # ─── Save results ────────────────────────────────────────────────────
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    # ─── Print report ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  WALK-FORWARD VALIDATION — FULL PIPELINE REPORT")
    print("=" * 78)

    for name, r in sorted(results.items(), key=lambda x: x[1].verdict):
        icon = {
            "VALIDATED": "PASS",
            "BORDERLINE": "WARN",
            "REJECTED": "FAIL",
        }.get(r.verdict, "????")
        print(
            f"  [{icon}] {name:40s} "
            f"Sharpe IS={r.avg_is_sharpe:>6.2f} OOS={r.avg_oos_sharpe:>6.2f} | "
            f"Ratio={r.avg_ratio:>5.2f} | "
            f"Profitable={r.pct_oos_profitable:>4.0%} | "
            f"Trades={r.n_trades:>4d}"
        )

    if missing:
        print(f"\n  DONNÉES MANQUANTES ({len(missing)}):")
        for m in missing:
            print(f"    - {m}")

    validated = [n for n, r in results.items() if r.verdict == "VALIDATED"]
    borderline = [n for n, r in results.items() if r.verdict == "BORDERLINE"]
    rejected = [n for n, r in results.items() if r.verdict == "REJECTED"]

    print(f"\n  VALIDATED: {len(validated)} | BORDERLINE: {len(borderline)} | "
          f"REJECTED: {len(rejected)} | MISSING: {len(missing)}")

    if validated:
        print("\n  Stratégies VALIDÉES pour paper trading :")
        for n in validated:
            print(f"    + {n}")

    if borderline:
        print("\n  Stratégies BORDERLINE (à surveiller) :")
        for n in borderline:
            print(f"    ~ {n}")

    if rejected:
        print("\n  Stratégies REJETÉES (overfitting probable) :")
        for n in rejected:
            r = results[n]
            print(f"    - {n}: {r.reason}")

    print(f"\n  [JSON] {save_path}")
    print("=" * 78)

    return output


# ─── CLI entrypoint ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )
    run_full_validation()
