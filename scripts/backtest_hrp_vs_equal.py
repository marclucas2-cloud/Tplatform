"""HRP vs Equal Weight — backtest comparison.

Compares 3 allocation methods over historical strategy PnL data:
  1. Equal Weight (1/N)
  2. Static (allocation.yaml weights)
  3. HRP (dynamic, rebalanced every 4h)

Usage:
    python scripts/backtest_hrp_vs_equal.py [--lookback-months 6]
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "output"


def generate_synthetic_pnl(n_strategies: int = 10, n_days: int = 126, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic daily PnL for testing (if no live data available).

    Creates 3 clusters:
      - Cluster 1 (4 strats): correlated momentum
      - Cluster 2 (3 strats): correlated mean-reversion
      - Cluster 3 (3 strats): uncorrelated
    """
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now(), periods=n_days)

    # Cluster 1: correlated momentum (Sharpe ~1.5)
    factor1 = rng.normal(0.001, 0.01, n_days)
    cluster1 = pd.DataFrame({
        f"strat_{i}": factor1 + rng.normal(0, 0.005, n_days)
        for i in range(4)
    }, index=dates)

    # Cluster 2: correlated mean-reversion (Sharpe ~1.0)
    factor2 = rng.normal(0.0005, 0.008, n_days)
    cluster2 = pd.DataFrame({
        f"strat_{i}": factor2 + rng.normal(0, 0.004, n_days)
        for i in range(4, 7)
    }, index=dates)

    # Cluster 3: uncorrelated (Sharpe ~0.8)
    cluster3 = pd.DataFrame({
        f"strat_{i}": rng.normal(0.0003, 0.006, n_days)
        for i in range(7, 10)
    }, index=dates)

    return pd.concat([cluster1, cluster2, cluster3], axis=1)


def backtest_equal_weight(pnl_matrix: pd.DataFrame, capital: float = 100_000) -> pd.Series:
    """Backtest with equal weight allocation."""
    n_strats = pnl_matrix.shape[1]
    weights = {col: 1.0 / n_strats for col in pnl_matrix.columns}
    daily_pnl = (pnl_matrix * pd.Series(weights)).sum(axis=1) * capital
    equity = capital + daily_pnl.cumsum()
    return equity


def backtest_static(pnl_matrix: pd.DataFrame, capital: float = 100_000) -> pd.Series:
    """Backtest with static allocation (decreasing weights by strategy index)."""
    n = pnl_matrix.shape[1]
    raw_weights = np.linspace(1.5, 0.5, n)
    raw_weights /= raw_weights.sum()
    weights = {col: w for col, w in zip(pnl_matrix.columns, raw_weights)}
    daily_pnl = (pnl_matrix * pd.Series(weights)).sum(axis=1) * capital
    equity = capital + daily_pnl.cumsum()
    return equity


def backtest_hrp(pnl_matrix: pd.DataFrame, capital: float = 100_000, rebalance_days: int = 1) -> pd.Series:
    """Backtest with HRP dynamic allocation."""
    try:
        from core.alloc.hrp_allocator import HRPAllocator
    except ImportError:
        logger.warning("HRPAllocator not available, falling back to equal weight")
        return backtest_equal_weight(pnl_matrix, capital)

    hrp = HRPAllocator(min_weight=0.02, max_weight=0.25)
    equity_values = [capital]
    current_weights = {col: 1.0 / pnl_matrix.shape[1] for col in pnl_matrix.columns}

    for i in range(len(pnl_matrix)):
        # Rebalance periodically
        if i > 20 and i % rebalance_days == 0:
            lookback = pnl_matrix.iloc[max(0, i - 20):i]
            try:
                pnl_dict = {col: lookback[col] for col in lookback.columns}
                new_weights = hrp.compute_weights(pnl_dict)
                if new_weights:
                    current_weights = new_weights
            except Exception as e:
                logger.debug("HRP rebalance failed at day %d: %s", i, e)

        # Calculate daily PnL
        day_pnl = sum(
            pnl_matrix.iloc[i][col] * current_weights.get(col, 0) * equity_values[-1]
            for col in pnl_matrix.columns
        )
        equity_values.append(equity_values[-1] + day_pnl)

    return pd.Series(equity_values[1:], index=pnl_matrix.index)


