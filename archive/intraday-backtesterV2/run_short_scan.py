"""Scan batch — 10 short/bear strategies."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP
from datetime import datetime, timedelta

from strategies.bear_morning_fade import BearMorningFadeStrategy
from strategies.breakdown_continuation import BreakdownContinuationStrategy
from strategies.vix_expansion_short import VIXExpansionShortStrategy
from strategies.weak_sector_short import WeakSectorShortStrategy
from strategies.failed_rally_short import FailedRallyShortStrategy
from strategies.overnight_short_bear import OvernightShortBearStrategy
from strategies.defensive_rotation_long import DefensiveRotationLongStrategy
from strategies.squeeze_fade import SqueezeFadeStrategy
from strategies.eod_sell_pressure import EODSellPressureStrategy
# ARCHIVED: from strategies.crypto_bear_cascade import CryptoBearCascadeStrategy

STRATEGIES = [
    ("bear_fade", BearMorningFadeStrategy),
    ("breakdown", BreakdownContinuationStrategy),
    ("vix_short", VIXExpansionShortStrategy),
    ("weak_sector", WeakSectorShortStrategy),
    ("failed_rally", FailedRallyShortStrategy),
    ("overnight_short", OvernightShortBearStrategy),
    ("defensive_long", DefensiveRotationLongStrategy),
    ("squeeze_fade", SqueezeFadeStrategy),
    ("eod_sell", EODSellPressureStrategy),
    # ("crypto_bear", CryptoBearCascadeStrategy),  # ARCHIVED (WF-rejected)
]

def get_tickers():
    tickers = list(PERMANENT_TICKERS)
    for c in SECTOR_MAP.values():
        tickers.extend(c)
    return sorted(set(tickers))

def main():
    tickers = get_tickers()
    end = datetime.now()
    start = end - timedelta(days=200)

    print(f"Loading data for {len(tickers)} tickers...")
    sys.stdout.flush()
    data = fetch_multiple(tickers, timeframe=config.TIMEFRAME_5MIN, start=start, end=end)
    loaded = {k: v for k, v in data.items() if v is not None and not v.empty}
    print(f"Loaded: {len(loaded)} tickers, {sum(len(v) for v in loaded.values()):,} bars")
    sys.stdout.flush()

    output_dir = Path(__file__).parent.parent / "output" / "session_20260326"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for name, cls in STRATEGIES:
        print(f"\n--- {name} ---")
        sys.stdout.flush()
        try:
            strategy = cls()
            engine = BacktestEngine(strategy, config.INITIAL_CAPITAL)
            trades_df = engine.run(loaded)
            if trades_df.empty:
                print(f"  0 trades")
                results[name] = {"n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0, "win_rate": 0, "profit_factor": 0, "max_drawdown_pct": 0}
            else:
                m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
                # Count short vs long
                n_short = len(trades_df[trades_df.get("direction", pd.Series()) == "SHORT"]) if "direction" in trades_df.columns else 0
                n_long = len(trades_df) - n_short
                print(f"  {m['n_trades']} trades ({n_short}S/{n_long}L) | PnL ${m['net_pnl']:,.2f} | Sharpe {m['sharpe_ratio']:.2f} | "
                      f"WR {m['win_rate']:.1f}% | PF {m['profit_factor']:.2f} | DD {m['max_drawdown_pct']:.2f}%")
                csv_path = Path(config.OUTPUT_DIR) / f"trades_{strategy.name.lower().replace(' ', '_')}.csv"
                trades_df.to_csv(csv_path, index=False)
                trades_df.to_csv(output_dir / f"trades_short_{name}.csv", index=False)
                results[name] = m
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[name] = {"n_trades": 0, "error": str(e)}
        sys.stdout.flush()

    # Summary
    print(f"\n{'='*95}")
    print(f"  SHORT STRATEGIES — RESULTATS")
    print(f"{'='*95}")
    print(f"  {'Strategie':<18} {'Trades':>6} {'Net PnL':>12} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Verdict':>10}")
    print(f"  {'-'*18} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*10}")

    for name, m in results.items():
        if "error" in m:
            print(f"  {name:<18} ERROR: {m['error'][:40]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {name:<18}      0                                               SKIP")
        else:
            s = m.get("sharpe_ratio", 0)
            pf = m.get("profit_factor", 0)
            pnl = m.get("net_pnl", 0)
            n = m.get("n_trades", 0)
            wr = m.get("win_rate", 0)
            dd = m.get("max_drawdown_pct", 0)
            verdict = "WINNER" if s >= 0.5 and pf >= 1.2 and n >= 15 and dd < 10 and pnl > 0 else \
                      "POTENTIEL" if s > 0 and pnl > 0 else "REJETE"
            print(f"  {name:<18} {n:>6} ${pnl:>10,.2f} {s:>8.2f} {wr:>5.1f}% {pf:>6.2f} {dd:>5.2f}% {verdict:>10}")

    import json
    with open(output_dir / "short_scan_results.json", "w") as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk not in ("equity_curve", "by_weekday", "by_ticker")}
                    for k, v in results.items()}, f, indent=2, default=str)

import pandas as pd
if __name__ == "__main__":
    main()
