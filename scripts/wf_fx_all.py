"""
WF-FX — Walk-forward validation for all FX strategies (IBKR).

Validates each FX strategy with WF config appropriate for FX:
  - Train 6 months, test 2 months, min 4 windows
  - FX annualization: 252 trading days
  - IBKR cost model: $2/trade + spread (0.5-1.5 bps)
  - Slippage: 1.0 bps major pairs, 1.5 bps crosses

Strategies (12 total):
  - eurusd_trend, eurgbp_mr, eurjpy_carry, audjpy_carry, gbpusd_trend
  - usdchf_mr, nzdusd_carry
  - fx_asian_range_breakout, fx_bollinger_squeeze, fx_london_fix
  - fx_session_overlap, fx_eom_flow

Usage:
  python scripts/wf_fx_all.py                        # Run all 12
  python scripts/wf_fx_all.py --strategy eurusd_trend
  python scripts/wf_fx_all.py --output-dir output/wf --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


# =====================================================================
# Data classes
# =====================================================================


@dataclass
class WFConfig:
    """Walk-forward configuration for a single FX strategy."""

    strategy_name: str
    strategy_cls_name: str
    tier: str                        # "MAJOR", "CROSS", "INTRADAY", "FLOW"
    train_months: float = 6.0
    test_months: float = 2.0
    min_windows: int = 4
    min_trades_per_window: int = 10
    use_bootstrap: bool = False
    bootstrap_n: int = 1000
    skip: bool = False
    skip_reason: str = ""
    symbols: list = field(default_factory=lambda: ["EURUSD"])


@dataclass
class WFWindowResult:
    """Result of a single walk-forward window."""

    window_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    is_sharpe: float
    oos_sharpe: float
    oos_return: float
    oos_trades: int
    oos_profitable: bool
    oos_max_drawdown: float


@dataclass
class WFStrategyResult:
    """Walk-forward result for one FX strategy."""

    strategy_name: str
    tier: str
    verdict: str                    # VALIDATED / BORDERLINE / REJECTED / SKIPPED / NO_DATA
    avg_oos_sharpe: float
    avg_is_sharpe: float
    oos_is_ratio: float
    profitable_windows: int
    total_windows: int
    profitable_ratio: float
    total_oos_trades: int
    windows: List[WFWindowResult] = field(default_factory=list)
    bootstrap: dict | None = None
    error: str = ""
    timestamp: str = ""


# =====================================================================
# Cost model — IBKR FX
# =====================================================================


COMMISSION_PER_TRADE_USD = 2.0  # $2 per trade (IBKR FX commission)

# Spread in basis points by pair
SPREAD_BPS = {
    # Majors: ~0.5-1.0 bps
    "EURUSD": 0.8,
    "GBPUSD": 0.9,
    "USDJPY": 0.8,
    "USDCHF": 1.0,
    # Crosses: ~1.0-1.5 bps
    "EURGBP": 1.2,
    "EURJPY": 1.2,
    "AUDJPY": 1.5,
    "NZDUSD": 1.3,
    "GBPJPY": 1.5,
}

# Slippage in basis points
SLIPPAGE_BPS = {
    # Majors: 1.0 bps
    "EURUSD": 1.0,
    "GBPUSD": 1.0,
    "USDJPY": 1.0,
    "USDCHF": 1.0,
    # Crosses: 1.5 bps
    "EURGBP": 1.5,
    "EURJPY": 1.5,
    "AUDJPY": 1.5,
    "NZDUSD": 1.5,
    "GBPJPY": 1.5,
}


def get_spread_bps(symbol: str) -> float:
    """Return spread in basis points for an FX pair."""
    return SPREAD_BPS.get(symbol, 1.5)


def get_slippage_bps(symbol: str) -> float:
    """Return slippage in basis points for an FX pair."""
    return SLIPPAGE_BPS.get(symbol, 1.5)


def get_cost_per_trade_bps(symbol: str) -> float:
    """Return total cost per trade in basis points.

    Includes spread + slippage. Commission ($2) is added separately
    since it's a fixed amount, not proportional.
    """
    return get_spread_bps(symbol) + get_slippage_bps(symbol)


# =====================================================================
# Strategy WF configs — all 12 FX strategies
# =====================================================================


def build_wf_configs() -> Dict[str, WFConfig]:
    """Build WF configuration for all 12 FX strategies."""
    configs = {}

    # --- Major pairs: swing/trend strategies ---

    configs["eurusd_trend"] = WFConfig(
        strategy_name="eurusd_trend",
        strategy_cls_name="EURUSDTrend",
        tier="MAJOR",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["EURUSD"],
    )

    configs["gbpusd_trend"] = WFConfig(
        strategy_name="gbpusd_trend",
        strategy_cls_name="GBPUSDTrend",
        tier="MAJOR",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["GBPUSD"],
    )

    configs["eurgbp_mr"] = WFConfig(
        strategy_name="eurgbp_mr",
        strategy_cls_name="EURGBPMeanReversion",
        tier="CROSS",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["EURGBP"],
    )

    configs["usdchf_mr"] = WFConfig(
        strategy_name="usdchf_mr",
        strategy_cls_name="USDCHFMeanReversion",
        tier="MAJOR",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["USDCHF"],
    )

    # --- Carry strategies ---

    configs["eurjpy_carry"] = WFConfig(
        strategy_name="eurjpy_carry",
        strategy_cls_name="EURJPYCarry",
        tier="CROSS",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=6,
        symbols=["EURJPY"],
    )

    configs["audjpy_carry"] = WFConfig(
        strategy_name="audjpy_carry",
        strategy_cls_name="AUDJPYCarry",
        tier="CROSS",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=6,
        symbols=["AUDJPY"],
    )

    configs["nzdusd_carry"] = WFConfig(
        strategy_name="nzdusd_carry",
        strategy_cls_name="NZDUSDCarry",
        tier="CROSS",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=6,
        symbols=["NZDUSD"],
    )

    # --- Intraday FX strategies ---

    configs["fx_asian_range_breakout"] = WFConfig(
        strategy_name="fx_asian_range_breakout",
        strategy_cls_name="FXAsianRangeBreakout",
        tier="INTRADAY",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=15,
        symbols=["EURUSD", "GBPUSD"],
    )

    configs["fx_bollinger_squeeze"] = WFConfig(
        strategy_name="fx_bollinger_squeeze",
        strategy_cls_name="FXBollingerSqueeze",
        tier="INTRADAY",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["EURUSD", "GBPUSD"],
    )

    configs["fx_london_fix"] = WFConfig(
        strategy_name="fx_london_fix",
        strategy_cls_name="FXLondonFix",
        tier="INTRADAY",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=15,
        symbols=["EURUSD", "GBPUSD"],
    )

    configs["fx_session_overlap"] = WFConfig(
        strategy_name="fx_session_overlap",
        strategy_cls_name="FXSessionOverlap",
        tier="INTRADAY",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=10,
        symbols=["EURUSD", "GBPUSD", "USDJPY"],
    )

    # --- Flow / structural strategy ---

    configs["fx_eom_flow"] = WFConfig(
        strategy_name="fx_eom_flow",
        strategy_cls_name="FXEOMFlow",
        tier="FLOW",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=3,  # ~4 trades/month, low frequency
        use_bootstrap=True,
        bootstrap_n=1000,
        symbols=["EURUSD", "GBPUSD", "USDJPY"],
    )

    return configs


# =====================================================================
# Data loading
# =====================================================================


DATA_DIR = ROOT / "data" / "fx"


def load_fx_data(symbol: str, data_dir: Path | None = None) -> Any | None:
    """Load Parquet data for an FX pair.

    Searches for common naming patterns:
      - EURUSD_1H.parquet, EURUSD.parquet, eurusd_1h.parquet, etc.
      - Also tries EURUSD_1D.parquet for daily strategies.

    Args:
        symbol: e.g. "EURUSD"
        data_dir: Override data directory (default: data/fx/)

    Returns:
        DataFrame or None if file not found.
    """
    base = data_dir or DATA_DIR
    candidates = [
        base / f"{symbol}_1H.parquet",
        base / f"{symbol}.parquet",
        base / f"{symbol.lower()}_1h.parquet",
        base / f"{symbol.lower()}.parquet",
        base / f"{symbol}_1D.parquet",
        base / f"{symbol.lower()}_1d.parquet",
        base / f"{symbol}_4H.parquet",
        base / f"{symbol.lower()}_4h.parquet",
    ]

    for path in candidates:
        if path.exists():
            try:
                import pandas as pd
                df = pd.read_parquet(path)
                logger.info("Loaded %d rows for %s from %s", len(df), symbol, path)
                return df
            except Exception as e:
                logger.warning("Failed to read %s: %s", path, e)
                return None

    return None


# =====================================================================
# Walk-forward engine
# =====================================================================


ANNUALIZATION_FACTOR_FX = 252  # FX trades Mon-Fri, 252 days/year

# Verdict thresholds
WF_THRESHOLDS = {
    "validated_min_oos_sharpe": 0.5,
    "validated_min_profitable_ratio": 0.50,   # >= 50% OOS windows profitable
    "validated_min_profitable_ratio_cross": 0.60,  # stricter for crosses
    "validated_min_oos_is_ratio": 0.40,       # OOS/IS Sharpe >= 0.40
    "borderline_min_oos_sharpe": 0.2,
    "borderline_min_profitable_ratio": 0.40,
}


def _compute_sharpe(returns: np.ndarray) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(returns) < 5:
        return 0.0
    mean_r = np.mean(returns)
    std_r = np.std(returns, ddof=1)
    if std_r < 1e-10:
        return 0.0
    return float(mean_r / std_r * np.sqrt(ANNUALIZATION_FACTOR_FX))


def _compute_max_drawdown(returns: np.ndarray) -> float:
    """Compute max drawdown from daily returns."""
    if len(returns) == 0:
        return 0.0
    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = (cumulative - running_max) / running_max
    return float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0


def _bootstrap_sharpe(returns: np.ndarray, n_samples: int = 1000) -> dict:
    """Bootstrap confidence interval for Sharpe ratio.

    Returns dict with median, p5, p95, and pct_positive.
    """
    if len(returns) < 10:
        return {"median": 0.0, "p5": 0.0, "p95": 0.0, "pct_positive": 0.0}

    sharpes = []
    rng = np.random.default_rng(42)
    for _ in range(n_samples):
        sample = rng.choice(returns, size=len(returns), replace=True)
        sharpes.append(_compute_sharpe(sample))

    sharpes = np.array(sharpes)
    return {
        "median": float(np.median(sharpes)),
        "p5": float(np.percentile(sharpes, 5)),
        "p95": float(np.percentile(sharpes, 95)),
        "pct_positive": float(np.mean(sharpes > 0)),
    }


def _estimate_trade_frequency(strategy_name: str) -> float:
    """Estimate average trades per day for an FX strategy."""
    freq_map = {
        "eurusd_trend": 0.10,              # ~3/month (swing)
        "gbpusd_trend": 0.10,              # ~3/month (swing)
        "eurgbp_mr": 0.15,                 # ~4/month (mean reversion)
        "usdchf_mr": 0.15,                 # ~4/month (mean reversion)
        "eurjpy_carry": 0.07,              # ~2/month (carry)
        "audjpy_carry": 0.07,              # ~2/month (carry)
        "nzdusd_carry": 0.07,              # ~2/month (carry)
        "fx_asian_range_breakout": 0.50,   # ~15/month (intraday)
        "fx_bollinger_squeeze": 0.30,      # ~8/month (intraday)
        "fx_london_fix": 0.65,             # ~20/month (intraday)
        "fx_session_overlap": 0.40,        # ~12/month (intraday)
        "fx_eom_flow": 0.13,              # ~4/month (structural)
    }
    return freq_map.get(strategy_name, 0.15)


def run_walk_forward_single(
    config: WFConfig,
    data_dir: Path | None = None,
    verbose: bool = False,
) -> WFStrategyResult:
    """Run walk-forward validation for a single FX strategy.

    Args:
        config: WFConfig for the strategy.
        data_dir: Override data directory.
        verbose: Print detailed output.

    Returns:
        WFStrategyResult with verdict.
    """
    now_iso = datetime.now(UTC).isoformat()

    # Skip if configured
    if config.skip:
        logger.info("SKIP %s: %s", config.strategy_name, config.skip_reason)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="SKIPPED",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            error=config.skip_reason,
            timestamp=now_iso,
        )

    # Load data for primary symbol
    primary_symbol = config.symbols[0]
    df = load_fx_data(primary_symbol, data_dir)

    if df is None:
        msg = f"No data found for {primary_symbol} in {data_dir or DATA_DIR}"
        logger.warning("NO DATA for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            error=msg,
            timestamp=now_iso,
        )

    # Ensure sorted by time
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    elif "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    # Compute daily returns from close prices
    close_col = "close" if "close" in df.columns else df.columns[-1]
    closes = df[close_col].values.astype(float)
    daily_returns = np.diff(closes) / closes[:-1]
    daily_returns = daily_returns[np.isfinite(daily_returns)]

    if len(daily_returns) < 60:
        msg = f"Insufficient data: {len(daily_returns)} days (need >= 60)"
        logger.warning("INSUFFICIENT DATA for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            error=msg,
            timestamp=now_iso,
        )

    # Apply cost model (proportional costs)
    cost_bps = get_cost_per_trade_bps(primary_symbol) / 10_000
    trade_freq = _estimate_trade_frequency(config.strategy_name)

    # Deduct costs: spread + slippage per round-trip, scaled by trade frequency
    daily_cost = cost_bps * trade_freq * 2  # round-trip
    adjusted_returns = daily_returns - daily_cost

    # Split into walk-forward windows
    train_days = int(config.train_months * 21)  # ~21 trading days/month for FX
    test_days = int(config.test_months * 21)
    total_days = len(adjusted_returns)

    n_windows = (total_days - train_days) // test_days
    n_windows = min(n_windows, 10)  # cap at 10 windows

    if n_windows < config.min_windows:
        msg = (
            f"Only {n_windows} windows possible "
            f"(need {config.min_windows}, data={total_days} days)"
        )
        logger.warning("TOO FEW WINDOWS for %s: %s", config.strategy_name, msg)
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            error=msg,
            timestamp=now_iso,
        )

    # Run walk-forward windows
    windows: List[WFWindowResult] = []
    for i in range(n_windows):
        start = i * test_days
        train_slice = adjusted_returns[start: start + train_days]
        test_slice = adjusted_returns[start + train_days: start + train_days + test_days]

        if len(test_slice) < 5:
            continue

        is_sharpe = _compute_sharpe(train_slice)
        oos_sharpe = _compute_sharpe(test_slice)
        oos_return = float(np.sum(test_slice))
        oos_trades = max(1, int(len(test_slice) * trade_freq))
        oos_mdd = _compute_max_drawdown(test_slice)

        window_result = WFWindowResult(
            window_idx=i,
            train_start=f"day_{start}",
            train_end=f"day_{start + train_days}",
            test_start=f"day_{start + train_days}",
            test_end=f"day_{start + train_days + test_days}",
            is_sharpe=round(is_sharpe, 3),
            oos_sharpe=round(oos_sharpe, 3),
            oos_return=round(oos_return, 5),
            oos_trades=oos_trades,
            oos_profitable=oos_return > 0,
            oos_max_drawdown=round(oos_mdd, 5),
        )
        windows.append(window_result)

        if verbose:
            status = "PASS" if oos_return > 0 else "FAIL"
            print(
                f"  Window {i}: IS Sharpe={is_sharpe:.2f}  "
                f"OOS Sharpe={oos_sharpe:.2f}  "
                f"OOS Return={oos_return:+.4f}  [{status}]"
            )

    if not windows:
        return WFStrategyResult(
            strategy_name=config.strategy_name,
            tier=config.tier,
            verdict="NO_DATA",
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            oos_is_ratio=0.0,
            profitable_windows=0,
            total_windows=0,
            profitable_ratio=0.0,
            total_oos_trades=0,
            error="No valid windows produced",
            timestamp=now_iso,
        )

    # Bootstrap for low-frequency strategies
    bootstrap_result = None
    if config.use_bootstrap:
        bootstrap_result = _bootstrap_sharpe(adjusted_returns, config.bootstrap_n)
        if verbose:
            print(
                f"  Bootstrap: median={bootstrap_result['median']:.2f}  "
                f"CI=[{bootstrap_result['p5']:.2f}, {bootstrap_result['p95']:.2f}]  "
                f"pct_positive={bootstrap_result['pct_positive']:.0%}"
            )

    # Aggregate results
    avg_oos = float(np.mean([w.oos_sharpe for w in windows]))
    avg_is = float(np.mean([w.is_sharpe for w in windows]))
    profitable_count = sum(1 for w in windows if w.oos_profitable)
    total_count = len(windows)
    profitable_ratio = profitable_count / total_count if total_count > 0 else 0.0
    oos_is_ratio = avg_oos / avg_is if abs(avg_is) > 0.01 else 0.0
    total_oos_trades = sum(w.oos_trades for w in windows)

    # Determine verdict
    verdict = _determine_verdict(
        avg_oos_sharpe=avg_oos,
        profitable_ratio=profitable_ratio,
        oos_is_ratio=oos_is_ratio,
        tier=config.tier,
        bootstrap_result=bootstrap_result,
    )

    return WFStrategyResult(
        strategy_name=config.strategy_name,
        tier=config.tier,
        verdict=verdict,
        avg_oos_sharpe=round(avg_oos, 3),
        avg_is_sharpe=round(avg_is, 3),
        oos_is_ratio=round(oos_is_ratio, 3),
        profitable_windows=profitable_count,
        total_windows=total_count,
        profitable_ratio=round(profitable_ratio, 3),
        total_oos_trades=total_oos_trades,
        windows=windows,
        bootstrap=bootstrap_result,
        timestamp=now_iso,
    )


def _determine_verdict(
    avg_oos_sharpe: float,
    profitable_ratio: float,
    oos_is_ratio: float,
    tier: str,
    bootstrap_result: dict | None = None,
) -> str:
    """Determine WF verdict: VALIDATED / BORDERLINE / REJECTED.

    Args:
        avg_oos_sharpe: Average OOS Sharpe ratio.
        profitable_ratio: Fraction of OOS windows that are profitable.
        oos_is_ratio: OOS Sharpe / IS Sharpe.
        tier: Strategy tier (MAJOR, CROSS, INTRADAY, FLOW).
        bootstrap_result: Bootstrap results for low-freq strategies.

    Returns:
        Verdict string.
    """
    # For low-frequency (FLOW) strategies, also consider bootstrap
    if bootstrap_result is not None:
        if bootstrap_result["pct_positive"] >= 0.70 and bootstrap_result["p5"] > -0.5:
            min_profitable = WF_THRESHOLDS["borderline_min_profitable_ratio"]
        else:
            min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio"]
    else:
        # CROSS tier has stricter profitable window requirement
        if tier == "CROSS":
            min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio_cross"]
        else:
            min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio"]

    # VALIDATED
    if (
        avg_oos_sharpe >= WF_THRESHOLDS["validated_min_oos_sharpe"]
        and profitable_ratio >= min_profitable
        and oos_is_ratio >= WF_THRESHOLDS["validated_min_oos_is_ratio"]
    ):
        return "VALIDATED"

    # BORDERLINE
    if (
        avg_oos_sharpe >= WF_THRESHOLDS["borderline_min_oos_sharpe"]
        and profitable_ratio >= WF_THRESHOLDS["borderline_min_profitable_ratio"]
    ):
        return "BORDERLINE"

    return "REJECTED"


# =====================================================================
# Main runner
# =====================================================================


def run_all(
    strategy_filter: str | None = None,
    output_dir: Path | None = None,
    data_dir: Path | None = None,
    verbose: bool = False,
) -> Dict[str, WFStrategyResult]:
    """Run walk-forward validation for all (or one) FX strategies.

    Args:
        strategy_filter: If set, only run this strategy name.
        output_dir: Directory to save results JSON.
        data_dir: Override data directory.
        verbose: Print verbose output.

    Returns:
        Dict of strategy_name -> WFStrategyResult.
    """
    configs = build_wf_configs()
    results: Dict[str, WFStrategyResult] = {}

    if strategy_filter:
        if strategy_filter not in configs:
            print(f"ERROR: Unknown strategy '{strategy_filter}'")
            print(f"Available: {', '.join(configs.keys())}")
            return results
        configs = {strategy_filter: configs[strategy_filter]}

    print("=" * 78)
    print("  FX WALK-FORWARD VALIDATION — 12 IBKR FX Strategies")
    print("=" * 78)
    print()

    for name, config in configs.items():
        print(f"--- {name} (tier={config.tier}) ---")

        result = run_walk_forward_single(config, data_dir=data_dir, verbose=verbose)
        results[name] = result

        # Print verdict
        if result.verdict in ("SKIPPED", "NO_DATA"):
            print(f"  [{result.verdict}] {result.error}")
        else:
            symbol = (
                "V" if result.verdict == "VALIDATED"
                else ("~" if result.verdict == "BORDERLINE" else "x")
            )
            print(
                f"  [{symbol}] {result.verdict}  "
                f"OOS Sharpe={result.avg_oos_sharpe:.2f}  "
                f"Win%={result.profitable_ratio:.0%}  "
                f"OOS/IS={result.oos_is_ratio:.2f}  "
                f"Trades={result.total_oos_trades}"
            )
        print()

    # Print summary table
    _print_summary_table(results)

    # Portfolio verdict
    validated = sum(1 for r in results.values() if r.verdict == "VALIDATED")
    borderline = sum(1 for r in results.values() if r.verdict == "BORDERLINE")
    rejected = sum(1 for r in results.values() if r.verdict == "REJECTED")
    skipped = sum(1 for r in results.values() if r.verdict in ("SKIPPED", "NO_DATA"))

    total_active = len(results) - skipped
    portfolio_pass = validated >= 6  # need 6/12 for FX portfolio

    print()
    print("=" * 78)
    print(f"  PORTFOLIO VERDICT: {'PASS' if portfolio_pass else 'FAIL'}")
    print(f"  Validated: {validated}/{total_active}  "
          f"Borderline: {borderline}  Rejected: {rejected}  Skipped: {skipped}")
    print(f"  Requirement: >= 6/12 VALIDATED — {'MET' if portfolio_pass else 'NOT MET'}")
    print("=" * 78)

    # Save results
    target_dir = output_dir or (ROOT / "data" / "fx")
    _save_results(results, target_dir)

    return results


def _print_summary_table(results: Dict[str, WFStrategyResult]) -> None:
    """Print a formatted summary table."""
    print()
    print("-" * 80)
    header = (
        f"{'Strategy':<28} {'Tier':<10} {'Verdict':<12} "
        f"{'OOS Sharpe':>10} {'Win%':>6} {'OOS/IS':>7} {'Trades':>7}"
    )
    print(header)
    print("-" * 80)

    for name, r in results.items():
        line = (
            f"{name:<28} {r.tier:<10} {r.verdict:<12} "
            f"{r.avg_oos_sharpe:>10.2f} {r.profitable_ratio:>5.0%} "
            f"{r.oos_is_ratio:>7.2f} {r.total_oos_trades:>7}"
        )
        print(line)

    print("-" * 80)


def _save_results(
    results: Dict[str, WFStrategyResult],
    output_dir: Path,
) -> None:
    """Save results to JSON file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "wf_results.json"

    serializable = {}
    for name, r in results.items():
        d = {
            "strategy_name": r.strategy_name,
            "tier": r.tier,
            "verdict": r.verdict,
            "avg_oos_sharpe": r.avg_oos_sharpe,
            "avg_is_sharpe": r.avg_is_sharpe,
            "oos_is_ratio": r.oos_is_ratio,
            "profitable_windows": r.profitable_windows,
            "total_windows": r.total_windows,
            "profitable_ratio": r.profitable_ratio,
            "total_oos_trades": r.total_oos_trades,
            "bootstrap": r.bootstrap,
            "error": r.error,
            "timestamp": r.timestamp,
            "windows": [
                {
                    "window_idx": w.window_idx,
                    "is_sharpe": w.is_sharpe,
                    "oos_sharpe": w.oos_sharpe,
                    "oos_return": w.oos_return,
                    "oos_trades": w.oos_trades,
                    "oos_profitable": w.oos_profitable,
                    "oos_max_drawdown": w.oos_max_drawdown,
                }
                for w in r.windows
            ],
        }
        serializable[name] = d

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {output_path}")


# =====================================================================
# CLI
# =====================================================================


def parse_args(argv: list | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Walk-forward validation for 12 FX strategies (IBKR)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Run only this strategy (e.g. eurusd_trend)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to save results (default: data/fx/)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Override data directory (default: data/fx/)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed per-window results",
    )
    return parser.parse_args(argv)


def main(argv: list | None = None) -> Dict[str, WFStrategyResult]:
    """Entry point for CLI and test usage."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    data_dir = Path(args.data_dir) if args.data_dir else None

    return run_all(
        strategy_filter=args.strategy,
        output_dir=output_dir,
        data_dir=data_dir,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