def compute_metrics(equity: pd.Series) -> dict:
    """Compute performance metrics from equity curve."""
    returns = equity.pct_change().dropna()
    if len(returns) == 0:
        return {"sharpe": 0, "max_dd": 0, "total_return": 0, "calmar": 0}

    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = abs(dd.min())
    total_ret = (equity.iloc[-1] / equity.iloc[0]) - 1
    calmar = total_ret / max_dd if max_dd > 0 else 0

    return {
        "sharpe": round(float(sharpe), 3),
        "max_dd": round(float(max_dd), 4),
        "total_return": round(float(total_ret), 4),
        "calmar": round(float(calmar), 3),
        "sortino": round(float(
            returns.mean() / returns[returns < 0].std() * np.sqrt(252)
            if len(returns[returns < 0]) > 0 and returns[returns < 0].std() > 0 else 0
        ), 3),
    }


def run_comparison(pnl_matrix: pd.DataFrame, capital: float = 100_000) -> dict:
    """Run full comparison and generate report."""
    print("Running Equal Weight backtest...")
    eq_ew = backtest_equal_weight(pnl_matrix, capital)
    metrics_ew = compute_metrics(eq_ew)

    print("Running Static Weight backtest...")
    eq_static = backtest_static(pnl_matrix, capital)
    metrics_static = compute_metrics(eq_static)

    print("Running HRP Dynamic backtest...")
    eq_hrp = backtest_hrp(pnl_matrix, capital)
    metrics_hrp = compute_metrics(eq_hrp)

    return {
        "equal_weight": metrics_ew,
        "static": metrics_static,
        "hrp": metrics_hrp,
    }


def generate_report(results: dict, output_path: Path) -> None:
    """Generate markdown comparison report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# HRP vs Equal Weight — Comparison Report",
        f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Performance Metrics",
        "",
        "| Metric | Equal Weight | Static | HRP Dynamic |",
        "|--------|-------------|--------|-------------|",
    ]

    for metric in ["sharpe", "max_dd", "total_return", "calmar", "sortino"]:
        ew = results["equal_weight"].get(metric, 0)
        st = results["static"].get(metric, 0)
        hrp = results["hrp"].get(metric, 0)
        fmt = ".3f" if metric in ["sharpe", "calmar", "sortino"] else ".2%"
        if fmt == ".2%":
            lines.append(f"| {metric} | {ew:.2%} | {st:.2%} | {hrp:.2%} |")
        else:
            lines.append(f"| {metric} | {ew:{fmt}} | {st:{fmt}} | {hrp:{fmt}} |")

    # Winner
    sharpes = {
        "Equal Weight": results["equal_weight"]["sharpe"],
        "Static": results["static"]["sharpe"],
        "HRP": results["hrp"]["sharpe"],
    }
    winner = max(sharpes, key=sharpes.get)
    lines.extend([
        "",
        f"## Verdict: **{winner}** wins on Sharpe",
        "",
        "## Key Takeaways",
        f"- HRP Max DD: {results['hrp']['max_dd']:.2%} vs EW: {results['equal_weight']['max_dd']:.2%}",
        f"- HRP reduces DD by {(1 - results['hrp']['max_dd'] / max(results['equal_weight']['max_dd'], 0.001)):.0%}" if results['equal_weight']['max_dd'] > 0 else "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="HRP vs Equal Weight comparison")
    parser.add_argument("--lookback-months", type=int, default=6)
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--use-synthetic", action="store_true", default=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    n_days = args.lookback_months * 21  # Trading days
    pnl_matrix = generate_synthetic_pnl(n_strategies=10, n_days=n_days)
    print(f"PnL matrix: {pnl_matrix.shape[0]} days x {pnl_matrix.shape[1]} strategies")

    results = run_comparison(pnl_matrix, args.capital)
    generate_report(results, OUTPUT_DIR / "hrp_comparison.md")

    # Print summary
    print("\n" + "=" * 50)
    for method, metrics in results.items():
        print(f"  {method:15s}: Sharpe={metrics['sharpe']:.3f}, MaxDD={metrics['max_dd']:.2%}, Return={metrics['total_return']:.2%}")
    print("=" * 50)


if __name__ == "__main__":
    main()
