"""
Batch Backtest — toutes les stratégies sur vraies données Yahoo Finance.

Usage :
    python scripts/batch_backtest.py                  # toutes les stratégies
    python scripts/batch_backtest.py --strategy rsi   # filtre par nom
    python scripts/batch_backtest.py --timeframe 1H   # filtre par timeframe
    python scripts/batch_backtest.py --export results.csv

Ce script :
  1. Charge toutes les stratégies dans strategies/*.json
  2. Télécharge les données réelles depuis yfinance
  3. Lance le backtest + walk-forward validation
  4. Affiche le leaderboard trié par score
  5. Exporte les résultats en CSV (optionnel)
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

# Ajouter le root au path pour imports relatifs
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader
from core.portfolio.correlation import PortfolioCorrelation
from core.ranking.ranker import StrategyRanker
from core.strategy_schema.validator import StrategyValidator

logging.basicConfig(level=logging.WARNING)  # Silencer les logs info pendant le batch


# ─── Configuration des assets par timeframe ──────────────────────────────────

# Période de téléchargement recommandée par timeframe
PERIOD_FOR_TIMEFRAME = {
    "1M":  "7d",
    "5M":  "60d",
    "15M": "60d",
    "1H":  "2y",
    "4H":  "2y",
    "1D":  "5y",
}

# Pour les timeframes intraday courts, on remonte en 1H pour avoir plus d'historique
FALLBACK_TIMEFRAME = {
    "1M": "1H",   # 1M -> on teste sur 1H (2 ans) si pas assez de données 1M
    "5M": "1H",   # idem
}


def load_all_strategies(strategies_dir: Path, name_filter: str = "",
                        tf_filter: str = "") -> list[dict]:
    """Charge et valide tous les fichiers JSON de strategies/."""
    validator = StrategyValidator()
    strategies = []

    for path in sorted(strategies_dir.glob("*.json")):
        try:
            s = validator.load_and_validate(path)
            if name_filter and name_filter.lower() not in s["strategy_id"].lower():
                continue
            if tf_filter and s["timeframe"] != tf_filter.upper():
                continue
            strategies.append(s)
        except Exception as e:
            print(f"  [SKIP] {path.name} : {e}")

    return strategies


def fetch_data(asset: str, timeframe: str, verbose: bool = True) -> OHLCVData | None:
    """Télécharge les données yfinance avec fallback si timeframe intraday trop court."""
    actual_tf = timeframe
    fallback_used = False

    # Pour 1M/5M : peu d'historique disponible — tenter le fallback 1H
    if timeframe in FALLBACK_TIMEFRAME:
        fallback_tf = FALLBACK_TIMEFRAME[timeframe]
        if verbose:
            print(f"    [INFO] {timeframe} -> fallback sur {fallback_tf} (limite yfinance)")
        actual_tf = fallback_tf
        fallback_used = True

    period = PERIOD_FOR_TIMEFRAME.get(actual_tf, "1y")

    try:
        data = OHLCVLoader.from_yfinance(asset, actual_tf, period=period)
        if verbose:
            label = f"{asset} {actual_tf}"
            if fallback_used:
                label += f" (demandé: {timeframe})"
            print(f"    [DATA] {label} — {data.n_bars} barres "
                  f"({data.df.index[0].date()} -> {data.df.index[-1].date()})")
        return data
    except Exception as e:
        if verbose:
            print(f"    [ERREUR] {asset} {actual_tf} : {e}")
        return None


def run_backtest(strategy: dict, data, engine: BacktestEngine) -> dict | None:
    """Lance un backtest et retourne le dict résultat enrichi."""
    try:
        result = engine.run(data, strategy)
        d = result.to_dict()
        d["equity_curve"] = result.equity_curve  # pour la corrélation portfolio
        return d
    except Exception as e:
        print(f"    [ERREUR BACKTEST] {strategy['strategy_id']} : {e}")
        return None


def run_walk_forward(strategy: dict, data, engine: BacktestEngine,
                     n_windows: int = 5) -> list[float]:
    """Walk-forward : retourne la liste des Sharpe OOS."""
    try:
        windows = data.walk_forward_windows(n_windows=n_windows, oos_pct=0.3)
        sharpes = []
        for is_data, oos_data in windows:
            if len(oos_data.df) < 30:
                continue
            r = engine.run(oos_data, strategy)
            sharpes.append(r.sharpe_ratio)
        return sharpes
    except Exception:
        return []


def print_summary(results: list[dict], ranked, portfolio_result=None):
    """Affiche un résumé complet."""
    print(f"\n{'='*90}")
    print(f"  RESULTATS BATCH BACKTEST — {len(results)} strategies testees")
    print(f"{'='*90}")

    # Leaderboard
    ranker = StrategyRanker()
    ranker.print_leaderboard(ranked)

    # Métriques supplémentaires
    print(f"\n  {'Strategy':<38} {'Expectancy':>12} {'Sharpe_std':>11} {'WF_Sharpe':>10} {'WF_std':>7}")
    print(f"  {'-'*80}")
    for d in sorted(results, key=lambda x: x.get("sharpe_ratio", 0), reverse=True):
        wf = d.get("wf_sharpes", [])
        wf_mean = f"{sum(wf)/len(wf):+.3f}" if wf else "   N/A"
        wf_std  = f"{(sum((x - sum(wf)/len(wf))**2 for x in wf)/len(wf))**0.5:.3f}" if len(wf) > 1 else "  N/A"
        print(
            f"  {d['strategy_id']:<38} "
            f"{d.get('expectancy', 0):>+12.6f} "
            f"{d.get('rolling_sharpe_std', 0):>11.3f} "
            f"{wf_mean:>10} "
            f"{wf_std:>7}"
        )

    # Portfolio allocation
    if portfolio_result:
        print(portfolio_result.summary())

    # Stats globales
    valid = [r for r in results if r.get("passes_validation")]
    print(f"\n  Strategies validees : {len(valid)}/{len(results)}")
    if valid:
        best = max(valid, key=lambda x: x.get("sharpe_ratio", 0))
        print(f"  Meilleur Sharpe     : {best['strategy_id']} ({best['sharpe_ratio']:.3f})")


def export_csv(results: list[dict], ranked, path: str):
    """Exporte les résultats en CSV."""
    rank_map = {r.strategy_id: r for r in ranked}
    fields = [
        "rank", "strategy_id", "score", "asset", "timeframe", "period",
        "sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "profit_factor",
        "win_rate_pct", "total_trades", "total_return_pct",
        "expectancy", "rolling_sharpe_std", "total_costs", "passes_validation",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for d in results:
            sid = d["strategy_id"]
            row = {k: d.get(k, "") for k in fields}
            if sid in rank_map:
                row["rank"] = rank_map[sid].rank
                row["score"] = rank_map[sid].score
            writer.writerow(row)
    print(f"\n  Export CSV : {path}")


def main():
    parser = argparse.ArgumentParser(description="Batch backtest sur vraies données")
    parser.add_argument("--strategy", default="", help="Filtre sur le nom de stratégie")
    parser.add_argument("--timeframe", default="", help="Filtre sur le timeframe (ex: 1H)")
    parser.add_argument("--capital", type=float, default=10_000, help="Capital initial")
    parser.add_argument("--export", default="", help="Exporter résultats en CSV")
    parser.add_argument("--no-walkforward", action="store_true", help="Désactiver le walk-forward")
    parser.add_argument("--no-portfolio", action="store_true", help="Désactiver l'analyse portfolio")
    args = parser.parse_args()

    strategies_dir = Path(__file__).parent.parent / "strategies"
    engine = BacktestEngine(initial_capital=args.capital)

    # 1. Charger les stratégies
    print(f"\nChargement des strategies depuis {strategies_dir}...")
    strategies = load_all_strategies(strategies_dir, args.strategy, args.timeframe)
    if not strategies:
        print("Aucune strategie trouvee. Verifier les filtres.")
        sys.exit(0)
    print(f"  {len(strategies)} strategies chargees")

    # 2. Télécharger les données et lancer les backtests
    results = []
    t0 = time.time()

    for s in strategies:
        sid = s["strategy_id"]
        asset = s["asset"]
        tf = s["timeframe"]
        print(f"\n[{sid}]")

        data = fetch_data(asset, tf)
        if data is None:
            continue

        # Backtest principal
        result = run_backtest(s, data, engine)
        if result is None:
            continue

        # Walk-forward
        if not args.no_walkforward:
            wf_sharpes = run_walk_forward(s, data, engine, n_windows=5)
            result["wf_sharpes"] = wf_sharpes
            if wf_sharpes:
                print(f"    [WF] {len(wf_sharpes)} fenetres OOS — "
                      f"Sharpe moyen: {sum(wf_sharpes)/len(wf_sharpes):+.3f}")

        # Affichage rapide
        v = "[OK]" if result["passes_validation"] else "[--]"
        print(f"    {v} Sharpe={result['sharpe_ratio']:+.3f}  "
              f"DD={result['max_drawdown_pct']:.1f}%  "
              f"WR={result['win_rate_pct']:.0f}%  "
              f"Trades={result['total_trades']}  "
              f"Return={result['total_return_pct']:+.1f}%")

        results.append(result)

    elapsed = time.time() - t0
    print(f"\n  Backtests completes en {elapsed:.1f}s")

    if not results:
        print("Aucun resultat. Verifier les donnees et les strategies.")
        sys.exit(0)

    # 3. Ranking
    ranker = StrategyRanker()
    ranked = ranker.rank(results)

    # 4. Portfolio correlation
    portfolio_result = None
    if not args.no_portfolio and len(results) >= 2:
        pc = PortfolioCorrelation(max_weight=0.40)
        portfolio_result = pc.allocate(results)

    # 5. Affichage final
    print_summary(results, ranked, portfolio_result)

    # 6. Export CSV
    if args.export:
        export_csv(results, ranked, args.export)


if __name__ == "__main__":
    main()
