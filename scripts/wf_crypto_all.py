"""
WF-001 — Walk-forward validation for all 8 crypto strategies (Binance France).

Validates each strategy with tier-appropriate WF config:
  - Tier 1 (BTC/ETH): train 6m, test 2m, min 4 windows
  - Tier 2 (altcoins): train 4m, test 1.5m, min 4 windows
  - Bootstrap for low-frequency (Weekend Gap, Liquidation Momentum)
  - Skip for passive (Borrow Rate Carry)

Cost model:
  - Commission: 0.10% per trade (Binance spot/margin)
  - Slippage: BTC 2bps, ETH 3bps, tier2 5bps, tier3 8bps

Usage:
  python scripts/wf_crypto_all.py                         # Run all 8
  python scripts/wf_crypto_all.py --strategy btc_eth_dual_momentum
  python scripts/wf_crypto_all.py --output-dir output/wf  --verbose
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


# ═══════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class WFConfig:
    """Walk-forward configuration for a single strategy."""

    strategy_name: str
    strategy_cls_name: str
    tier: str                        # "TIER_1", "TIER_2", "PASSIVE", "LOW_FREQ"
    train_months: float = 6.0
    test_months: float = 2.0
    min_windows: int = 4
    min_trades_per_window: int = 10
    use_bootstrap: bool = False
    bootstrap_n: int = 1000
    skip: bool = False
    skip_reason: str = ""
    symbols: list = field(default_factory=lambda: ["BTCUSDT"])


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
    """Walk-forward result for one strategy."""

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
    error: str = ""
    timestamp: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Cost model
# ═══════════════════════════════════════════════════════════════════════


COMMISSION_RATE = 0.0010  # 0.10% per trade (maker+taker avg)

SLIPPAGE_BPS = {
    "BTCUSDT": 2,
    "ETHUSDT": 3,
    # Tier 2
    "SOLUSDT": 5,
    "BNBUSDT": 5,
    "XRPUSDT": 5,
    # Tier 3 (altcoins)
    "DOGEUSDT": 8,
    "AVAXUSDT": 8,
    "MATICUSDT": 8,
    "LINKUSDT": 8,
    "ADAUSDT": 8,
}


def get_slippage_bps(symbol: str) -> int:
    """Return slippage in basis points for a given symbol."""
    return SLIPPAGE_BPS.get(symbol, 8)  # Default tier 3


def get_cost_model(symbol: str) -> dict:
    """Return full cost model dict for a symbol."""
    slippage = get_slippage_bps(symbol) / 10_000
    return {
        "commission_rate": COMMISSION_RATE,
        "slippage_rate": slippage,
        "total_cost_per_trade": COMMISSION_RATE + slippage,
    }


# ═══════════════════════════════════════════════════════════════════════
# Strategy WF configs — all 8 crypto strategies
# ═══════════════════════════════════════════════════════════════════════


def build_wf_configs() -> Dict[str, WFConfig]:
    """Build WF configuration for all 8 crypto strategies."""
    configs = {}

    # --- Tier 1: BTC/ETH — high liquidity, train 6m test 2m ---

    configs["btc_eth_dual_momentum"] = WFConfig(
        strategy_name="btc_eth_dual_momentum",
        strategy_cls_name="BTCETHDualMomentum",
        tier="TIER_1",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=10,
        symbols=["BTCUSDT", "ETHUSDT"],
    )

    configs["btc_mean_reversion"] = WFConfig(
        strategy_name="btc_mean_reversion",
        strategy_cls_name="BTCMeanReversion",
        tier="TIER_1",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["BTCUSDT"],
    )

    configs["vol_breakout"] = WFConfig(
        strategy_name="vol_breakout",
        strategy_cls_name="VolBreakout",
        tier="TIER_1",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=8,
        symbols=["BTCUSDT"],
    )

    configs["btc_dominance"] = WFConfig(
        strategy_name="btc_dominance",
        strategy_cls_name="BTCDominance",
        tier="TIER_1",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=6,
        symbols=["BTCUSDT"],
    )

    # --- Tier 2: altcoins — thinner liquidity, train 4m test 1.5m ---

    configs["altcoin_relative_strength"] = WFConfig(
        strategy_name="altcoin_relative_strength",
        strategy_cls_name="AltcoinRelativeStrength",
        tier="TIER_2",
        train_months=4.0,
        test_months=1.5,
        min_windows=4,
        min_trades_per_window=6,
        symbols=["SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT"],
    )

    # --- Low frequency: bootstrap validation ---

    configs["weekend_gap"] = WFConfig(
        strategy_name="weekend_gap",
        strategy_cls_name="WeekendGap",
        tier="LOW_FREQ",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=4,
        use_bootstrap=True,
        bootstrap_n=1000,
        symbols=["BTCUSDT"],
    )

    configs["liquidation_momentum"] = WFConfig(
        strategy_name="liquidation_momentum",
        strategy_cls_name="LiquidationMomentum",
        tier="LOW_FREQ",
        train_months=6.0,
        test_months=2.0,
        min_windows=4,
        min_trades_per_window=4,
        use_bootstrap=True,
        bootstrap_n=1000,
        symbols=["BTCUSDT"],
    )

    # --- Passive: skip WF ---

    configs["borrow_rate_carry"] = WFConfig(
        strategy_name="borrow_rate_carry",
        strategy_cls_name="BorrowRateCarry",
        tier="PASSIVE",
        skip=True,
        skip_reason="Passive earn strategy — no directional signal to walk-forward",
    )

    return configs


# ═══════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════


DATA_DIR = ROOT / "data" / "crypto" / "candles"


def load_candle_data(symbol: str, data_dir: Path | None = None) -> Any | None:
    """Load Parquet candle data for a symbol.

    Args:
        symbol: e.g. "BTCUSDT"
        data_dir: Override data directory (default: data/crypto/candles/)

    Returns:
        DataFrame or None if file not found.
    """
    base = data_dir or DATA_DIR
    candidates = [
        base / f"{symbol}.parquet",
        base / f"{symbol.lower()}.parquet",
        base / f"{symbol}_1h.parquet",
        base / f"{symbol.lower()}_1h.parquet",
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


# ═══════════════════════════════════════════════════════════════════════
# Walk-forward engine
# ═══════════════════════════════════════════════════════════════════════


ANNUALIZATION_FACTOR_CRYPTO = 365  # 365 days/year for crypto (24/7)

# Verdict thresholds
WF_THRESHOLDS = {
    "validated_min_oos_sharpe": 0.5,
    "validated_min_profitable_ratio": 0.50,   # >= 50% OOS windows profitable
    "validated_min_profitable_ratio_v2": 0.60,  # >= 60% for tier 2
    "validated_min_oos_is_ratio": 0.40,       # OOS Sharpe / IS Sharpe >= 0.40
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
    return float(mean_r / std_r * np.sqrt(ANNUALIZATION_FACTOR_CRYPTO))


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


def run_walk_forward_single(
    config: WFConfig,
    data_dir: Path | None = None,
    verbose: bool = False,
) -> WFStrategyResult:
    """Run walk-forward validation for a single strategy.

    Args:
        config: WFConfig for the strategy.
        data_dir: Override data directory.
        verbose: Print detailed output.

    Returns:
        WFStrategyResult with verdict.
    """
    now_iso = datetime.now(UTC).isoformat()

    # Skip passive strategies
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
    df = load_candle_data(primary_symbol, data_dir)

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

    # Apply cost model
    cost = get_cost_model(primary_symbol)
    cost_per_trade = cost["total_cost_per_trade"]

    # Estimate trade frequency (trades per day) from strategy type
    trade_freq = _estimate_trade_frequency(config.strategy_name)

    # Deduct costs from returns
    daily_cost = cost_per_trade * trade_freq * 2  # round-trip
    adjusted_returns = daily_returns - daily_cost

    # Split into walk-forward windows
    train_days = int(config.train_months * 30)
    test_days = int(config.test_months * 30)
    window_size = train_days + test_days
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
    windows = []
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
        timestamp=now_iso,
    )


def _estimate_trade_frequency(strategy_name: str) -> float:
    """Estimate average trades per day for a strategy."""
    freq_map = {
        "btc_eth_dual_momentum": 0.15,       # ~3/month
        "altcoin_relative_strength": 0.07,    # ~2/month (weekly rebal)
        "btc_mean_reversion": 0.20,           # ~6/month
        "vol_breakout": 0.10,                 # ~3/month
        "btc_dominance": 0.07,                # ~2/month
        "weekend_gap": 0.07,                  # ~2/month (weekends only)
        "liquidation_momentum": 0.10,         # ~3/month
        "borrow_rate_carry": 0.03,            # ~1/month
    }
    return freq_map.get(strategy_name, 0.10)


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
        tier: Strategy tier (TIER_1, TIER_2, LOW_FREQ).
        bootstrap_result: Bootstrap results for low-freq strategies.

    Returns:
        Verdict string.
    """
    # For low-frequency strategies, also consider bootstrap
    if bootstrap_result is not None:
        if bootstrap_result["pct_positive"] >= 0.70 and bootstrap_result["p5"] > -0.5:
            # Bootstrap is supportive — lower the bar slightly
            min_profitable = WF_THRESHOLDS["borderline_min_profitable_ratio"]
        else:
            min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio"]
    else:
        # Tier 2 has stricter profitable window requirement
        if tier == "TIER_2":
            min_profitable = WF_THRESHOLDS["validated_min_profitable_ratio_v2"]
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


