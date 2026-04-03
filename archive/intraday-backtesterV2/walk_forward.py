"""
Walk-Forward Validation des stratégies winners.

Divise les données en N fenêtres IS (In-Sample) / OOS (Out-of-Sample).
Chaque fenêtre : 60j IS → 30j OOS.
Une stratégie est validée si elle est profitable sur >= 50% des fenêtres OOS.
"""
import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import config
from data_fetcher import fetch_multiple
from universe import prepare_universe
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics
from strategies import (
    ORB5MinStrategy,
    OpExGammaPinStrategy,
    EarningsDriftStrategy,
    DayOfWeekSeasonalStrategy,
    VolumeProfileClusterStrategy,
)

WINNERS = {
    "ORB 5-Min Breakout": ORB5MinStrategy,
    "OpEx Gamma Pin": OpExGammaPinStrategy,
    "Earnings Drift": EarningsDriftStrategy,
    "Day-of-Week Seasonal": DayOfWeekSeasonalStrategy,
    "ML Volume Cluster": VolumeProfileClusterStrategy,
}

# Walk-forward params
IS_DAYS = 60    # In-sample : 60 jours
OOS_DAYS = 30   # Out-of-sample : 30 jours
STEP_DAYS = 30  # Avance de 30j entre fenêtres


def split_data_by_date(data, start_date, end_date):
    """Filtre le dict de DataFrames pour ne garder que les barres entre start et end."""
    filtered = {}
    for ticker, df in data.items():
        mask = (df.index.date >= start_date) & (df.index.date <= end_date)
        sub = df[mask]
        if not sub.empty:
            filtered[ticker] = sub
    return filtered


def run_walk_forward():
    print("=" * 70)
    print("  WALK-FORWARD VALIDATION")
    print("=" * 70)

    # Charger les données (déjà en cache)
    tickers = prepare_universe(mode="curated")
    start = datetime.now() - timedelta(days=180)
    end = datetime.now()
    data = fetch_multiple(tickers, timeframe="5Min", start=start, end=end, use_cache=True)

    if not data:
        print("[ERROR] No data loaded.")
        return

    # Trouver toutes les dates de trading
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.date)
    all_dates = sorted(all_dates)
    total_days = len(all_dates)
    print(f"\n  Data: {len(data)} tickers, {total_days} trading days")
    print(f"  Window: {IS_DAYS}j IS -> {OOS_DAYS}j OOS, step {STEP_DAYS}j\n")

    # Construire les fenêtres
    windows = []
    i = 0
    while i + IS_DAYS + OOS_DAYS <= total_days:
        is_start = all_dates[i]
        is_end = all_dates[i + IS_DAYS - 1]
        oos_start = all_dates[i + IS_DAYS]
        oos_end = all_dates[min(i + IS_DAYS + OOS_DAYS - 1, total_days - 1)]
        windows.append((is_start, is_end, oos_start, oos_end))
        i += STEP_DAYS

    print(f"  {len(windows)} walk-forward windows\n")

    # Résultats
    results = {}

    for name, strat_class in WINNERS.items():
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

        oos_results = []

        for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            # OOS backtest
            oos_data = split_data_by_date(data, oos_start, oos_end)
            if not oos_data:
                continue

            strategy = strat_class()
            engine = BacktestEngine(strategy, initial_capital=config.INITIAL_CAPITAL)

            # Suppress prints
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            trades = engine.run(oos_data)
            sys.stdout = old_stdout

            metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)

            oos_results.append({
                "window": w_idx + 1,
                "oos_start": str(oos_start),
                "oos_end": str(oos_end),
                "return_pct": metrics["total_return_pct"],
                "sharpe": metrics["sharpe_ratio"],
                "pf": metrics["profit_factor"],
                "trades": metrics["n_trades"],
                "win_rate": metrics["win_rate"],
            })

            status = "+" if metrics["total_return_pct"] > 0 else "-"
            print(f"  {status} W{w_idx+1}: OOS {oos_start} -> {oos_end} | "
                  f"Ret={metrics['total_return_pct']:>6.2f}% | "
                  f"Sharpe={metrics['sharpe_ratio']:>5.2f} | "
                  f"PF={metrics['profit_factor']:>4.2f} | "
                  f"Trades={metrics['n_trades']:>3d} | "
                  f"WR={metrics['win_rate']:>4.1f}%")

        if not oos_results:
            print("  [SKIP] No OOS windows")
            continue

        # Aggreger
        profitable_windows = sum(1 for r in oos_results if r["return_pct"] > 0)
        total_windows = len(oos_results)
        avg_return = np.mean([r["return_pct"] for r in oos_results])
        avg_sharpe = np.mean([r["sharpe"] for r in oos_results])
        avg_pf = np.mean([r["pf"] for r in oos_results])
        total_trades = sum(r["trades"] for r in oos_results)

        hit_rate = profitable_windows / total_windows * 100

        verdict = "VALIDATED" if hit_rate >= 50 and avg_return > 0 else "REJECTED"

        results[name] = {
            "hit_rate": hit_rate,
            "avg_return": avg_return,
            "avg_sharpe": avg_sharpe,
            "avg_pf": avg_pf,
            "total_trades": total_trades,
            "verdict": verdict,
        }

        print(f"\n  Summary: {profitable_windows}/{total_windows} windows profitable ({hit_rate:.0f}%)")
        print(f"  Avg OOS Return: {avg_return:.2f}%")
        print(f"  Avg OOS Sharpe: {avg_sharpe:.2f}")
        print(f"  Avg OOS PF: {avg_pf:.2f}")
        print(f"  Total OOS trades: {total_trades}")
        print(f"  >>> VERDICT: {verdict}")

    # Final summary
    print(f"\n\n{'='*70}")
    print("  WALK-FORWARD RESULTS SUMMARY")
    print(f"{'='*70}")
    validated = []
    for name, r in results.items():
        status = "PASS" if r["verdict"] == "VALIDATED" else "FAIL"
        print(f"  [{status}] {name:30s} "
              f"HitRate={r['hit_rate']:>4.0f}% | "
              f"AvgRet={r['avg_return']:>6.2f}% | "
              f"AvgSharpe={r['avg_sharpe']:>5.2f} | "
              f"Trades={r['total_trades']:>4d}")
        if r["verdict"] == "VALIDATED":
            validated.append(name)

    print(f"\n  VALIDATED for paper trading: {len(validated)}")
    for name in validated:
        print(f"    + {name}")

    # Save results
    results_df = pd.DataFrame([
        {"strategy": name, **r} for name, r in results.items()
    ])
    output_path = os.path.join(config.OUTPUT_DIR, "walk_forward_results.csv")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    results_df.to_csv(output_path, index=False)
    print(f"\n  [CSV] {output_path}")

    return validated


if __name__ == "__main__":
    validated = run_walk_forward()
