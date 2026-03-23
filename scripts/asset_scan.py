"""
Asset Scan — teste une strategie sur tout l'univers d'actifs.

Usage :
    # Scanner RSI sur tous les actifs (forex + indices + stocks + crypto)
    python scripts/asset_scan.py --strategy rsi_mean_reversion

    # Limiter a une classe d'actifs
    python scripts/asset_scan.py --strategy rsi_mean_reversion --class forex
    python scripts/asset_scan.py --strategy momentum_burst --class stocks
    python scripts/asset_scan.py --strategy bb_squeeze --class crypto

    # Plusieurs classes
    python scripts/asset_scan.py --strategy vwap --class forex,indices

    # Timeframe specifique (override celui du JSON)
    python scripts/asset_scan.py --strategy rsi_mean_reversion --timeframe 1H

    # Exporter les resultats
    python scripts/asset_scan.py --strategy rsi_mean_reversion --export scan_rsi.csv

Sortie :
    - Tableau tri par Sharpe, groupe par classe d'actif
    - Top 5 actifs ou la strategie fonctionne le mieux
    - Actifs a eviter (Sharpe negatif ou 0 trades)
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.loader import OHLCVLoader
from core.data.universe import UNIVERSE, get_all_assets, Asset
from core.strategy_schema.validator import StrategyValidator
from core.backtest.engine import BacktestEngine
from core.ranking.ranker import StrategyRanker

STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"

# Timeframe par defaut selon la classe d'actif
# (les strategiesnpeuvent specifier leur propre TF)
DEFAULT_TF_MAP = {
    "forex":       "1H",
    "indices":     "1H",
    "stocks":      "1D",  # stocks : daily plus fiable (spreads eleves en intraday)
    "crypto":      "1H",
    "commodities": "1D",
}

# Periode yfinance par timeframe
PERIOD_MAP = {
    "1M": "7d",
    "5M": "60d",
    "15M": "60d",
    "1H": "2y",
    "4H": "2y",
    "1D": "5y",
}


def resolve_strategy(name: str) -> Path | None:
    for p in sorted(STRATEGIES_DIR.glob("*.json")):
        if name.lower() in p.stem.lower():
            return p
    return None


def fetch_asset_data(asset: Asset, timeframe: str):
    """Telecharge les donnees pour un actif, retourne None si echec."""
    # Fallback pour les timeframes courts
    actual_tf = timeframe
    if timeframe in ("1M", "5M"):
        actual_tf = "1H"

    period = PERIOD_MAP.get(actual_tf, "1y")
    try:
        return OHLCVLoader.from_yfinance(asset.symbol, actual_tf, period=period,
                                          ticker=asset.ticker)
    except Exception:
        return None


def scan_asset(asset: Asset, strategy: dict, engine: BacktestEngine,
               timeframe: str) -> dict | None:
    """Lance le backtest d'une strategie sur un actif."""
    data = fetch_asset_data(asset, timeframe)
    if data is None or data.n_bars < 100:
        return None

    # Adapter le cost model au spread de l'actif
    import copy
    s = copy.deepcopy(strategy)
    # Convertir le spread de l'actif en % si cost_model utilise le nouveau format
    if "spread_pct" in s["cost_model"]:
        # spread_pips * pip_value donne un montant absolu; on garde le default du JSON
        pass
    else:
        s["cost_model"]["spread_pips"] = asset.spread_pips

    try:
        result = engine.run(data, s)
        d = result.to_dict()
        d["asset_symbol"]  = asset.symbol
        d["asset_name"]    = asset.name
        d["asset_class"]   = asset.asset_class
        d["n_bars"]        = data.n_bars
        d["period_start"]  = str(data.df.index[0].date())
        d["period_end"]    = str(data.df.index[-1].date())
        d["equity_curve"]  = result.equity_curve
        return d
    except Exception as e:
        return None


