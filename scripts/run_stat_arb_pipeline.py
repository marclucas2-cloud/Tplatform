#!/usr/bin/env python3
"""MidCap Stat Arb Pipeline — fetch data, backtest, walk-forward, decide.

Usage:
    python scripts/run_stat_arb_pipeline.py [--skip-fetch] [--years 3]

Steps:
    1. Fetch ~200 tickers via Alpaca (or use cache)
    2. Run backtest_stat_arb() on full period
    3. Run walk_forward_stat_arb() (5 windows)
    4. Print verdict: VALIDATED / BORDERLINE / REJECTED
    5. If VALIDATED -> print integration instructions
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(message)s",
)
logger = logging.getLogger("stat_arb.pipeline")


def run_pipeline(skip_fetch: bool = False, years: int = 3):
    """Run the full stat arb pipeline."""

    print("=" * 70)
    print("MIDCAP STATISTICAL ARBITRAGE — FULL PIPELINE")
    print(f"Date: {datetime.now().isoformat()}")
    print("=" * 70)

    # ============================================
    # STEP 1: Fetch data
    # ============================================
    print("\n--- STEP 1: DATA ---")
    from scripts.fetch_midcap_data import fetch_all_tickers, load_cached_data

    if skip_fetch:
        print("Skipping fetch, loading cache...")
        prices = load_cached_data()
    else:
        print(f"Fetching {years} years of daily data for ~200 tickers...")
        prices = fetch_all_tickers(years=years)

    if len(prices) < 20:
        print(f"ABORT: Only {len(prices)} tickers available (need >= 20)")
        return

    print(f"Data ready: {len(prices)} tickers")

    # Compute dollar volumes
    volumes = {}
    for ticker, df in prices.items():
        if "volume" in df.columns and "close" in df.columns:
            volumes[ticker] = df["volume"] * df["close"]

    # ============================================
    # STEP 2: Quick backtest
    # ============================================
    print("\n--- STEP 2: QUICK BACKTEST ---")

    from strategies_v2.us.midcap_stat_arb_backtest import backtest_stat_arb
    from strategies_v2.us.midcap_stat_arb_strategy import StatArbConfig

    config = StatArbConfig(
        max_pairs=10,
        position_per_leg_usd=1500,
        formation_period_days=120,
    )

    # Determine date range from data
    sample = list(prices.values())[0]
    all_dates = sample.index
    # Use last 2 years for backtest, keep earliest for formation
    total_days = len(all_dates)
    formation_buffer = config.formation_period_days + 20
    if total_days < formation_buffer + 100:
        print(f"ABORT: Not enough data ({total_days} bars, need {formation_buffer + 100}+)")
        return

    bt_start = all_dates[formation_buffer]
    bt_end = all_dates[-1]

    print(f"Backtest period: {bt_start.date()} to {bt_end.date()} ({(bt_end - bt_start).days} days)")

    result = backtest_stat_arb(
        prices=prices,
        start_date=str(bt_start.date()),
        end_date=str(bt_end.date()),
        initial_capital=30_000,
        config=config,
        volumes=volumes,
        rebalance_every_n_days=5,
    )

    print(result.summary())

    # Gate check
    if result.sharpe_ratio < 0.5:
        print(f"GATE 2 FAILED: Sharpe {result.sharpe_ratio} < 0.5 — KILL")
        _save_report(result, None, "REJECTED")
        return

    print(f"GATE 2 PASSED: Sharpe {result.sharpe_ratio}")

    # ============================================
    # STEP 3: Walk-Forward
    # ============================================
    print("\n--- STEP 3: WALK-FORWARD ---")

    from strategies_v2.us.midcap_stat_arb_backtest import walk_forward_stat_arb

    wf_result = walk_forward_stat_arb(
        prices=prices,
        n_windows=5,
        train_pct=0.7,
        initial_capital=30_000,
        volumes=volumes,
    )

    print(f"\nWalk-Forward Results:")
    print(f"  Windows: {wf_result['n_windows']}")
    print(f"  Avg Sharpe OOS: {wf_result['avg_sharpe_oos']}")
    print(f"  Median Sharpe OOS: {wf_result['median_sharpe_oos']}")
    print(f"  Profitable windows: {wf_result['profitable_windows_pct']}%")
    print(f"  Total OOS trades: {wf_result['total_trades']}")

    for w in wf_result["windows"]:
        print(f"    Window {w['window']}: Sharpe={w['sharpe']:.2f}, "
              f"Return={w['return_pct']:.2f}%, Trades={w['trades']}")

    verdict = wf_result["verdict"]
    print(f"\n  VERDICT: {verdict}")

    # ============================================
    # STEP 4: Report
    # ============================================
    _save_report(result, wf_result, verdict)

    if verdict == "VALIDATED":
        print("\n" + "=" * 70)
        print("NEXT STEPS — PAPER INTEGRATION")
        print("=" * 70)
        print("""
1. Add to worker.py cycle:
   - Weekly: strategy.update_pairs(prices)
   - Daily at 15:45 CET: signals = strategy.generate_signals(prices, regime)
   - Execute signals via Alpaca paper account

2. Config: config/midcap_stat_arb.yaml already ready

3. Risk integration:
   - Add 'midcap_stat_arb' to activation matrix
   - Position limits: $1.5K per leg, $3K per pair, max 10 pairs
   - Max gross: 80% of $30K = $24K deployed

4. Monitor for 30+ trades in paper mode before live
""")

    elif verdict == "BORDERLINE":
        print("\n  BORDERLINE — extended paper testing recommended.")
        print("  Consider adjusting: z_entry (1.8?), half_life_max (25?)")

    else:
        print("\n  REJECTED — strategy not viable on this universe.")
        print("  Options: different universe (SP500?), different timeframe (4h?)")


def _save_report(bt_result, wf_result, verdict):
    """Save pipeline results to JSON."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "verdict": verdict,
        "backtest": {
            "sharpe": bt_result.sharpe_ratio,
            "total_return_pct": bt_result.total_return_pct,
            "max_dd_pct": bt_result.max_drawdown_pct,
            "win_rate": bt_result.win_rate,
            "total_trades": bt_result.total_trades,
            "avg_holding_days": bt_result.avg_holding_days,
            "profit_factor": bt_result.profit_factor,
            "commission_burn_pct": bt_result.commission_burn_pct,
            "pairs_traded": bt_result.pairs_traded,
        },
        "walk_forward": wf_result,
    }

    path = ROOT / "reports" / "research" / "stat_arb_pipeline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to {path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true", help="Use cached data")
    parser.add_argument("--years", type=int, default=3, help="Years of history")
    args = parser.parse_args()

    run_pipeline(skip_fetch=args.skip_fetch, years=args.years)
