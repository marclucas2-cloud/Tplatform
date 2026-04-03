"""
Run Short P0 — Backtest + Walk-Forward de 4 nouvelles strategies SHORT intraday.

1. Sector Relative Weakness Short
2. High-Beta Underperformance Short
3. Trend Exhaustion Short
4. Late Day Bear Acceleration

Charge les donnees parquet depuis data_cache/, backtest chaque strategie,
walk-forward si trades >= 30, exporte resultats JSON + CSV.

OPTIMISATION : chaque strategie ne recoit que ses tickers requis (pas les 1131).
"""
import sys
import os
import io
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta

# Ajouter le dossier courant au path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
import config
from backtest_engine import BacktestEngine
from utils.metrics import calculate_metrics, print_metrics
from universe import PERMANENT_TICKERS, SECTOR_MAP

# ── Import des 4 strategies ──
from strategies.sector_relative_weakness_short import SectorRelativeWeaknessShortStrategy
from strategies.high_beta_underperf_short import HighBetaUnderperfShortStrategy
from strategies.trend_exhaustion_short import TrendExhaustionShortStrategy
from strategies.late_day_bear_acceleration import LateDayBearAccelerationStrategy


# ── Config ──
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "session_20260326"
IS_DAYS = 60
OOS_DAYS = 30
STEP_DAYS = 30
MIN_TRADES_WF = 30  # Walk-forward si trades >= 30
VALIDATION_CRITERIA = {
    "sharpe_min": 0.5,
    "pf_min": 1.2,
    "trades_min": 15,
    "dd_max": 10,
}


def load_data_from_cache(tickers_filter: list[str] = None) -> dict[str, pd.DataFrame]:
    """Charge les donnees 5Min parquet depuis le cache.
    Si tickers_filter est fourni, ne charge que ces tickers."""
    cache_dir = Path(config.CACHE_DIR)
    if not cache_dir.exists():
        print(f"[ERROR] Cache directory not found: {cache_dir}")
        return {}

    parquet_files = sorted(cache_dir.glob("*_5Min_*.parquet"))
    if not parquet_files:
        print("[ERROR] No parquet files found in cache")
        return {}

    # Grouper par ticker, prendre le fichier avec la plage la plus large
    # (date start la plus ancienne, puis date end la plus recente)
    ticker_files = {}
    for f in parquet_files:
        name = f.stem  # ex: SPY_5Min_20250325_20260325
        parts = name.split("_")
        ticker = parts[0]
        if tickers_filter and ticker not in tickers_filter:
            continue
        # Extraire start_date et end_date du nom
        try:
            start_str = parts[2]  # 20250325
            end_str = parts[3]    # 20260325
            date_range = int(end_str) - int(start_str)  # Plus grand = plus de donnees
        except (IndexError, ValueError):
            date_range = 0
        if ticker not in ticker_files or date_range > ticker_files[ticker][1]:
            ticker_files[ticker] = (f, date_range)

    # Extraire juste les paths
    ticker_files = {t: v[0] for t, v in ticker_files.items()}

    data = {}
    for ticker, fpath in sorted(ticker_files.items()):
        try:
            df = pd.read_parquet(fpath)
            if not df.empty:
                data[ticker] = df
        except Exception as e:
            print(f"  [WARN] {ticker}: {e}")

    total_bars = sum(len(v) for v in data.values())
    print(f"  [DATA] Loaded {len(data)} tickers, {total_bars:,} bars")
    return data


def compute_sma200_for_spy(spy_df: pd.DataFrame) -> dict:
    """Pre-calcule la SMA(200) daily pour SPY a partir des barres 5M."""
    daily_closes = spy_df.groupby(spy_df.index.date)["close"].last()
    sma200 = daily_closes.rolling(200, min_periods=100).mean()
    return sma200.to_dict()


def split_data_by_date(data, start_date, end_date):
    """Filtre le dict de DataFrames entre start et end."""
    filtered = {}
    for ticker, df in data.items():
        mask = (df.index.date >= start_date) & (df.index.date <= end_date)
        sub = df[mask]
        if not sub.empty:
            filtered[ticker] = sub
    return filtered


