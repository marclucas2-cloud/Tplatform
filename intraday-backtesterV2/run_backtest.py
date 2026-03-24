"""
Point d'entrée principal — univers dynamique + scanner Stocks in Play.

Usage :
    # Full universe (eligible ~500-1500 tickers)
    python run_backtest.py --universe eligible --strategy all --days 180

    # Quick test (minimal ~50 tickers)
    python run_backtest.py --universe minimal --strategy orb --days 30

    # Curated (top 200 tickers by volume)
    python run_backtest.py --universe curated --strategy all --days 365

    # Specific strategy
    python run_backtest.py --strategy fomc --universe eligible
"""
import argparse
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

import config
from universe import (
    prepare_universe,
    scan_stocks_in_play,
    compute_daily_stats,
    PERMANENT_TICKERS,
    SECTOR_MAP,
    SYMPATHY_MAP,
)
from data_fetcher import fetch_multiple
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
    # Nouvelles stratégies (batch 2)
    InitialBalanceExtensionStrategy,
    VolumeClimaxReversalStrategy,
    SectorRotationMomentumStrategy,
    ETFNavPremiumStrategy,
    MomentumExhaustionStrategy,
    CryptoProxyRegimeStrategy,
    MOCImbalanceStrategy,
    OpeningDriveStrategy,
    RelativeStrengthPairsStrategy,
    VWAPSDReversalStrategy,
    DayOfWeekSeasonalStrategy,
    MultiTimeframeTrendStrategy,
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
    # Initial Balance / Breakout
    "ib": InitialBalanceExtensionStrategy,
    "drive": OpeningDriveStrategy,
    # Mean Reversion
    "climax": VolumeClimaxReversalStrategy,
    "exhaust": MomentumExhaustionStrategy,
    "vwapsd": VWAPSDReversalStrategy,
    # Sector / Pairs
    "sector": SectorRotationMomentumStrategy,
    "etfnav": ETFNavPremiumStrategy,
    "rspairs": RelativeStrengthPairsStrategy,
    "crypto": CryptoProxyRegimeStrategy,
    # Flow / Seasonal
    "moc": MOCImbalanceStrategy,
    "dow": DayOfWeekSeasonalStrategy,
    # Multi-Timeframe
    "mttf": MultiTimeframeTrendStrategy,
}