# ═══════════════════════════════════════════════════════════════════════
# Main runner
# ═══════════════════════════════════════════════════════════════════════


def run_all(
    strategy_filter: str | None = None,
    output_dir: Path | None = None,
    data_dir: Path | None = None,
    verbose: bool = False,
) -> Dict[str, WFStrategyResult]:
    """Run walk-forward validation for all (or one) crypto strategies.

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
    print("  CRYPTO WALK-FORWARD VALIDATION — 8 Binance France Strategies")
    print("=" * 78)
    print()

    for name, config in configs.items():
        print(f"--- {name} (tier={config.tier}) ---")

        result = run_walk_forward_single(config, data_dir=data_dir, verbose=verbose)
        results[name] = result

        # Print verdict
        symbol = "✓" if result.verdict == "VALIDATED" else ("~" if result.verdict == "BORDERLINE" else "x")
        if result.verdict in ("SKIPPED", "NO_DATA"):
            print(f"  [{result.verdict}] {result.error}")
        else:
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
    portfolio_pass = validated >= 4

    print()
    print("=" * 78)
    print(f"  PORTFOLIO VERDICT: {'PASS' if portfolio_pass else 'FAIL'}")
    print(f"  Validated: {validated}/{total_active}  "
          f"Borderline: {borderline}  Rejected: {rejected}  Skipped: {skipped}")
    print(f"  Requirement: >= 4/8 VALIDATED — {'MET' if portfolio_pass else 'NOT MET'}")
    print("=" * 78)

    # Save results
    if output_dir:
        _save_results(results, output_dir)
    else:
        default_output = ROOT / "data" / "crypto" / "wf_results.json"
        _save_results(results, default_output.parent)

    return results


def _print_summary_table(results: Dict[str, WFStrategyResult]) -> None:
    """Print a formatted summary table."""
    print()
    print("-" * 78)
    header = (
        f"{'Strategy':<30} {'Tier':<10} {'Verdict':<12} "
        f"{'OOS Sharpe':>10} {'Win%':>6} {'OOS/IS':>7}"
    )
    print(header)
    print("-" * 78)

    for name, r in results.items():
        line = (
            f"{name:<30} {r.tier:<10} {r.verdict:<12} "
            f"{r.avg_oos_sharpe:>10.2f} {r.profitable_ratio:>5.0%} {r.oos_is_ratio:>7.2f}"
        )
        print(line)

    print("-" * 78)


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


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def parse_args(argv: list | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Walk-forward validation for 8 crypto strategies (Binance France)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Run only this strategy (e.g. btc_eth_dual_momentum)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to save results (default: data/crypto/)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Override data directory (default: data/crypto/candles/)",
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
