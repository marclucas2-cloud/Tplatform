"""
Pairs Trading Backtest — long/short intra-secteur sur données journalières.

Usage :
    # Scanner toutes les paires tech
    python scripts/pairs_backtest.py --sector tech_us

    # Tous les secteurs
    python scripts/pairs_backtest.py --all

    # Paire spécifique
    python scripts/pairs_backtest.py --pair AAPL MSFT

    # Afficher uniquement la coïntégration (sans backtest)
    python scripts/pairs_backtest.py --sector tech_us --discover-only

    # Exporter les résultats
    python scripts/pairs_backtest.py --all --export results/pairs_results.csv

Sortie :
    - Table coïntégration : ADF p-value, half-life, corrélation
    - Leaderboard backtest : Sharpe, DD, WR, trades, return
    - Top 3 meilleures paires par secteur
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.loader import OHLCVLoader
from core.data.pairs import PairDiscovery, SECTOR_MAP
from core.backtest.pairs_engine import PairsBacktestEngine, PairsBacktestResult


# ─── Helpers ─────────────────────────────────────────────────────────────────

def fetch_sector_data(sector: str, verbose: bool = True) -> dict:
    """Télécharge les données 1D (5 ans) pour chaque actif du secteur."""
    from core.data.universe import get_asset
    symbols = SECTOR_MAP.get(sector, [])
    data = {}
    for sym in symbols:
        try:
            asset = get_asset(sym)
            ticker = asset.ticker if asset else None
            d = OHLCVLoader.from_yfinance(sym, "1D", period="5y", ticker=ticker)
            data[sym] = d
            if verbose:
                print(f"  {sym:<8} {d.n_bars} barres  "
                      f"({d.df.index[0].date()} -> {d.df.index[-1].date()})")
        except Exception as e:
            if verbose:
                print(f"  {sym:<8} ECHEC — {e}")
    return data


def print_discovery_table(pairs_stats):
    """Affiche la table de coïntégration."""
    if not pairs_stats:
        print("  Aucune paire trouvée avec les filtres actuels.")
        return
    print(f"\n  {'Paire':<16} {'Corr':>6} {'beta':>7} {'ADF_p':>6} {'HL(j)':>7} {'Coint':>6} {'N':>5}")
    print(f"  {'-'*58}")
    for p in pairs_stats:
        flag = " OK" if p.is_cointegrated else "   "
        print(f"  {p.symbol_a+'/'+p.symbol_b:<16} "
              f"{p.correlation:>+6.3f} {p.hedge_ratio:>7.3f} "
              f"{p.adf_pvalue:>6.3f} {p.half_life_days:>7.1f} "
              f"{flag:>6} {p.n_obs:>5}")


def print_backtest_table(results: list[PairsBacktestResult]):
    """Affiche le leaderboard pairs trading."""
    results_sorted = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    print(f"\n{'='*100}")
    print(f"  PAIRS TRADING LEADERBOARD — {len(results_sorted)} paires")
    print(f"{'='*100}")
    print(f"  {'Paire':<16} {'Sharpe':>7} {'DD%':>6} {'WR%':>6} {'PF':>6} "
          f"{'Trades':>7} {'Ret%':>7} {'Hold':>5} {'HL_j':>5} {'Coint':>6}")
    print(f"  {'-'*95}")

    for r in results_sorted:
        flag = "[V]" if r.passes_validation else "   "
        coint = "[C]" if r.half_life_days < 60 else "   "
        print(f"  {r.pair_id:<16} "
              f"{r.sharpe_ratio:>+7.3f} "
              f"{r.max_drawdown_pct:>6.1f} "
              f"{r.win_rate_pct:>6.1f} "
              f"{r.profit_factor:>6.2f} "
              f"{r.total_trades:>7} "
              f"{r.total_return_pct:>+7.2f} "
              f"{r.avg_holding_days:>5.1f}j "
              f"{r.half_life_days:>5.1f} "
              f"{flag}{coint}")

    print(f"{'='*100}")

    # Stats globales
    sharpes = [r.sharpe_ratio for r in results_sorted]
    pos = [s for s in sharpes if s > 0]
    valid = [r for r in results_sorted if r.passes_validation]

    print(f"\n  Paires testées          : {len(results_sorted)}")
    print(f"  Sharpe positif          : {len(pos)}/{len(results_sorted)}")
    print(f"  Stratégies validées     : {len(valid)}/{len(results_sorted)}")
    if pos:
        print(f"  Sharpe moyen (>0)       : {sum(pos)/len(pos):.3f}")

    if results_sorted:
        print(f"\n  TOP 3 :")
        for i, r in enumerate(results_sorted[:3], 1):
            v = "[VALIDE]" if r.passes_validation else ""
            print(f"  {i}. {r.pair_id:<16}  Sharpe={r.sharpe_ratio:+.3f}  "
                  f"DD={r.max_drawdown_pct:.1f}%  Trades={r.total_trades}  "
                  f"HL={r.half_life_days:.1f}j  {v}")


def export_csv(results: list[PairsBacktestResult], path: str):
    """Exporte les résultats en CSV."""
    fields = [
        "pair_id", "sector", "start_date", "end_date", "n_obs",
        "sharpe_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor",
        "total_trades", "total_return_pct", "annualized_return_pct",
        "avg_holding_days", "total_costs", "expectancy",
        "hedge_ratio", "half_life_days", "passes_validation",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(results, key=lambda x: x.sharpe_ratio, reverse=True):
            writer.writerow({k: getattr(r, k, "") for k in fields})
    print(f"\n  Export CSV : {path}")


def run_sector(
    sector: str,
    engine: PairsBacktestEngine,
    discovery: PairDiscovery,
    discover_only: bool = False,
) -> list[PairsBacktestResult]:
    """Lance l'analyse complète d'un secteur."""
    symbols = SECTOR_MAP.get(sector, [])
    n_pairs = len(symbols) * (len(symbols) - 1) // 2

    print(f"\n{'='*70}")
    print(f"  SECTEUR : {sector.upper()} "
          f"({len(symbols)} actifs, {n_pairs} paires possibles)")
    print(f"{'='*70}")
    print(f"  Téléchargement données journalières (5 ans)...")

    data_dict = fetch_sector_data(sector)
    if len(data_dict) < 2:
        print(f"  Pas assez de données ({len(data_dict)} actifs).")
        return []

    print(f"\n  Analyse de coïntégration ({len(data_dict)} actifs disponibles)...")
    pairs_stats = discovery.find_pairs(sector, data_dict)

    print(f"  {len(pairs_stats)} paires candidates (après filtres) :")
    print_discovery_table(pairs_stats)

    if discover_only or not pairs_stats:
        return []

    # Backtest de toutes les paires candidates
    print(f"\n  Backtest en cours...")
    results = []
    for ps in pairs_stats:
        try:
            r = engine.run(data_dict[ps.symbol_a], data_dict[ps.symbol_b], ps)
            flag = "✅" if r.sharpe_ratio > 1.0 else "  "
            flag2 = "[OK]" if r.sharpe_ratio > 1.0 else "    "
            print(f"  {ps.symbol_a+'/'+ps.symbol_b:<16} "
                  f"Sharpe={r.sharpe_ratio:>+6.3f}  "
                  f"DD={r.max_drawdown_pct:.1f}%  "
                  f"Trades={r.total_trades:>3}  {flag2}")
            results.append(r)
        except Exception as e:
            print(f"  {ps.symbol_a+'/'+ps.symbol_b:<16} ERREUR — {e}")

    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pairs Trading Backtest long/short intra-secteur"
    )
    parser.add_argument(
        "--sector", default="",
        help=f"Secteur : {list(SECTOR_MAP.keys())}",
    )
    parser.add_argument(
        "--pair", nargs=2, metavar=("SYM_A", "SYM_B"),
        help="Paire spécifique ex: --pair AAPL MSFT",
    )
    parser.add_argument("--all", action="store_true", help="Tous les secteurs")
    parser.add_argument(
        "--discover-only", action="store_true",
        help="Stats de coïntégration seulement (sans backtest)",
    )
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--exit-z", type=float, default=0.5)
    parser.add_argument("--zscore-window", type=int, default=60)
    parser.add_argument("--export", default="", help="Exporter en CSV")
    parser.add_argument(
        "--min-adf-pvalue", type=float, default=0.05,
        help="Seuil p-value ADF pour considérer une paire coïntégrée",
    )
    args = parser.parse_args()

    engine = PairsBacktestEngine(
        initial_capital=args.capital,
        entry_zscore=args.entry_z,
        exit_zscore=args.exit_z,
        zscore_window=args.zscore_window,
    )
    discovery = PairDiscovery(
        adf_pvalue_threshold=args.min_adf_pvalue,
        min_correlation=0.50,
    )

    t0 = time.time()
    all_results = []

    if args.pair:
        # ── Paire spécifique ──────────────────────────────────────────────
        sym_a, sym_b = args.pair
        print(f"\nBacktest paire : {sym_a}/{sym_b}")

        from core.data.universe import get_asset
        data = {}
        for sym in [sym_a, sym_b]:
            try:
                asset = get_asset(sym)
                ticker = asset.ticker if asset else None
                d = OHLCVLoader.from_yfinance(sym, "1D", period="5y", ticker=ticker)
                data[sym] = d
                print(f"  {sym:<8} {d.n_bars} barres")
            except Exception as e:
                print(f"  {sym:<8} ECHEC — {e}")

        if sym_a not in data or sym_b not in data:
            print("Données insuffisantes.")
            sys.exit(1)

        # Trouver le secteur
        sector = "custom"
        for s, assets in SECTOR_MAP.items():
            if sym_a in assets and sym_b in assets:
                sector = s
                break

        ps = discovery.analyze_pair(
            data[sym_a], data[sym_b], sym_a, sym_b, sector
        )
        if ps is None:
            print("Impossible d'analyser la paire.")
            sys.exit(1)

        print(f"\n  Stats : {ps}")
        if not args.discover_only:
            r = engine.run(data[sym_a], data[sym_b], ps)
            print(r.summary())
            all_results.append(r)

    elif args.all:
        for sector in SECTOR_MAP:
            results = run_sector(sector, engine, discovery, args.discover_only)
            all_results.extend(results)
        if all_results and not args.discover_only:
            print_backtest_table(all_results)

    elif args.sector:
        results = run_sector(
            args.sector, engine, discovery, args.discover_only
        )
        all_results.extend(results)
        if results and not args.discover_only:
            print_backtest_table(results)

    else:
        # Défaut : tech_us + finance_us
        for sector in ["tech_us", "finance_us"]:
            results = run_sector(sector, engine, discovery, args.discover_only)
            all_results.extend(results)
        if all_results and not args.discover_only:
            print_backtest_table(all_results)

    elapsed = time.time() - t0
    print(f"\n  Terminé en {elapsed:.1f}s")

    if args.export and all_results:
        export_csv(all_results, args.export)


if __name__ == "__main__":
    main()