def run_walk_forward(strategy_class, data, all_dates, sma200_values=None):
    """Execute le walk-forward sur une strategie. Retourne les resultats."""
    total_days = len(all_dates)
    windows = []
    i = 0
    while i + IS_DAYS + OOS_DAYS <= total_days:
        is_start = all_dates[i]
        is_end = all_dates[i + IS_DAYS - 1]
        oos_start = all_dates[i + IS_DAYS]
        oos_end = all_dates[min(i + IS_DAYS + OOS_DAYS - 1, total_days - 1)]
        windows.append((is_start, is_end, oos_start, oos_end))
        i += STEP_DAYS

    if not windows:
        return None

    oos_results = []
    for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
        oos_data = split_data_by_date(data, oos_start, oos_end)
        if not oos_data:
            continue

        strategy = strategy_class()
        if sma200_values is not None:
            strategy._sma200_values = sma200_values

        engine = BacktestEngine(strategy, initial_capital=config.INITIAL_CAPITAL)

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
        print(f"    {status} W{w_idx+1}: OOS {oos_start} -> {oos_end} | "
              f"Ret={metrics['total_return_pct']:>6.2f}% | "
              f"Sharpe={metrics['sharpe_ratio']:>5.2f} | "
              f"PF={metrics['profit_factor']:>4.2f} | "
              f"Trades={metrics['n_trades']:>3d}")

    if not oos_results:
        return None

    profitable_windows = sum(1 for r in oos_results if r["return_pct"] > 0)
    total_windows = len(oos_results)
    hit_rate = profitable_windows / total_windows * 100
    avg_return = np.mean([r["return_pct"] for r in oos_results])
    avg_sharpe = np.mean([r["sharpe"] for r in oos_results])

    verdict = "VALIDATED" if hit_rate >= 50 and avg_return > 0 else "REJECTED"

    return {
        "hit_rate": hit_rate,
        "profitable_windows": profitable_windows,
        "total_windows": total_windows,
        "avg_oos_return": round(avg_return, 2),
        "avg_oos_sharpe": round(avg_sharpe, 2),
        "verdict": verdict,
        "windows": oos_results,
    }


def get_required_tickers_for_strategy(cls):
    """Retourne les tickers necessaires pour une strategie."""
    strategy = cls()
    return strategy.get_required_tickers()


