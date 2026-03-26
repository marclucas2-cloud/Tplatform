"""
Script dédié pour tester les 4 stratégies V2 (filtres assouplis).
Charge les données depuis le cache et lance les backtests.
"""
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from strategies import (
    InitialBalanceExtensionV2Strategy,
    VolumeClimaxReversalV2Strategy,
    VWAPBounceV2Strategy,
    CorrelationBreakdownV2Strategy,
)
from utils.metrics import calculate_metrics, print_metrics


V2_STRATEGIES = [
    InitialBalanceExtensionV2Strategy,
    VolumeClimaxReversalV2Strategy,
    VWAPBounceV2Strategy,
    CorrelationBreakdownV2Strategy,
]


def load_cached_data() -> dict[str, pd.DataFrame]:
    """Charge les données depuis le cache parquet.
    Si plusieurs fichiers par ticker, prend le plus volumineux (le plus de barres).
    """
    cache_dir = config.CACHE_DIR
    data = {}

    if not os.path.exists(cache_dir):
        print(f"[ERROR] Cache dir not found: {cache_dir}")
        sys.exit(1)

    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    print(f"  Found {len(parquet_files)} parquet files")

    # Grouper les fichiers par ticker (prendre le plus gros fichier)
    ticker_files: dict[str, list[tuple[str, int]]] = {}
    for f in parquet_files:
        # Extraire le ticker : AAPL_5Min_20250325_20260325.parquet -> AAPL
        parts = f.split("_5Min_")
        if len(parts) != 2:
            parts = f.split("_1Min_")
        if len(parts) != 2:
            continue
        ticker = parts[0]
        path = os.path.join(cache_dir, f)
        size = os.path.getsize(path)
        if ticker not in ticker_files:
            ticker_files[ticker] = []
        ticker_files[ticker].append((path, size))

    print(f"  Unique tickers: {len(ticker_files)}")

    # Charger le fichier le plus gros pour chaque ticker
    for ticker, files in ticker_files.items():
        # Trier par taille décroissante, prendre le plus gros
        best_path = max(files, key=lambda x: x[1])[0]
        try:
            df = pd.read_parquet(best_path)
            if not df.empty:
                if not isinstance(df.index, pd.DatetimeIndex):
                    if "timestamp" in df.columns:
                        df = df.set_index("timestamp")
                    elif "datetime" in df.columns:
                        df = df.set_index("datetime")
                    df.index = pd.to_datetime(df.index)

                if df.index.tz is None:
                    df.index = df.index.tz_localize("US/Eastern")
                elif str(df.index.tz) != "US/Eastern":
                    df.index = df.index.tz_convert("US/Eastern")

                data[ticker] = df
        except Exception as e:
            pass

    return data


def main():
    print("=" * 70)
    print("  V2 STRATEGIES BACKTEST")
    print("=" * 70)

    # Charger les données
    print("\n[STEP 1] Loading cached data...")
    data = load_cached_data()

    if not data:
        print("[ERROR] No data loaded. Run fetch first.")
        sys.exit(1)

    total_bars = sum(len(df) for df in data.values())
    print(f"  Loaded: {len(data)} tickers, {total_bars:,} total bars")

    # Vérifier les tickers requis pour la stratégie pairs
    pairs_tickers = ["NVDA", "TSLA", "META", "NFLX", "JPM", "GS", "XOM", "SLB", "AAPL", "AMZN"]
    available_pairs = [t for t in pairs_tickers if t in data]
    print(f"  Pairs tickers available: {available_pairs}")

    # Run les stratégies
    print(f"\n[STEP 2] Running {len(V2_STRATEGIES)} V2 strategies...")
    all_results = {}

    for strat_class in V2_STRATEGIES:
        strategy = strat_class()
        engine = BacktestEngine(strategy)
        trades = engine.run(data)
        metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)
        print_metrics(strategy.name, metrics)
        all_results[strategy.name] = {
            "trades": trades,
            "metrics": metrics,
        }

        if not trades.empty:
            safe_name = strategy.name.lower().replace(' ', '_').replace('/', '_')
            csv_path = os.path.join(config.OUTPUT_DIR, f"trades_{safe_name}.csv")
            trades.to_csv(csv_path, index=False)
            unique_tickers = trades["ticker"].nunique()
            print(f"  > Traded {unique_tickers} unique tickers")
            print(f"  > CSV: {csv_path}")

    # Résumé
    print("\n" + "=" * 70)
    print("  V2 STRATEGIES SUMMARY")
    print("=" * 70)
    print(f"  {'Strategy':<35} {'Trades':>7} {'Return':>8} {'Sharpe':>7} {'WR':>6} {'PF':>6} {'DD':>7}")
    print("  " + "-" * 77)

    for name, result in all_results.items():
        m = result["metrics"]
        status = "PASS" if (m["sharpe_ratio"] > 0.5 and m["profit_factor"] > 1.2 and m["n_trades"] >= 30) else "FAIL"
        print(f"  {name:<35} {m['n_trades']:>7} {m['total_return_pct']:>7.2f}% {m['sharpe_ratio']:>7.2f} {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.2f}%  [{status}]")

    print("=" * 70)


if __name__ == "__main__":
    main()
