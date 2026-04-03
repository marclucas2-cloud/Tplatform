"""Scan batch session 26 mars — 10 nouvelles strategies."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import config
from data_fetcher import fetch_multiple
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics, print_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP
from datetime import datetime, timedelta

# Imports
from strategies.overnight_simple_spy import OvernightSimpleSPYStrategy
from strategies.overnight_sector_winner import OvernightSectorWinnerStrategy
from strategies.overnight_crypto_proxy import OvernightCryptoProxyStrategy
from strategies.vwap_micro_crypto import VWAPMicroCryptoStrategy
from strategies.opex_weekly_expansion import OpExWeeklyExpansionStrategy
from strategies.midday_reversal_power_hour import MiddayReversalPowerHourStrategy
from strategies.gold_fear_gauge import GoldFearGaugeStrategy
from strategies.tlt_bank_signal import TLTBankSignalStrategy
from strategies.signal_confluence import SignalConfluenceStrategy
from strategies.correlation_regime_hedge import CorrelationRegimeHedgeStrategy

STRATEGIES = [
    ("overnight_spy", OvernightSimpleSPYStrategy),
    ("overnight_sector", OvernightSectorWinnerStrategy),
    ("overnight_crypto", OvernightCryptoProxyStrategy),
    ("vwap_micro_crypto", VWAPMicroCryptoStrategy),
    ("opex_weekly", OpExWeeklyExpansionStrategy),
    ("midday_power", MiddayReversalPowerHourStrategy),
    ("gold_fear", GoldFearGaugeStrategy),
    ("tlt_bank", TLTBankSignalStrategy),
    ("signal_confluence", SignalConfluenceStrategy),
    ("corr_hedge", CorrelationRegimeHedgeStrategy),
]

def get_tickers():
    tickers = list(PERMANENT_TICKERS)
    for components in SECTOR_MAP.values():
        tickers.extend(components)
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
                results[name] = {"n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0, "win_rate": 0,
                                 "profit_factor": 0, "max_drawdown_pct": 0}
            else:
                m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
                print(f"  {m['n_trades']} trades | PnL ${m['net_pnl']:,.2f} | Sharpe {m['sharpe_ratio']:.2f} | "
                      f"WR {m['win_rate']:.1f}% | PF {m['profit_factor']:.2f} | DD {m['max_drawdown_pct']:.2f}%")
                csv_path = Path(config.OUTPUT_DIR) / f"trades_{strategy.name.lower().replace(' ', '_')}.csv"
                trades_df.to_csv(csv_path, index=False)
                # Also save to session dir
                trades_df.to_csv(output_dir / f"trades_{name}.csv", index=False)
                results[name] = m
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            results[name] = {"n_trades": 0, "error": str(e)}
        sys.stdout.flush()

    # Summary
    print(f"\n{'='*90}")
    print(f"  SESSION 26 MARS — RESULTATS")
    print(f"{'='*90}")
    print(f"  {'Strategie':<22} {'Trades':>6} {'Net PnL':>12} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Verdict':>10}")
    print(f"  {'-'*22} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*10}")

    for name, m in results.items():
        if "error" in m:
            print(f"  {name:<22} ERROR: {m['error'][:40]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {name:<22}      0                                               SKIP")
        else:
            sharpe = m.get("sharpe_ratio", 0)
            pf = m.get("profit_factor", 0)
            pnl = m.get("net_pnl", 0)
            n = m.get("n_trades", 0)
            wr = m.get("win_rate", 0)
            dd = m.get("max_drawdown_pct", 0)

            if sharpe >= 1.5 and pf >= 1.2 and n >= 20 and dd < 10 and pnl > 0:
                verdict = "WINNER"
            elif sharpe >= 0.5 and pnl > 0:
                verdict = "POTENTIEL"
            else:
                verdict = "REJETE"

            print(f"  {name:<22} {n:>6} ${pnl:>10,.2f} {sharpe:>8.2f} {wr:>5.1f}% {pf:>6.2f} {dd:>5.2f}% {verdict:>10}")

    # Save summary
    import json
    with open(output_dir / "scan_results.json", "w") as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk != "equity_curve" and kk != "by_weekday" and kk != "by_ticker"}
                    for k, v in results.items()}, f, indent=2, default=str)
    print(f"\n  Results saved to {output_dir}")

if __name__ == "__main__":
    main()