def run_strategy(strategy_class, data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Lance un backtest pour une stratégie."""
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
                        help="Strategy to backtest (default: all)")
    parser.add_argument("--universe", type=str, default=None,
                        choices=["full", "eligible", "curated", "minimal"],
                        help="Universe mode (default: from UNIVERSE_MODE env)")
    parser.add_argument("--days", type=int, default=config.BACKTEST_DAYS,
                        help="Backtest period in days (default: 365)")
    parser.add_argument("--timeframe", type=str, default="5Min",
                        choices=["1Min", "5Min"],
                        help="Bar timeframe (default: 5Min)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force re-download all data")
    parser.add_argument("--scan-only", action="store_true",
                        help="Only run the Stocks in Play scanner, don't backtest")
    args = parser.parse_args()

    universe_mode = args.universe or config.UNIVERSE_MODE

    # Setup
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    print("=" * 70)
    print("  INTRADAY BACKTESTER — FULL UNIVERSE")
    print("=" * 70)
    print(f"  Capital:      ${config.INITIAL_CAPITAL:,.0f}")
    print(f"  Period:       {args.days} days")
    print(f"  Timeframe:    {args.timeframe}")
    print(f"  Universe:     {universe_mode}")
    print(f"  Strategy:     {args.strategy}")
    print(f"  Costs:        ${config.COMMISSION_PER_SHARE}/share + {config.SLIPPAGE_PCT*100:.2f}% slippage")
    print("=" * 70)

    # ══════════════════════════════════════════════════════
    # STEP 1 : Préparer l'univers
    # ══════════════════════════════════════════════════════
    print("\n[STEP 1] Preparing universe...")
    tickers = prepare_universe(mode=universe_mode, force_refresh=args.no_cache)
    print(f"  Universe: {len(tickers)} tickers")

    # ══════════════════════════════════════════════════════
    # STEP 2 : Fetch les données
    # ══════════════════════════════════════════════════════
    print(f"\n[STEP 2] Fetching {args.timeframe} data for {len(tickers)} tickers...")
    start = datetime.now() - timedelta(days=args.days)
    end = datetime.now()

    data = fetch_multiple(
        tickers,
        timeframe=args.timeframe,
        start=start,
        end=end,
        use_cache=not args.no_cache,
    )

    if not data:
        print("[ERROR] No data fetched. Check Alpaca API keys:")
        print("  export ALPACA_API_KEY='your_key'")
        print("  export ALPACA_SECRET_KEY='your_secret'")
        sys.exit(1)

    total_bars = sum(len(df) for df in data.values())
    print(f"\n  Loaded: {len(data)} tickers, {total_bars:,} total bars")

    # ══════════════════════════════════════════════════════
    # STEP 3 : Scanner Stocks in Play (pour chaque jour)
    # ══════════════════════════════════════════════════════
    print(f"\n[STEP 3] Scanning Stocks in Play...")

    # Collecter toutes les dates
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.date)
    all_dates = sorted(all_dates)

    # Calculer les daily stats pour le scanner
    daily_stats = pd.DataFrame()  # Le scanner utilise les données intraday comme proxy
    # En mode complet, on peut calculer les daily stats via compute_daily_stats()

    # Scanner chaque jour et logger les stats
    daily_sip_counts = []
    for date in all_dates:
        day_data = {}
        for ticker, df in data.items():
            day_df = df[df.index.date == date]
            if not day_df.empty:
                day_data[ticker] = day_df

        sip = scan_stocks_in_play(day_data, daily_stats, date)
        daily_sip_counts.append(len(sip))

    avg_sip = sum(daily_sip_counts) / len(daily_sip_counts) if daily_sip_counts else 0
    print(f"  Trading days: {len(all_dates)}")
    print(f"  Avg Stocks in Play/day: {avg_sip:.1f}")
    print(f"  Min/Max SIP: {min(daily_sip_counts)}/{max(daily_sip_counts)}")

    if args.scan_only:
        # Montrer le dernier scan
        last_date = all_dates[-1]
        last_day_data = {t: df[df.index.date == last_date] for t, df in data.items() if not df[df.index.date == last_date].empty}
        sip = scan_stocks_in_play(last_day_data, daily_stats, last_date)
        print(f"\n  Stocks in Play for {last_date}:")
        for s in sip[:20]:
            print(f"    {s['ticker']:6s} score={s['score']:>5.1f}  {', '.join(s['reasons'])}")
        return

    # ══════════════════════════════════════════════════════
    # STEP 4 : Run les stratégies
    # ══════════════════════════════════════════════════════
    print(f"\n[STEP 4] Running strategies on {len(data)} tickers...")

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

        if not trades.empty:
            safe_name = name.lower().replace(' ', '_').replace('/', '_').replace('+', '_')
            csv_path = os.path.join(config.OUTPUT_DIR, f"trades_{safe_name}.csv")
            trades.to_csv(csv_path, index=False)

            # Unique tickers traded
            unique_tickers = trades["ticker"].nunique()
            print(f"  > {name}: traded {unique_tickers} unique tickers")

            plot_weekday_performance(trades, name)
            plot_trade_distribution(trades, name)

    # ══════════════════════════════════════════════════════
    # STEP 5 : Comparaison multi-stratégies
    # ══════════════════════════════════════════════════════
    if len(all_results) > 1:
        print("\n" + "=" * 70)
        print("  MULTI-STRATEGY COMPARISON")
        print("=" * 70)
        print(f"  Universe: {len(data)} tickers ({universe_mode} mode)")

        # Equity curves
        equity_curves = {n: m["equity_curve"] for n, m in all_results.items()
                        if not m["equity_curve"].empty}
        if equity_curves:
            plot_equity_curve(equity_curves, "All Strategies — Equity Curves")

        plot_strategy_comparison(all_results)

        # Ranking
        print("\n  RANKING BY SHARPE RATIO:")
        ranked = sorted(all_results.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True)
        for i, (name, m) in enumerate(ranked, 1):
            status = "+" if m["sharpe_ratio"] > 1 else "-"
            print(f"  {status} {i:2d}. {name:30s} "
                  f"Sharpe={m['sharpe_ratio']:>6.2f}  "
                  f"Return={m['total_return_pct']:>7.2f}%  "
                  f"WR={m['win_rate']:>5.1f}%  "
                  f"PF={m['profit_factor']:>5.2f}  "
                  f"DD={m['max_drawdown_pct']:>5.2f}%  "
                  f"Trades={m['n_trades']:>5d}")

        # Summary CSV
        summary = pd.DataFrame([
            {"strategy": name,
             "universe_size": len(data),
             "universe_mode": universe_mode,
             **{k: v for k, v in m.items()
                if k not in ["equity_curve", "by_weekday", "by_ticker"]}}
            for name, m in all_results.items()
        ])
        summary_path = os.path.join(config.OUTPUT_DIR, "strategy_summary.csv")
        summary.to_csv(summary_path, index=False)
        print(f"\n  [CSV] Summary: {summary_path}")

        # Top traded tickers across all strategies
        print("\n  TOP TRADED TICKERS (all strategies combined):")
        all_trades_combined = pd.concat(
            [t for t in all_trades.values() if not t.empty],
            ignore_index=True
        )
        if not all_trades_combined.empty:
            ticker_counts = all_trades_combined["ticker"].value_counts().head(20)
            for ticker, count in ticker_counts.items():
                avg_pnl = all_trades_combined[all_trades_combined["ticker"] == ticker]["pnl"].mean()
                print(f"    {ticker:6s}: {count:>4d} trades, avg P&L ${avg_pnl:>8.2f}")

    print(f"\n[DONE] Results saved to: {config.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
