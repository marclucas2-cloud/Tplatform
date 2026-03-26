"""
Script final : backtest + walk-forward des 4 strategies V2.
Top 200 tickers par volume.
"""
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import config
from backtest_engine import BacktestEngine
from strategies import (
    InitialBalanceExtensionV2Strategy,
    VolumeClimaxReversalV2Strategy,
    VWAPBounceV2Strategy,
    CorrelationBreakdownV2Strategy,
)
from utils.metrics import calculate_metrics, print_metrics


def load_cached_data(max_tickers: int = 200) -> dict[str, pd.DataFrame]:
    """Charge les top N tickers par volume depuis le cache."""
    cache_dir = config.CACHE_DIR
    if not os.path.exists(cache_dir):
        print(f"[ERROR] Cache dir not found: {cache_dir}")
        sys.exit(1)

    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    ticker_files = {}
    for f in parquet_files:
        parts = f.split("_5Min_")
        if len(parts) != 2:
            continue
        ticker = parts[0]
        path = os.path.join(cache_dir, f)
        size = os.path.getsize(path)
        if ticker not in ticker_files:
            ticker_files[ticker] = []
        ticker_files[ticker].append((path, size))

    # Top N par taille de fichier (proxy pour volume/liquidite)
    sorted_tickers = sorted(
        ticker_files.keys(),
        key=lambda t: max(s for _, s in ticker_files[t]),
        reverse=True,
    )[:max_tickers]

    # Forcer l'inclusion des tickers requis pour pairs
    required = ["NVDA", "AMD", "META", "NFLX", "JPM", "GS", "XOM", "SLB",
                 "AAPL", "MSFT", "GOOG", "GOOGL", "SPY"]
    for t in required:
        if t not in sorted_tickers and t in ticker_files:
            sorted_tickers.append(t)

    data = {}
    for ticker in sorted_tickers:
        if ticker in ticker_files:
            best_path = max(ticker_files[ticker], key=lambda x: x[1])[0]
            try:
                df = pd.read_parquet(best_path)
                if not df.empty:
                    data[ticker] = df
            except Exception:
                pass

    return data


def walk_forward(strategy_class, data: dict[str, pd.DataFrame],
                 n_folds: int = 5) -> dict:
    """Walk-forward validation : split les jours en N folds."""
    # Collecter toutes les dates
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.date)
    all_dates = sorted(all_dates)
    n_days = len(all_dates)

    fold_size = n_days // n_folds
    fold_results = []

    print(f"\n  [WALK-FORWARD] {n_folds} folds, {fold_size} days/fold")

    for fold in range(n_folds):
        start_idx = fold * fold_size
        end_idx = (fold + 1) * fold_size if fold < n_folds - 1 else n_days
        fold_dates = set(all_dates[start_idx:end_idx])

        # Filtrer les donnees pour ce fold
        fold_data = {}
        for ticker, df in data.items():
            mask = pd.Series(df.index.date, index=df.index).isin(fold_dates)
            fold_df = df[mask.values]
            if not fold_df.empty:
                fold_data[ticker] = fold_df

        if not fold_data:
            continue

        strategy = strategy_class()
        engine = BacktestEngine(strategy)
        trades = engine.run(fold_data)
        metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)

        fold_results.append({
            "fold": fold + 1,
            "days": end_idx - start_idx,
            "trades": metrics["n_trades"],
            "return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe_ratio"],
            "win_rate": metrics["win_rate"],
            "pf": metrics["profit_factor"],
            "dd": metrics["max_drawdown_pct"],
        })

        print(f"    Fold {fold+1}: {metrics['n_trades']:>4d} trades, "
              f"Ret={metrics['total_return_pct']:>6.2f}%, "
              f"Sharpe={metrics['sharpe_ratio']:>6.2f}, "
              f"WR={metrics['win_rate']:>5.1f}%, "
              f"PF={metrics['profit_factor']:>5.2f}")

    # Stabilite
    if fold_results:
        returns = [f["return_pct"] for f in fold_results]
        profitable_folds = sum(1 for r in returns if r > 0)
        avg_return = np.mean(returns)
        std_return = np.std(returns)

        print(f"  [WALK-FORWARD SUMMARY]")
        print(f"    Profitable folds: {profitable_folds}/{n_folds}")
        print(f"    Avg return: {avg_return:.2f}% +/- {std_return:.2f}%")
        print(f"    Consistency: {'STABLE' if std_return < abs(avg_return) * 2 else 'UNSTABLE'}")

    return {"folds": fold_results}


def main():
    print("=" * 70)
    print("  V2 STRATEGIES — FINAL BACKTEST + WALK-FORWARD")
    print("=" * 70)
    sys.stdout.flush()

    print("\n[STEP 1] Loading cached data (top 200 tickers)...")
    sys.stdout.flush()
    data = load_cached_data(max_tickers=200)

    if not data:
        print("[ERROR] No data loaded.")
        sys.exit(1)

    total_bars = sum(len(df) for df in data.values())
    print(f"  Loaded: {len(data)} tickers, {total_bars:,} total bars")
    sys.stdout.flush()

    # Run backtest complet
    print(f"\n[STEP 2] Full backtest on {len(data)} tickers...")
    sys.stdout.flush()

    strategies = [
        InitialBalanceExtensionV2Strategy,
        VolumeClimaxReversalV2Strategy,
        VWAPBounceV2Strategy,
        CorrelationBreakdownV2Strategy,
    ]

    all_results = {}
    for strat_class in strategies:
        strategy = strat_class()
        engine = BacktestEngine(strategy)
        trades = engine.run(data)
        metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)
        print_metrics(strategy.name, metrics)
        sys.stdout.flush()
        all_results[strategy.name] = {
            "trades": trades,
            "metrics": metrics,
            "class": strat_class,
        }

        if not trades.empty:
            safe_name = strategy.name.lower().replace(' ', '_').replace('/', '_')
            csv_path = os.path.join(config.OUTPUT_DIR, f"trades_{safe_name}.csv")
            trades.to_csv(csv_path, index=False)
            print(f"  > Traded {trades['ticker'].nunique()} unique tickers")
            print(f"  > Exit reasons: {trades['exit_reason'].value_counts().to_dict()}")
            sys.stdout.flush()

    # Walk-forward les strategies avec PF > 0.8
    print(f"\n[STEP 3] Walk-forward validation...")
    sys.stdout.flush()

    wf_results = {}
    for name, result in all_results.items():
        m = result["metrics"]
        if m["n_trades"] >= 30 and m["profit_factor"] >= 0.80:
            print(f"\n  === Walk-forward: {name} ===")
            sys.stdout.flush()
            wf = walk_forward(result["class"], data, n_folds=5)
            wf_results[name] = wf
        else:
            print(f"\n  === SKIP {name}: PF={m['profit_factor']:.2f}, trades={m['n_trades']} ===")
            sys.stdout.flush()

    # Resume final
    print("\n" + "=" * 70)
    print("  FINAL REPORT")
    print("=" * 70)
    print(f"  {'Strategy':<35} {'Trades':>7} {'Return':>8} {'Sharpe':>7} {'WR':>6} {'PF':>6} {'DD':>7}")
    print("  " + "-" * 77)

    for name, result in all_results.items():
        m = result["metrics"]
        passes = m["sharpe_ratio"] > 0.5 and m["profit_factor"] > 1.2 and m["n_trades"] >= 30
        status = "PASS" if passes else "FAIL"
        print(f"  {name:<35} {m['n_trades']:>7} {m['total_return_pct']:>7.2f}% {m['sharpe_ratio']:>7.2f} {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.2f}%  [{status}]")

    print("=" * 70)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