def main():
    print("=" * 80)
    print("  SHORT P0 — 4 Nouvelles Strategies SHORT Intraday")
    print("=" * 80)
    print()

    # ── Strategies avec leurs tickers requis ──
    STRATEGIES = [
        ("sector_weakness", SectorRelativeWeaknessShortStrategy, False),
        ("high_beta_underperf", HighBetaUnderperfShortStrategy, False),
        ("trend_exhaustion", TrendExhaustionShortStrategy, False),
        ("late_day_bear_accel", LateDayBearAccelerationStrategy, True),
    ]

    # ── Pre-calculer SMA200 pour SPY ──
    print("[SMA200] Loading SPY for SMA200 computation...")
    spy_data = load_data_from_cache(tickers_filter=["SPY"])
    sma200_values = {}
    if "SPY" in spy_data:
        sma200_values = compute_sma200_for_spy(spy_data["SPY"])
        n_valid = sum(1 for v in sma200_values.values() if not pd.isna(v))
        print(f"[SMA200] SPY: {n_valid}/{len(sma200_values)} days with valid SMA200")
    else:
        print("[WARN] No SPY data — Late Day Bear Acceleration will skip all days")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for name, cls, needs_sma200 in STRATEGIES:
        print(f"\n{'='*70}")
        print(f"  {name}")
        print(f"{'='*70}")
        sys.stdout.flush()

        try:
            # ── Charger UNIQUEMENT les tickers requis ──
            required_tickers = get_required_tickers_for_strategy(cls)
            print(f"  Required tickers: {len(required_tickers)} -> {required_tickers[:15]}{'...' if len(required_tickers) > 15 else ''}")

            data = load_data_from_cache(tickers_filter=set(required_tickers))
            if not data:
                print(f"  No data — SKIP")
                results[name] = {"n_trades": 0, "verdict": "NO_DATA"}
                continue

            # ── Dates de trading ──
            all_dates = set()
            for df in data.values():
                all_dates.update(df.index.date)
            all_dates = sorted(all_dates)
            print(f"  {len(all_dates)} trading days ({all_dates[0]} -> {all_dates[-1]})")

            strategy = cls()

            # Pre-set SMA200 si necessaire
            if needs_sma200:
                strategy._sma200_values = sma200_values

            engine = BacktestEngine(strategy, config.INITIAL_CAPITAL)
            trades_df = engine.run(data)

            if trades_df.empty:
                print(f"  0 trades — SKIP")
                results[name] = {
                    "n_trades": 0, "net_pnl": 0, "sharpe_ratio": 0,
                    "win_rate": 0, "profit_factor": 0, "max_drawdown_pct": 0,
                    "total_return_pct": 0, "verdict": "NO_TRADES",
                }
                continue

            # ── Metriques ──
            m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
            n_short = len(trades_df[trades_df["direction"] == "SHORT"]) if "direction" in trades_df.columns else 0
            n_long = len(trades_df) - n_short

            print_metrics(strategy.name, m)
            print(f"  Direction: {n_short} SHORT / {n_long} LONG")

            # ── Exporter CSV trades ──
            csv_path = OUTPUT_DIR / f"trades_short_p0_{name}.csv"
            trades_df.to_csv(csv_path, index=False)
            print(f"  [CSV] {csv_path}")

            # ── Verdict initial ──
            s = m.get("sharpe_ratio", 0)
            pf = m.get("profit_factor", 0)
            pnl = m.get("net_pnl", 0)
            n = m.get("n_trades", 0)
            dd = m.get("max_drawdown_pct", 0)

            if s >= VALIDATION_CRITERIA["sharpe_min"] and pf >= VALIDATION_CRITERIA["pf_min"] \
                    and n >= VALIDATION_CRITERIA["trades_min"] and dd < VALIDATION_CRITERIA["dd_max"] and pnl > 0:
                verdict = "WINNER"
            elif s > 0 and pnl > 0:
                verdict = "POTENTIEL"
            else:
                verdict = "REJETE"

            # ── Walk-forward si assez de trades ──
            wf_result = None
            if n >= MIN_TRADES_WF:
                print(f"\n  --- Walk-Forward ({IS_DAYS}j IS / {OOS_DAYS}j OOS) ---")
                wf_result = run_walk_forward(
                    cls, data, all_dates,
                    sma200_values=sma200_values if needs_sma200 else None,
                )
                if wf_result:
                    print(f"\n  WF: {wf_result['profitable_windows']}/{wf_result['total_windows']} "
                          f"windows profitable ({wf_result['hit_rate']:.0f}%)")
                    print(f"  WF Avg OOS Return: {wf_result['avg_oos_return']:.2f}%")
                    print(f"  WF Avg OOS Sharpe: {wf_result['avg_oos_sharpe']:.2f}")
                    print(f"  WF Verdict: {wf_result['verdict']}")

                    if verdict == "WINNER" and wf_result["verdict"] == "VALIDATED":
                        verdict = "WINNER+WF"
                    elif wf_result["verdict"] == "VALIDATED":
                        verdict = f"{verdict}+WF"

            result = {
                "n_trades": n,
                "net_pnl": round(pnl, 2),
                "total_return_pct": m["total_return_pct"],
                "sharpe_ratio": s,
                "win_rate": m["win_rate"],
                "profit_factor": pf,
                "max_drawdown_pct": dd,
                "avg_winner": m["avg_winner"],
                "avg_loser": m["avg_loser"],
                "avg_rr_ratio": m["avg_rr_ratio"],
                "n_short": n_short,
                "n_long": n_long,
                "verdict": verdict,
            }
            if wf_result:
                result["walk_forward"] = {
                    "hit_rate": wf_result["hit_rate"],
                    "avg_oos_return": wf_result["avg_oos_return"],
                    "avg_oos_sharpe": wf_result["avg_oos_sharpe"],
                    "wf_verdict": wf_result["verdict"],
                }

            results[name] = result

        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            results[name] = {"n_trades": 0, "error": str(e)}

        sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print(f"\n\n{'='*100}")
    print(f"  SHORT P0 — RESULTATS FINAUX")
    print(f"{'='*100}")
    print(f"  {'Strategie':<25} {'Trades':>6} {'Net PnL':>12} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Verdict':>14}")
    print(f"  {'-'*25} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*14}")

    for name, m in results.items():
        if "error" in m:
            print(f"  {name:<25} ERROR: {m['error'][:50]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {name:<25}      0                                                       {m.get('verdict', 'NO_TRADES')}")
        else:
            print(f"  {name:<25} {m['n_trades']:>6} ${m['net_pnl']:>10,.2f} "
                  f"{m['sharpe_ratio']:>8.2f} {m['win_rate']:>5.1f}% "
                  f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>5.2f}% "
                  f"{m['verdict']:>14}")

    # ── Exporter JSON ──
    json_path = OUTPUT_DIR / "short_p0_results.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [JSON] {json_path}")

    # ── Count winners ──
    winners = [n for n, m in results.items() if "WINNER" in m.get("verdict", "")]
    potentiels = [n for n, m in results.items() if "POTENTIEL" in m.get("verdict", "")]

    print(f"\n  WINNERS: {len(winners)}")
    for w in winners:
        print(f"    + {w}")
    print(f"  POTENTIELS: {len(potentiels)}")
    for p in potentiels:
        print(f"    ~ {p}")

    return results


if __name__ == "__main__":
    main()