def print_scan_results(results: list[dict], strategy_id: str):
    """Affiche les resultats groupes par classe d'actif."""
    ranker = StrategyRanker()
    ranked = ranker.rank(results)
    rank_map = {r.strategy_id: r for r in ranked}

    print(f"\n{'='*100}")
    print(f"  ASSET SCAN : {strategy_id} — {len(results)} actifs testes")
    print(f"{'='*100}")

    # Grouper par classe
    classes_order = ["forex", "indices", "stocks", "crypto", "commodities"]
    results_by_class: dict[str, list[dict]] = {}
    for r in results:
        cls = r.get("asset_class", "autre")
        results_by_class.setdefault(cls, []).append(r)

    header = (f"  {'Actif':<14} {'Nom':<22} {'Sharpe':>7} {'DD%':>6} "
              f"{'WR%':>6} {'PF':>5} {'Trades':>7} {'Return%':>8} {'Score':>6}")
    sep = f"  {'-'*90}"

    all_sorted: list[dict] = []

    for cls in classes_order:
        cls_results = results_by_class.get(cls, [])
        if not cls_results:
            continue

        cls_results.sort(key=lambda x: x.get("sharpe_ratio", -999), reverse=True)
        all_sorted.extend(cls_results)

        print(f"\n  --- {cls.upper()} ({len(cls_results)} actifs) ---")
        print(header)
        print(sep)

        for r in cls_results:
            sid = r["strategy_id"]
            rr = rank_map.get(sid)
            score_str = f"{rr.score:>6.1f}" if rr else "   N/A"
            valid = "[OK]" if r.get("passes_validation") else "    "
            print(
                f"  {r['asset_symbol']:<14} {r['asset_name']:<22} "
                f"{r['sharpe_ratio']:>+7.3f} {r['max_drawdown_pct']:>6.1f} "
                f"{r['win_rate_pct']:>6.1f} {r['profit_factor']:>5.2f} "
                f"{r['total_trades']:>7} {r['total_return_pct']:>+8.2f} "
                f"{score_str} {valid}"
            )

    # Top 5 globaux
    top5 = sorted(results, key=lambda x: x.get("sharpe_ratio", -999), reverse=True)[:5]
    worst5 = sorted(results, key=lambda x: x.get("sharpe_ratio", 999))[:5]

    print(f"\n{'='*100}")
    print(f"  TOP 5 — Meilleurs actifs pour {strategy_id}")
    print(f"{'='*100}")
    for i, r in enumerate(top5, 1):
        v = "[VALIDE]" if r.get("passes_validation") else ""
        print(f"  {i}. {r['asset_symbol']:<12} ({r['asset_class']:<12}) "
              f"Sharpe={r['sharpe_ratio']:+.3f}  DD={r['max_drawdown_pct']:.1f}%  "
              f"Trades={r['total_trades']}  {v}")

    print(f"\n  TOP 5 — Actifs a EVITER")
    for i, r in enumerate(worst5, 1):
        print(f"  {i}. {r['asset_symbol']:<12} ({r['asset_class']:<12}) "
              f"Sharpe={r['sharpe_ratio']:+.3f}  Trades={r['total_trades']}")

    # Stats globales
    valid = [r for r in results if r.get("passes_validation")]
    positive_sharpe = [r for r in results if r.get("sharpe_ratio", 0) > 0]
    print(f"\n  Strategies validees (Sharpe>min + DD<max) : {len(valid)}/{len(results)}")
    print(f"  Sharpe positif                            : {len(positive_sharpe)}/{len(results)}")
    if positive_sharpe:
        avg_sh = sum(r["sharpe_ratio"] for r in positive_sharpe) / len(positive_sharpe)
        print(f"  Sharpe moyen (positifs seulement)         : {avg_sh:.3f}")


def export_csv(results: list[dict], path: str):
    """Exporte les resultats en CSV."""
    fields = [
        "asset_symbol", "asset_name", "asset_class", "strategy_id",
        "n_bars", "period_start", "period_end",
        "sharpe_ratio", "sortino_ratio", "max_drawdown_pct",
        "profit_factor", "win_rate_pct", "total_trades",
        "total_return_pct", "expectancy", "total_costs",
        "passes_validation",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(results, key=lambda x: x.get("sharpe_ratio", -999), reverse=True):
            writer.writerow({k: r.get(k, "") for k in fields})
    print(f"\n  Export CSV : {path}")


def main():
    parser = argparse.ArgumentParser(description="Scanner une strategie sur tous les actifs")
    parser.add_argument("--strategy", required=True,
                        help="Nom (partiel) de la strategie")
    parser.add_argument("--class", dest="asset_class", default="",
                        help="Classes comma-separated : forex,indices,stocks,crypto,commodities")
    parser.add_argument("--timeframe", default="",
                        help="Override le timeframe du JSON (ex: 1H)")
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--export", default="",
                        help="Exporter en CSV")
    parser.add_argument("--top", type=int, default=0,
                        help="Limiter a N actifs par classe (0=tous)")
    args = parser.parse_args()

    # Trouver la strategie
    path = resolve_strategy(args.strategy)
    if path is None:
        print(f"Strategie '{args.strategy}' introuvable")
        sys.exit(1)

    validator = StrategyValidator()
    strategy = validator.load_and_validate(path)
    sid = strategy["strategy_id"]

    # Timeframe
    timeframe = args.timeframe.upper() if args.timeframe else strategy["timeframe"]

    # Selectionner les actifs
    if args.asset_class:
        classes = [c.strip() for c in args.asset_class.split(",")]
        assets_to_test = [a for cls in classes
                         for a in UNIVERSE.get(cls, [])]
    else:
        assets_to_test = get_all_assets()

    if args.top > 0:
        # Prendre les N premiers de chaque classe
        from collections import defaultdict
        by_class: dict[str, list] = defaultdict(list)
        for a in assets_to_test:
            by_class[a.asset_class].append(a)
        assets_to_test = [a for assets in by_class.values() for a in assets[:args.top]]

    print(f"\nAsset Scan : {sid} sur {len(assets_to_test)} actifs (TF={timeframe})")
    print(f"Classes    : {set(a.asset_class for a in assets_to_test)}")
    print(f"Telechargement et backtest en cours...\n")

    engine = BacktestEngine(initial_capital=args.capital)
    results = []
    t0 = time.time()

    for i, asset in enumerate(assets_to_test, 1):
        print(f"  [{i:>2}/{len(assets_to_test)}] {asset.symbol:<12} ({asset.asset_class})", end=" ")
        r = scan_asset(asset, strategy, engine, timeframe)
        if r is None:
            print("-> SKIP (pas de donnees ou erreur)")
            continue

        sharpe = r["sharpe_ratio"]
        trades = r["total_trades"]
        valid = "[OK]" if r.get("passes_validation") else ""
        print(f"-> Sharpe={sharpe:+.3f}  Trades={trades}  {valid}")
        results.append(r)

        # Pause courte pour eviter le rate limit yfinance
        if i % 5 == 0:
            time.sleep(0.5)

    elapsed = time.time() - t0
    print(f"\n  Scan complete en {elapsed:.1f}s — {len(results)}/{len(assets_to_test)} actifs")

    if not results:
        print("Aucun resultat.")
        sys.exit(0)

    print_scan_results(results, sid)

    if args.export:
        export_csv(results, args.export)


if __name__ == "__main__":
    main()
