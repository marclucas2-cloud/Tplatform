"""
Backtest batch : 5 strategies short (1 nouvelle + 4 V2 optimisees).

Strategies testees :
1. Momentum Exhaustion Short (A5) — nouvelle
2. Bear Morning Fade V2 — filtres assouplis
3. Breakdown Continuation V2 — stop elargi + RSI filter
4. EOD Sell Pressure V2 — timing raccourci
5. Squeeze Fade (re-test avec parametres courants)

Charge les donnees depuis data_cache/ (parquet).
Sauvegarde les resultats dans output/session_20260326/short_v2_results.json
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

import config
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics, print_metrics

# Import des strategies
from strategies.momentum_exhaustion_short import MomentumExhaustionShortStrategy
from strategies.bear_morning_fade_v2 import BearMorningFadeV2Strategy
from strategies.breakdown_v2 import BreakdownV2Strategy
from strategies.eod_sell_v2 import EODSellV2Strategy
from strategies.squeeze_fade import SqueezeFadeStrategy


STRATEGIES = [
    ("momentum_exhaustion", MomentumExhaustionShortStrategy),
    ("bear_fade_v2", BearMorningFadeV2Strategy),
    ("breakdown_v2", BreakdownV2Strategy),
    ("eod_sell_v2", EODSellV2Strategy),
    ("squeeze_fade", SqueezeFadeStrategy),
]

# Seuils de validation
MIN_SHARPE = 0.5
MIN_PF = 1.2
MIN_TRADES = 15
MAX_DD = 10.0


def load_cached_data(max_tickers: int = 200) -> dict[str, pd.DataFrame]:
    """Charge les top N tickers par volume depuis le cache parquet."""
    cache_dir = config.CACHE_DIR
    if not os.path.exists(cache_dir):
        print(f"[ERROR] Cache dir not found: {cache_dir}")
        sys.exit(1)

    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    ticker_files = {}
    for f in parquet_files:
        parts = f.split("_5Min_")
        if len(parts) != 2:
            continue
        ticker = parts[0]
        path = os.path.join(cache_dir, f)
        size = os.path.getsize(path)
        if ticker not in ticker_files:
            ticker_files[ticker] = []
        ticker_files[ticker].append((path, size))

    # Top N par taille de fichier (proxy pour volume/liquidite)
    sorted_tickers = sorted(
        ticker_files.keys(),
        key=lambda t: max(s for _, s in ticker_files[t]),
        reverse=True,
    )[:max_tickers]

    # Forcer inclusion des tickers requis par les strategies
    required = [
        # Benchmark
        "SPY", "QQQ",
        # High-beta (momentum exhaustion, squeeze fade)
        "TSLA", "NVDA", "AMD", "COIN", "MARA", "MSTR",
        # EOD sell V2
        "AAPL", "MSFT", "META", "AMZN", "GOOGL",
        # Sector leaders
        "JPM", "GS", "XOM", "BA", "UNH",
    ]
    for t in required:
        if t not in sorted_tickers and t in ticker_files:
            sorted_tickers.append(t)

    data = {}
    for ticker in sorted_tickers:
        if ticker in ticker_files:
            # Prendre le fichier le plus gros (le plus de donnees)
            best_path = max(ticker_files[ticker], key=lambda x: x[1])[0]
            try:
                df = pd.read_parquet(best_path)
                if not df.empty:
                    data[ticker] = df
            except Exception:
                pass

    return data


def walk_forward(strategy_class, data: dict[str, pd.DataFrame],
                 n_folds: int = 5) -> dict:
    """Walk-forward validation : split les jours en N folds."""
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.date)
    all_dates = sorted(all_dates)
    n_days = len(all_dates)

    fold_size = n_days // n_folds
    fold_results = []

    print(f"\n  [WALK-FORWARD] {n_folds} folds, {fold_size} days/fold")

    for fold in range(n_folds):
        start_idx = fold * fold_size
        end_idx = (fold + 1) * fold_size if fold < n_folds - 1 else n_days
        fold_dates = set(all_dates[start_idx:end_idx])

        fold_data = {}
        for ticker, df in data.items():
            mask = pd.Series(df.index.date, index=df.index).isin(fold_dates)
            fold_df = df[mask.values]
            if not fold_df.empty:
                fold_data[ticker] = fold_df

        if not fold_data:
            continue

        strategy = strategy_class()
        engine = BacktestEngine(strategy)
        trades = engine.run(fold_data)
        metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)

        fold_results.append({
            "fold": fold + 1,
            "days": end_idx - start_idx,
            "trades": metrics["n_trades"],
            "return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe_ratio"],
            "win_rate": metrics["win_rate"],
            "pf": metrics["profit_factor"],
            "dd": metrics["max_drawdown_pct"],
        })

        print(f"    Fold {fold+1}: {metrics['n_trades']:>4d} trades, "
              f"Ret={metrics['total_return_pct']:>6.2f}%, "
              f"Sharpe={metrics['sharpe_ratio']:>6.2f}, "
              f"WR={metrics['win_rate']:>5.1f}%, "
              f"PF={metrics['profit_factor']:>5.2f}")

    if fold_results:
        returns = [f["return_pct"] for f in fold_results]
        profitable_folds = sum(1 for r in returns if r > 0)
        avg_return = np.mean(returns)
        std_return = np.std(returns)

        print(f"  [WALK-FORWARD SUMMARY]")
        print(f"    Profitable folds: {profitable_folds}/{n_folds} "
              f"({'PASS' if profitable_folds >= n_folds * 0.5 else 'FAIL'})")
        print(f"    Avg return: {avg_return:.2f}% +/- {std_return:.2f}%")

    return {
        "folds": fold_results,
        "profitable_folds": profitable_folds if fold_results else 0,
        "total_folds": n_folds,
        "pass": (profitable_folds >= n_folds * 0.5) if fold_results else False,
    }


def main():
    print("=" * 80)
    print("  SHORT STRATEGIES V2 — BACKTEST + WALK-FORWARD")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    sys.stdout.flush()

    # ── Step 1 : charger les donnees ──
    print("\n[STEP 1] Loading cached data (top 200 tickers)...")
    sys.stdout.flush()
    data = load_cached_data(max_tickers=200)

    if not data:
        print("[ERROR] No data loaded. Run data_fetcher.py first.")
        sys.exit(1)

    total_bars = sum(len(df) for df in data.values())
    print(f"  Loaded: {len(data)} tickers, {total_bars:,} total bars")
    sys.stdout.flush()

    # ── Step 2 : backtest complet ──
    print(f"\n[STEP 2] Running {len(STRATEGIES)} strategies on {len(data)} tickers...")
    sys.stdout.flush()

    output_dir = Path(__file__).parent.parent / "output" / "session_20260326"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}
    all_trades = {}

    for name, cls in STRATEGIES:
        print(f"\n{'-'*60}")
        print(f"  {name.upper()}")
        print(f"{'-'*60}")
        sys.stdout.flush()

        try:
            strategy = cls()
            engine = BacktestEngine(strategy, config.INITIAL_CAPITAL)
            trades_df = engine.run(data)

            if trades_df.empty:
                print(f"  >>> 0 trades — SKIP")
                all_results[name] = {
                    "n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0,
                    "win_rate": 0, "profit_factor": 0, "max_drawdown_pct": 0,
                    "verdict": "SKIP",
                }
                continue

            m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)

            # Compter short vs long
            n_short = len(trades_df[trades_df["direction"] == "SHORT"]) if "direction" in trades_df.columns else 0
            n_long = len(trades_df) - n_short

            print(f"\n  --- RESULTATS ---")
            print(f"  Trades    : {m['n_trades']} ({n_short}S/{n_long}L)")
            print(f"  Net PnL   : ${m['net_pnl']:,.2f}")
            print(f"  Sharpe    : {m['sharpe_ratio']:.2f}")
            print(f"  Win Rate  : {m['win_rate']:.1f}%")
            print(f"  PF        : {m['profit_factor']:.2f}")
            print(f"  Max DD    : {m['max_drawdown_pct']:.2f}%")
            print(f"  Avg Win   : ${m['avg_winner']:,.2f}")
            print(f"  Avg Loss  : ${m['avg_loser']:,.2f}")
            print(f"  R:R       : {m['avg_rr_ratio']:.2f}")

            # Verdict
            is_winner = (
                m["sharpe_ratio"] >= MIN_SHARPE
                and m["profit_factor"] >= MIN_PF
                and m["n_trades"] >= MIN_TRADES
                and m["max_drawdown_pct"] < MAX_DD
                and m["net_pnl"] > 0
            )
            is_potential = m["sharpe_ratio"] > 0 and m["net_pnl"] > 0
            verdict = "WINNER" if is_winner else ("POTENTIEL" if is_potential else "REJETE")
            print(f"  VERDICT   : {verdict}")

            # Sauvegarder les trades en CSV
            csv_path = output_dir / f"trades_short_v2_{name}.csv"
            trades_df.to_csv(csv_path, index=False)

            all_results[name] = {
                k: v for k, v in m.items()
                if k not in ("equity_curve", "by_weekday", "by_ticker")
            }
            all_results[name]["verdict"] = verdict
            all_trades[name] = trades_df

        except Exception as e:
            print(f"  >>> ERROR: {e}")
            import traceback
            traceback.print_exc()
            all_results[name] = {"n_trades": 0, "error": str(e), "verdict": "ERROR"}
        sys.stdout.flush()

    # ── Step 3 : walk-forward pour les strategies avec potentiel ──
    print(f"\n\n{'='*80}")
    print(f"  [STEP 3] WALK-FORWARD VALIDATION")
    print(f"{'='*80}")
    sys.stdout.flush()

    wf_results = {}
    for name, result in all_results.items():
        n_trades = result.get("n_trades", 0)
        verdict = result.get("verdict", "")

        if verdict in ("WINNER", "POTENTIEL") and n_trades >= 10:
            print(f"\n  === Walk-forward: {name} ===")
            sys.stdout.flush()
            cls = dict(STRATEGIES)[name]
            wf = walk_forward(cls, data, n_folds=5)
            wf_results[name] = wf
            all_results[name]["walk_forward"] = wf
        else:
            print(f"\n  === SKIP WF: {name} (verdict={verdict}, trades={n_trades}) ===")

    # ── Step 4 : resume final ──
    print(f"\n\n{'='*95}")
    print(f"  SHORT STRATEGIES V2 — RESULTATS FINAUX")
    print(f"{'='*95}")
    print(f"  {'Strategie':<24} {'Trades':>6} {'Net PnL':>12} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'WF':>6} {'Verdict':>10}")
    print(f"  {'-'*24} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*10}")

    for name, m in all_results.items():
        if "error" in m:
            print(f"  {name:<24} ERROR: {m['error'][:40]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {name:<24}      0                                                        SKIP")
        else:
            s = m.get("sharpe_ratio", 0)
            pf = m.get("profit_factor", 0)
            pnl = m.get("net_pnl", 0)
            n = m.get("n_trades", 0)
            wr = m.get("win_rate", 0)
            dd = m.get("max_drawdown_pct", 0)
            wf_pass = m.get("walk_forward", {}).get("pass", None)
            wf_str = "PASS" if wf_pass is True else ("FAIL" if wf_pass is False else "N/A")
            verdict = m.get("verdict", "?")
            print(f"  {name:<24} {n:>6} ${pnl:>10,.2f} {s:>8.2f} {wr:>5.1f}% {pf:>6.2f} {dd:>5.2f}% {wf_str:>6} {verdict:>10}")

    print(f"{'='*95}")
    sys.stdout.flush()

    # ── Sauvegarder JSON ──
    json_path = output_dir / "short_v2_results.json"
    serializable_results = {}
    for k, v in all_results.items():
        serializable_results[k] = {
            kk: vv for kk, vv in v.items()
            if kk not in ("equity_curve", "by_weekday", "by_ticker")
        }

    with open(json_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "n_tickers": len(data),
            "total_bars": total_bars,
            "validation_thresholds": {
                "min_sharpe": MIN_SHARPE,
                "min_pf": MIN_PF,
                "min_trades": MIN_TRADES,
                "max_dd": MAX_DD,
            },
            "strategies": serializable_results,
        }, f, indent=2, default=str)

    print(f"\n  Resultats sauvegardes : {json_path}")
    print(f"  Trades CSV dans : {output_dir}/")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
