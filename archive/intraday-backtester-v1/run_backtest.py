"""
Point d'entrée principal — lance toutes les stratégies et génère le rapport.

Usage :
    python run_backtest.py                    # Toutes les stratégies
    python run_backtest.py --strategy orb     # Une seule stratégie
    python run_backtest.py --days 90          # Période personnalisée
"""
import argparse
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

import config
from data_fetcher import fetch_bars, fetch_multiple
from backtest_engine import BacktestEngine
from strategies import (
    ORB5MinStrategy,
    VWAPBounceStrategy,
    GapFadeStrategy,
    CorrelationBreakdownStrategy,
    PowerHourStrategy,
    MeanReversionStrategy,
    FOMCDriftStrategy,
    OpExGammaPinStrategy,
    TickImbalanceStrategy,
    DarkPoolBlockStrategy,
    VolumeProfileClusterStrategy,
    CrossAssetLeadLagStrategy,
    PatternRecognitionStrategy,
    EarningsDriftStrategy,
)
from utils.metrics import calculate_metrics, print_metrics
from utils.plotting import (
    plot_equity_curve,
    plot_strategy_comparison,
    plot_weekday_performance,
    plot_trade_distribution,
)


STRATEGY_MAP = {
    # Classiques
    "orb": ORB5MinStrategy,
    "vwap": VWAPBounceStrategy,
    "gap": GapFadeStrategy,
    "pairs": CorrelationBreakdownStrategy,
    "power": PowerHourStrategy,
    "meanrev": MeanReversionStrategy,
    # Macro / Event-Driven
    "fomc": FOMCDriftStrategy,
    "opex": OpExGammaPinStrategy,
    "earnings": EarningsDriftStrategy,
    # Microstructure
    "tickimb": TickImbalanceStrategy,
    "darkpool": DarkPoolBlockStrategy,
    # AI/ML
    "mlcluster": VolumeProfileClusterStrategy,
    "pattern": PatternRecognitionStrategy,
    # Cross-Asset
    "crossasset": CrossAssetLeadLagStrategy,
}


def run_strategy(strategy_class, data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Lance un backtest pour une stratégie et retourne (trades_df, metrics)."""
    strategy = strategy_class()
    engine = BacktestEngine(strategy)
    trades = engine.run(data)
    metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)
    print_metrics(strategy.name, metrics)
    return trades, metrics


def main():
    parser = argparse.ArgumentParser(description="Intraday Strategy Backtester")
    parser.add_argument("--strategy", type=str, default="all",
                        choices=["all"] + list(STRATEGY_MAP.keys()),
                        help="Strategy to backtest")
    parser.add_argument("--days", type=int, default=config.BACKTEST_DAYS,
                        help="Number of days to backtest")
    parser.add_argument("--timeframe", type=str, default="5Min",
                        choices=["1Min", "5Min"],
                        help="Bar timeframe")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force re-download data")
    args = parser.parse_args()

    # Setup
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    print("=" * 60)
    print("  INTRADAY BACKTESTER")
    print("=" * 60)
    print(f"  Capital:    ${config.INITIAL_CAPITAL:,.0f}")
    print(f"  Period:     {args.days} days")
    print(f"  Timeframe:  {args.timeframe}")
    print(f"  Strategy:   {args.strategy}")
    print("=" * 60)

    # ── Fetch data ──
    print("\n[DATA] Fetching market data from Alpaca...")
    start = datetime.now() - timedelta(days=args.days)
    end = datetime.now()

    data = fetch_multiple(
        config.ALL_TICKERS,
        timeframe=args.timeframe,
        start=start,
        end=end,
    )

    if not data:
        print("[ERROR] No data fetched. Check your Alpaca API keys.")
        print("  Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables.")
        sys.exit(1)

    print(f"\n[DATA] Loaded {len(data)} tickers, "
          f"{sum(len(df) for df in data.values()):,} total bars")

    # ── Run strategies ──
    strategies_to_run = (
        list(STRATEGY_MAP.values()) if args.strategy == "all"
        else [STRATEGY_MAP[args.strategy]]
    )

    all_results = {}
    all_trades = {}

    for strat_class in strategies_to_run:
        trades, metrics = run_strategy(strat_class, data)
        name = strat_class().name
        all_results[name] = metrics
        all_trades[name] = trades

        # Sauvegarder les trades en CSV
        if not trades.empty:
            csv_path = os.path.join(config.OUTPUT_DIR, f"trades_{name.lower().replace(' ', '_')}.csv")
            trades.to_csv(csv_path, index=False)
            print(f"  [CSV] Saved: {csv_path}")

            # Plots individuels
            plot_weekday_performance(trades, name)
            plot_trade_distribution(trades, name)

    # ── Comparaison multi-stratégies ──
    if len(all_results) > 1:
        print("\n" + "=" * 60)
        print("  MULTI-STRATEGY COMPARISON")
        print("=" * 60)

        # Equity curves
        equity_curves = {}
        for name, metrics in all_results.items():
            if not metrics["equity_curve"].empty:
                equity_curves[name] = metrics["equity_curve"]

        if equity_curves:
            plot_equity_curve(equity_curves, "All Strategies — Equity Curves")

        # Bar comparison
        plot_strategy_comparison(all_results)

        # Ranking
        print("\n  RANKING BY SHARPE RATIO:")
        ranked = sorted(all_results.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True)
        for i, (name, m) in enumerate(ranked, 1):
            print(f"    {i}. {name:30s} Sharpe={m['sharpe_ratio']:>6.2f}  "
                  f"Return={m['total_return_pct']:>7.2f}%  "
                  f"WinRate={m['win_rate']:>5.1f}%  "
                  f"DD={m['max_drawdown_pct']:>5.2f}%")

        # Summary CSV
        summary = pd.DataFrame([
            {"strategy": name, **{k: v for k, v in m.items()
                                   if k not in ["equity_curve", "by_weekday", "by_ticker"]}}
            for name, m in all_results.items()
        ])
        summary_path = os.path.join(config.OUTPUT_DIR, "strategy_summary.csv")
        summary.to_csv(summary_path, index=False)
        print(f"\n  [CSV] Summary saved: {summary_path}")

    print("\n[DONE] All results saved to:", config.OUTPUT_DIR)


if __name__ == "__main__":
    main()
