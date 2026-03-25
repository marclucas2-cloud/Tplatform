"""
Scan batch de strategies sur l'univers curated (207 tickers, 6 mois, 5M).

Usage :
    python run_batch_scan.py --strategies p0
    python run_batch_scan.py --strategies p1
    python run_batch_scan.py --strategies all_new
    python run_batch_scan.py --strategies vol_squeeze,rsi_div
"""
import argparse
import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics, print_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP

# Strategy imports
from strategies.volatility_squeeze_breakout import VolatilitySqueezeBreakoutStrategy
from strategies.rsi_divergence import RSIDivergenceStrategy
from strategies.opening_volume_surge import OpeningVolumeSurgeStrategy
from strategies.vwap_micro_reversion import VWAPMicroReversionStrategy
from strategies.intraday_momentum_persistence import IntradayMomentumPersistenceStrategy
# P1 strategies
from strategies.range_compression_breakout import RangeCompressionBreakoutStrategy
from strategies.volume_dry_up_reversal import VolumeDryUpReversalStrategy
from strategies.midday_reversal import MiddayReversalStrategy
from strategies.atr_breakout_filter import ATRBreakoutFilterStrategy
from strategies.ema_crossover_5m import EMACrossover5MStrategy
from strategies.high_of_day_breakout import HighOfDayBreakoutStrategy
from strategies.gap_and_go_momentum import GapAndGoMomentumStrategy
from strategies.afternoon_trend_follow import AfternoonTrendFollowStrategy
# P2 strategies
from strategies.close_auction_imbalance import CloseAuctionImbalanceStrategy
from strategies.first_hour_range_retest import FirstHourRangeRetestStrategy
from strategies.sector_leader_follow import SectorLeaderFollowStrategy
from strategies.vwap_trend_day import VWAPTrendDayStrategy
from strategies.morning_star_reversal import MorningStarReversalStrategy
from strategies.relative_volume_breakout import RelativeVolumeBreakoutStrategy
from strategies.mean_reversion_rsi2 import MeanReversionRSI2Strategy
from strategies.spread_compression_pairs import SpreadCompressionPairsStrategy
from strategies.opening_gap_fill import OpeningGapFillStrategy
from strategies.double_bottom_top import DoubleBottomTopStrategy
from strategies.momentum_ignition import MomentumIgnitionStrategy
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

P0_STRATEGIES = {
    "vol_squeeze": VolatilitySqueezeBreakoutStrategy,
    "rsi_div": RSIDivergenceStrategy,
    "vol_surge": OpeningVolumeSurgeStrategy,
    "vwap_micro": VWAPMicroReversionStrategy,
    "momentum_persist": IntradayMomentumPersistenceStrategy,
}

P1_STRATEGIES = {
    "range_compress": RangeCompressionBreakoutStrategy,
    "vol_dryup": VolumeDryUpReversalStrategy,
    "midday_rev": MiddayReversalStrategy,
    "atr_break": ATRBreakoutFilterStrategy,
    "ema_cross": EMACrossover5MStrategy,
    "hod_break": HighOfDayBreakoutStrategy,
    "gap_go": GapAndGoMomentumStrategy,
    "pm_trend": AfternoonTrendFollowStrategy,
}
P2_STRATEGIES = {
    "close_auction": CloseAuctionImbalanceStrategy,
    "fh_retest": FirstHourRangeRetestStrategy,
    "sector_lead": SectorLeaderFollowStrategy,
    "vwap_trend": VWAPTrendDayStrategy,
    "morn_star": MorningStarReversalStrategy,
    "rel_vol_break": RelativeVolumeBreakoutStrategy,
    "rsi2": MeanReversionRSI2Strategy,
    "spread_pairs": SpreadCompressionPairsStrategy,
    "gap_fill": OpeningGapFillStrategy,
    "double_bt": DoubleBottomTopStrategy,
    "mom_ignition": MomentumIgnitionStrategy,
}
P3_STRATEGIES = {
    "mr_3sigma": MeanReversion3SigmaStrategy,
    "vol_poc": VolumeProfilePOCStrategy,
    "macd_div": MACDDivergenceStrategy,
    "hammer": HammerEngulfingStrategy,
    "range_scalp": RangeBoundScalpStrategy,
    "premarket_vol": PreMarketVolumeLeaderStrategy,
    "triple_ema": TripleEMAPullbackStrategy,
    "overnight_brk": OvernightRangeBreakoutStrategy,
    "tlt_spy_div": TLTSPYDivergenceStrategy,
    "consec_rev": ConsecutiveBarReversalStrategy,
    "mr_etf": IntradayMeanReversionETFStrategy,
}

ALL_GROUPS = {
    "p0": P0_STRATEGIES,
    "p1": P1_STRATEGIES,
    "p2": P2_STRATEGIES,
    "p3": P3_STRATEGIES,
}


