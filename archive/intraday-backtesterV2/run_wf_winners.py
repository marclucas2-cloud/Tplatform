"""Walk-forward validation for session winners."""
import sys, os, io
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP

from strategies.vwap_micro_reversion import VWAPMicroReversionStrategy
from strategies.midday_reversal import MiddayReversalStrategy
from strategies.triple_ema_pullback import TripleEMAPullbackStrategy

CANDIDATES = {
    "VWAP Micro-Deviation": VWAPMicroReversionStrategy,
    "Midday Reversal": MiddayReversalStrategy,
    "Triple EMA Pullback": TripleEMAPullbackStrategy,
}

IS_DAYS = 60
OOS_DAYS = 30
STEP_DAYS = 30

def get_tickers():
    tickers = list(PERMANENT_TICKERS)
    for components in SECTOR_MAP.values():
        tickers.extend(components)
    return sorted(set(tickers))

def split_data(data, start_date, end_date):
    filtered = {}
    for ticker, df in data.items():
        mask = (df.index.date >= start_date) & (df.index.date <= end_date)
        sub = df[mask]
        if not sub.empty:
            filtered[ticker] = sub
    return filtered

def main():
    tickers = get_tickers()
    end = datetime.now()
    start = end - timedelta(days=200)

    print("Loading data...")
    sys.stdout.flush()
    data = fetch_multiple(tickers, timeframe="5Min", start=start, end=end, use_cache=True)
    loaded = {k: v for k, v in data.items() if v is not None and not v.empty}

    all_dates = set()
    for df in loaded.values():
        all_dates.update(df.index.date)
    all_dates = sorted(all_dates)
    total_days = len(all_dates)
    print(f"Data: {len(loaded)} tickers, {total_days} trading days")

    windows = []
    i = 0
    while i + IS_DAYS + OOS_DAYS <= total_days:
        windows.append((
            all_dates[i], all_dates[i + IS_DAYS - 1],
            all_dates[i + IS_DAYS], all_dates[min(i + IS_DAYS + OOS_DAYS - 1, total_days - 1)]
        ))
        i += STEP_DAYS

    print(f"{len(windows)} walk-forward windows (60j IS -> 30j OOS)")
    sys.stdout.flush()

    results = {}
    for name, cls in CANDIDATES.items():
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        sys.stdout.flush()

        oos_results = []
        for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            oos_data = split_data(loaded, oos_start, oos_end)
            if not oos_data:
                continue

            strategy = cls()
            engine = BacktestEngine(strategy, config.INITIAL_CAPITAL)

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            trades = engine.run(oos_data)
            sys.stdout = old_stdout

            m = calculate_metrics(trades, config.INITIAL_CAPITAL)
            oos_results.append({
                "window": w_idx + 1,
                "return_pct": m["total_return_pct"],
                "sharpe": m["sharpe_ratio"],
                "pf": m["profit_factor"],
                "trades": m["n_trades"],
            })

            status = "+" if m["total_return_pct"] > 0 else "-"
            print(f"  {status} W{w_idx+1}: {oos_start}->{oos_end} | Ret={m['total_return_pct']:>6.2f}% | Sharpe={m['sharpe_ratio']:>5.2f} | PF={m['profit_factor']:>4.2f} | {m['n_trades']:>3d} trades")
            sys.stdout.flush()

        if not oos_results:
            print("  No OOS windows")
            continue

        profitable = sum(1 for r in oos_results if r["return_pct"] > 0)
        total = len(oos_results)
        hit_rate = profitable / total * 100
        avg_ret = np.mean([r["return_pct"] for r in oos_results])
        avg_sharpe = np.mean([r["sharpe"] for r in oos_results])

        verdict = "VALIDATED" if hit_rate >= 50 and avg_ret > 0 else "REJECTED"
        results[name] = {"hit_rate": hit_rate, "avg_return": avg_ret, "avg_sharpe": avg_sharpe, "verdict": verdict}

        print(f"\n  {profitable}/{total} windows profitable ({hit_rate:.0f}%)")
        print(f"  Avg OOS Return: {avg_ret:.3f}%, Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  >>> {verdict}")
        sys.stdout.flush()

    print(f"\n{'='*60}")
    print("  WALK-FORWARD SUMMARY")
    print(f"{'='*60}")
    for name, r in results.items():
        tag = "PASS" if r["verdict"] == "VALIDATED" else "FAIL"
        print(f"  [{tag}] {name:<25} HitRate={r['hit_rate']:>4.0f}% | AvgRet={r['avg_return']:>6.3f}% | AvgSharpe={r['avg_sharpe']:>5.2f}")

if __name__ == "__main__":
    main()
