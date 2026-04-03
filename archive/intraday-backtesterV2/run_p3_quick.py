"""Quick P3 scan — runs each strategy one at a time, prints results."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics, print_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP
from datetime import datetime, timedelta

# P3 strategies
from strategies.mean_reversion_3sigma import MeanReversion3SigmaStrategy
from strategies.volume_profile_poc import VolumeProfilePOCStrategy
from strategies.macd_divergence import MACDDivergenceStrategy
from strategies.hammer_engulfing import HammerEngulfingStrategy
from strategies.range_bound_scalp import RangeBoundScalpStrategy
from strategies.pre_market_volume_leader import PreMarketVolumeLeaderStrategy
from strategies.triple_ema_pullback import TripleEMAPullbackStrategy
from strategies.overnight_range_breakout import OvernightRangeBreakoutStrategy
from strategies.tlt_spy_divergence import TLTSPYDivergenceStrategy
from strategies.consecutive_bar_reversal import ConsecutiveBarReversalStrategy
from strategies.intraday_mean_reversion_etf import IntradayMeanReversionETFStrategy

STRATEGIES = [
    ("mr_3sigma", MeanReversion3SigmaStrategy),
    ("vol_poc", VolumeProfilePOCStrategy),
    ("macd_div", MACDDivergenceStrategy),
    ("hammer", HammerEngulfingStrategy),
    ("range_scalp", RangeBoundScalpStrategy),
    ("premarket_vol", PreMarketVolumeLeaderStrategy),
    ("triple_ema", TripleEMAPullbackStrategy),
    ("overnight_brk", OvernightRangeBreakoutStrategy),
    ("tlt_spy_div", TLTSPYDivergenceStrategy),
    ("consec_rev", ConsecutiveBarReversalStrategy),
    ("mr_etf", IntradayMeanReversionETFStrategy),
]

def get_tickers():
    tickers = list(PERMANENT_TICKERS)
    for components in SECTOR_MAP.values():
        tickers.extend(components)
    return sorted(set(tickers))

def main():
    tickers = get_tickers()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180)

    print(f"Loading data for {len(tickers)} tickers...")
    sys.stdout.flush()
    data = fetch_multiple(tickers, timeframe=config.TIMEFRAME_5MIN, start=start_date, end=end_date)
    loaded = {k: v for k, v in data.items() if v is not None and not v.empty}
    print(f"Loaded: {len(loaded)} tickers, {sum(len(v) for v in loaded.values()):,} bars")
    sys.stdout.flush()

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
                results[name] = {"n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0, "win_rate": 0}
            else:
                m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
                print(f"  {m['n_trades']} trades | PnL ${m['net_pnl']:,.2f} | Sharpe {m['sharpe_ratio']:.2f} | WR {m['win_rate']:.1f}% | PF {m['profit_factor']:.2f}")
                csv_path = Path(config.OUTPUT_DIR) / f"trades_{strategy.name.lower().replace(' ', '_')}.csv"
                trades_df.to_csv(csv_path, index=False)
                results[name] = m
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[name] = {"n_trades": 0, "error": str(e)}
        sys.stdout.flush()

    # Summary
    print(f"\n{'='*80}")
    print(f"  P3 SUMMARY")
    print(f"{'='*80}")
    for name, m in results.items():
        if "error" in m:
            print(f"  {name:<20} ERROR: {m['error'][:40]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {name:<20} 0 trades")
        else:
            verdict = "WINNER" if m.get("sharpe_ratio", 0) >= 1.5 and m.get("net_pnl", 0) > 0 else \
                      "POTENTIEL" if m.get("sharpe_ratio", 0) >= 0.5 and m.get("net_pnl", 0) > 0 else "REJETE"
            print(f"  {name:<20} {m['n_trades']:>4} trades | ${m['net_pnl']:>9,.2f} | Sharpe {m['sharpe_ratio']:>6.2f} | [{verdict}]")

if __name__ == "__main__":
    main()