def get_curated_tickers():
    """Retourne l'univers curated (~207 tickers)."""
    tickers = list(PERMANENT_TICKERS)
    for components in SECTOR_MAP.values():
        tickers.extend(components)
    return sorted(set(tickers))


def run_scan(strategies: dict, tickers: list, days: int = 180):
    """Execute le scan batch sur toutes les strategies."""
    print(f"\n{'='*70}")
    print(f"  BATCH SCAN — {len(strategies)} strategies x {len(tickers)} tickers x {days}j")
    print(f"{'='*70}")

    # Fetch data (utilise le cache Parquet)
    print(f"\n  Chargement des donnees ({len(tickers)} tickers, {days}j, 5M)...")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    data = fetch_multiple(
        tickers,
        timeframe=config.TIMEFRAME_5MIN,
        start=start_date,
        end=end_date,
    )

    if not data:
        print("  [ERROR] Pas de donnees chargees!")
        return {}

    loaded = {k: v for k, v in data.items() if v is not None and not v.empty}
    print(f"  Donnees chargees: {len(loaded)} tickers, {sum(len(v) for v in loaded.values()):,} barres")

    results = {}
    for name, strategy_cls in strategies.items():
        print(f"\n  --- {name} ({strategy_cls.name if hasattr(strategy_cls, 'name') else strategy_cls.__name__}) ---")

        try:
            strategy = strategy_cls()
            engine = BacktestEngine(strategy, config.INITIAL_CAPITAL)
            trades_df = engine.run(loaded)

            if trades_df.empty:
                metrics = {"n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0, "win_rate": 0}
                print(f"  -> 0 trades")
            else:
                metrics = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
                print_metrics(strategy.name, metrics)

                # Sauvegarder les trades en CSV
                csv_name = f"trades_{strategy.name.lower().replace(' ', '_').replace('-', '_')}.csv"
                csv_path = Path(config.OUTPUT_DIR) / csv_name
                trades_df.to_csv(csv_path, index=False)
                print(f"  -> Trades sauvegardees: {csv_path}")

            results[name] = metrics

        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            import traceback
            traceback.print_exc()
            results[name] = {"n_trades": 0, "error": str(e)}

    # Summary
    print(f"\n{'='*90}")
    print(f"  RESUME DU SCAN")
    print(f"{'='*90}")
    print(f"  {'Strategie':<30} {'Trades':>6} {'Net PnL':>12} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6}")
    print(f"  {'-'*30} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6}")

    for name, m in results.items():
        if "error" in m:
            print(f"  {name:<30} ERROR: {m['error'][:50]}")
        else:
            print(f"  {name:<30} {m.get('n_trades', 0):>6} "
                  f"${m.get('net_pnl', 0):>10,.2f} "
                  f"{m.get('sharpe_ratio', 0):>8.2f} "
                  f"{m.get('win_rate', 0):>5.1f}% "
                  f"{m.get('profit_factor', 0):>6.2f} "
                  f"{m.get('max_drawdown_pct', 0):>5.2f}%")

    # Verdict
    print(f"\n  VERDICT :")
    for name, m in results.items():
        if m.get("n_trades", 0) == 0:
            print(f"  [SKIP] {name}: 0 trades")
        elif m.get("sharpe_ratio", 0) >= 1.5 and m.get("net_pnl", 0) > 0:
            print(f"  [WINNER] {name}: Sharpe {m['sharpe_ratio']:.2f}, PnL ${m['net_pnl']:,.2f}")
        elif m.get("sharpe_ratio", 0) >= 0.5 and m.get("net_pnl", 0) > 0:
            print(f"  [POTENTIEL] {name}: Sharpe {m['sharpe_ratio']:.2f} — a optimiser")
        else:
            print(f"  [REJETE] {name}: Sharpe {m.get('sharpe_ratio', 0):.2f}, PnL ${m.get('net_pnl', 0):,.2f}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="p0", help="Group: p0, p1, p2, p3, all_new, or comma-separated names")
    parser.add_argument("--days", type=int, default=180, help="Nombre de jours de backtest")
    parser.add_argument("--tickers", default="curated", help="curated, minimal, or comma-separated")
    args = parser.parse_args()

    # Select strategies
    if args.strategies in ALL_GROUPS:
        strategies = ALL_GROUPS[args.strategies]
    elif args.strategies == "all_new":
        strategies = {}
        for g in ALL_GROUPS.values():
            strategies.update(g)
    else:
        names = args.strategies.split(",")
        all_strats = {}
        for g in ALL_GROUPS.values():
            all_strats.update(g)
        strategies = {n: all_strats[n] for n in names if n in all_strats}

    if not strategies:
        print("Aucune strategie selectionnee!")
        return

    # Select tickers
    if args.tickers == "curated":
        tickers = get_curated_tickers()
    elif args.tickers == "minimal":
        tickers = list(PERMANENT_TICKERS)
    else:
        tickers = args.tickers.split(",")

    run_scan(strategies, tickers, args.days)


if __name__ == "__main__":
    main()
