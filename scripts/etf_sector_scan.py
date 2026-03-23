#!/usr/bin/env python3
"""
Scan RSI Mean Reversion sur ETFs sectoriels + Grid Search IS/OOS.

Hypothese : les ETFs sectoriels sont moins efficients que SPY,
donc la mean reversion a plus de chances d'y fonctionner.

Etapes pour chaque ETF :
  1. Telecharge 5 ans de donnees daily (yfinance)
  2. Grid search sur parametres RSI (IS 70% / OOS 30%)
  3. Walk-forward 3 fenetres sur IS
  4. Evaluation OOS du meilleur
  5. Rapport comparatif

Univers : 11 sector SPDRs + IWM (baseline valide)

Usage :
    python scripts/etf_sector_scan.py
    python scripts/etf_sector_scan.py --capital 100000
    python scripts/etf_sector_scan.py --save  # sauvegarde les JSONs gagnants
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.optimization.grid_search import GridSearch

STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"


# ─── Configuration ────────────────────────────────────────────────────────────

# ETFs a scanner (secteurs SPDR + baseline)
ETF_UNIVERSE = [
    ("IWM",  "Russell 2000 (baseline)"),
    ("XLF",  "Financials"),
    ("XLE",  "Energy"),
    ("XLK",  "Technology"),
    ("XLV",  "Healthcare"),
    ("XLI",  "Industrials"),
    ("XLC",  "Communication"),
    ("XLY",  "Consumer Discret."),
    ("XLP",  "Consumer Staples"),
    ("XLU",  "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLB",  "Materials"),
]

# Grille de parametres RSI a explorer
RSI_GRID = {
    "rsi_period":     [2, 5, 8, 10, 14],
    "oversold":       [20, 25, 30],
    "overbought":     [65, 70, 75],
    "stop_loss_pct":  [1.0, 1.5, 2.0],
    "take_profit_pct":[2.5, 3.5, 5.0],
}

# Strategie de base (template)
BASE_STRATEGY = {
    "strategy_id": "rsi_scan",
    "version": "1.0.0",
    "created_at": "2026-03-23T00:00:00Z",
    "description": "RSI Mean Reversion — scan sectoriel",
    "asset": "PLACEHOLDER",
    "timeframe": "1D",
    "parameters": {
        "rsi_period": 10,
        "oversold": 30,
        "overbought": 70,
        "stop_loss_pct": 1.5,
        "take_profit_pct": 3.5,
        "trailing_stop_pct": 0.5,
        "max_position_pct": 0.05,
    },
    "entry_rules": {
        "long":  "RSI crosses above oversold threshold",
        "short": "RSI crosses below overbought threshold",
    },
    "exit_rules": {
        "long":  "stop_loss OR take_profit OR trailing_stop OR signal_short",
        "short": "stop_loss OR take_profit OR trailing_stop OR signal_long",
    },
    "cost_model": {
        "spread_pct": 0.01,
        "slippage_pct": 0.02,
        "commission_per_lot": 0.0,
    },
    "risk_management": {
        "max_position_pct": 0.05,
        "max_open_trades": 1,
        "daily_loss_limit_pct": 0.04,
    },
    "validation_requirements": {
        "min_trades": 20,
        "min_sharpe": 0.5,
        "max_drawdown_pct": 25.0,
        "walk_forward_windows": 4,
        "out_of_sample_pct": 30,
        "min_profit_factor": 1.2,
    },
    "tags": ["mean_reversion", "rsi", "etf", "sector", "daily"],
}


def scan_etf(ticker: str, name: str, capital: float,
             save: bool) -> dict | None:
    """Lance le grid search RSI sur un ETF."""
    print(f"\n  [{ticker:>5}] {name}...", end=" ", flush=True)

    # Charger les donnees
    try:
        data = OHLCVLoader.from_yfinance(ticker, "1D", period="5y")
    except Exception as e:
        print(f"ERREUR donnees: {e}")
        return None

    n_bars = data.n_bars
    print(f"{n_bars} barres", end=" ", flush=True)

    if n_bars < 500:
        print("-> SKIP (pas assez de donnees)")
        return None

    # Preparer la strategie
    strategy = copy.deepcopy(BASE_STRATEGY)
    strategy["asset"] = ticker
    strategy["strategy_id"] = f"rsi_{ticker.lower()}_scan"

    # Grid search — couts en % du prix (plus besoin de pip_value)
    gs = GridSearch(initial_capital=capital, wf_windows=3)

    t0 = time.time()
    results = gs.run(strategy, data, RSI_GRID)
    elapsed = time.time() - t0

    if not results:
        print(f"-> 0 resultats ({elapsed:.0f}s)")
        return None

    # Evaluer le meilleur sur OOS
    best = gs.best(results)
    gs.evaluate_oos(best, strategy, data)

    verdict = "POSITIF" if best.oos_sharpe > 0 else "NEGATIF"
    robust = ""
    if best.oos_sharpe > 0.8:
        robust = " *** ROBUSTE ***"
    elif best.oos_sharpe > 0.5:
        robust = " * PROMETTEUR *"

    print(f"-> {len(results)} combos, best IS Sharpe {best.is_sharpe:+.3f}, "
          f"OOS Sharpe {best.oos_sharpe:+.3f} [{verdict}]{robust} ({elapsed:.0f}s)")

    # Sauvegarder si demande et OOS positif
    if save and best.oos_sharpe > 0.5 and best.oos_trades >= 10:
        save_winner(ticker, strategy, best)

    return {
        "ticker": ticker,
        "name": name,
        "n_bars": n_bars,
        "n_combos": len(results),
        "best_params": best.params,
        # IS
        "is_sharpe": best.is_sharpe,
        "is_pf": best.is_profit_factor,
        "is_wr": best.is_win_rate,
        "is_dd": best.is_max_dd,
        "is_trades": best.is_trades,
        "is_return": best.is_return_pct,
        "is_wf_mean": best.is_wf_sharpe_mean,
        # OOS
        "oos_sharpe": best.oos_sharpe,
        "oos_pf": best.oos_profit_factor,
        "oos_wr": best.oos_win_rate,
        "oos_dd": best.oos_max_dd,
        "oos_trades": best.oos_trades,
        "oos_return": best.oos_return_pct,
        "elapsed": elapsed,
    }


def save_winner(ticker: str, base_strategy: dict, best) -> None:
    """Sauvegarde les meilleurs params en JSON."""
    import datetime
    s = copy.deepcopy(base_strategy)
    s["parameters"].update(best.params)
    s["strategy_id"] = f"rsi_{ticker.lower()}_1d_opt_v1"
    s["asset"] = ticker
    s["created_at"] = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s["description"] = (
        f"RSI Mean Reversion sur {ticker} daily — optimise par grid search. "
        f"IS Sharpe {best.is_sharpe:+.3f}, OOS Sharpe {best.oos_sharpe:+.3f}."
    )
    s["cost_model"] = {
        "spread_pct": 0.01,
        "slippage_pct": 0.02,
        "commission_per_lot": 0.0,
    }
    out = STRATEGIES_DIR / f"rsi_{ticker.lower()}_1d_opt_v1.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    print(f"         -> JSON sauvegarde : {out.name}")


def print_results(results: list[dict]) -> None:
    """Affiche le tableau de resultats."""
    print(f"\n{'='*120}")
    print("  SCAN RSI MEAN REVERSION — ETFs SECTORIELS (Grid Search IS/OOS + Walk-Forward)")
    print(f"{'='*120}")
    print()

    # Header
    print(f"  {'ETF':<6} {'Secteur':<22} "
          f"{'IS_Sh':>7} {'IS_PF':>6} {'IS_WR':>6} {'IS_Tr':>6} {'WF_Sh':>7} | "
          f"{'OOS_Sh':>7} {'OOS_PF':>7} {'OOS_WR':>7} {'OOS_Tr':>7}  {'Verdict'}")
    print(f"  {'-'*115}")

    for r in sorted(results, key=lambda x: x["oos_sharpe"], reverse=True):
        # Verdict
        if r["oos_sharpe"] > 0.8 and r["oos_trades"] >= 10:
            verdict = "*** ROBUSTE ***"
        elif r["oos_sharpe"] > 0.5 and r["oos_trades"] >= 10:
            verdict = "* PROMETTEUR *"
        elif r["oos_sharpe"] > 0:
            verdict = "positif"
        else:
            verdict = "REJET"

        print(
            f"  {r['ticker']:<6} {r['name']:<22} "
            f"{r['is_sharpe']:>+7.3f} {r['is_pf']:>6.2f} {r['is_wr']:>5.1f}% {r['is_trades']:>6} "
            f"{r['is_wf_mean']:>+7.3f} | "
            f"{r['oos_sharpe']:>+7.3f} {r['oos_pf']:>7.2f} {r['oos_wr']:>6.1f}% {r['oos_trades']:>7}  "
            f"{verdict}"
        )

    print(f"  {'-'*115}")

    # Resume
    positifs = [r for r in results if r["oos_sharpe"] > 0]
    robustes = [r for r in results if r["oos_sharpe"] > 0.8 and r["oos_trades"] >= 10]

    print(f"\n  Resultats : {len(results)} ETFs scannes, "
          f"{len(positifs)} OOS positifs, {len(robustes)} ROBUSTES")

    if robustes:
        print(f"\n  *** ETFs ROBUSTES (OOS Sharpe > 0.8) ***")
        for r in robustes:
            p = r["best_params"]
            print(f"  {r['ticker']:<6} Sharpe OOS {r['oos_sharpe']:+.3f} | "
                  f"RSI({p.get('rsi_period',14)}) "
                  f"OS={p.get('oversold',30)} OB={p.get('overbought',70)} "
                  f"SL={p.get('stop_loss_pct',1.5)}% TP={p.get('take_profit_pct',3.5)}%")

    # Prometteurs
    prometteurs = [r for r in results
                   if 0.5 < r["oos_sharpe"] <= 0.8 and r["oos_trades"] >= 10]
    if prometteurs:
        print(f"\n  * ETFs PROMETTEURS (OOS Sharpe 0.5-0.8) *")
        for r in prometteurs:
            p = r["best_params"]
            print(f"  {r['ticker']:<6} Sharpe OOS {r['oos_sharpe']:+.3f} | "
                  f"RSI({p.get('rsi_period',14)}) "
                  f"OS={p.get('oversold',30)} OB={p.get('overbought',70)} "
                  f"SL={p.get('stop_loss_pct',1.5)}% TP={p.get('take_profit_pct',3.5)}%")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Scan RSI Mean Reversion sur ETFs sectoriels")
    parser.add_argument("--capital", type=float, default=100_000,
                        help="Capital initial (defaut: 100000)")
    parser.add_argument("--save", action="store_true",
                        help="Sauvegarder les JSONs des ETFs robustes")
    args = parser.parse_args()

    n_combos = 1
    for v in RSI_GRID.values():
        n_combos *= len(v)

    print(f"\n{'='*80}")
    print(f"  SCAN RSI MEAN REVERSION — ETFs SECTORIELS")
    print(f"{'='*80}")
    print(f"  Capital     : ${args.capital:,.0f}")
    print(f"  Position    : 5% du capital par trade")
    print(f"  Couts       : spread 0.01% + slippage 0.02% du prix")
    print(f"  Donnees     : yfinance 1D, 5 ans")
    print(f"  Grid        : {n_combos} combinaisons x {len(ETF_UNIVERSE)} ETFs")
    print(f"  WF          : 3 fenetres sur IS (70/30)")
    print(f"  Sauvegarde  : {'OUI' if args.save else 'NON'}")
    print(f"{'='*80}")

    t_start = time.time()
    results = []

    for ticker, name in ETF_UNIVERSE:
        r = scan_etf(ticker, name, args.capital, args.save)
        if r is not None:
            results.append(r)

    t_total = time.time() - t_start
    print(f"\n  Scan termine en {t_total:.0f}s ({t_total/60:.1f} min)")

    if results:
        print_results(results)


if __name__ == "__main__":
    main()
