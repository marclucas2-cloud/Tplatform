"""
Optimisation des parametres d'une strategie par Grid Search IS/OOS.

Usage :
    # Optimiser RSI sur EURUSD 1H avec grille par defaut
    python scripts/optimize.py --strategy rsi_mean_reversion

    # Grille personnalisee
    python scripts/optimize.py --strategy rsi_mean_reversion \\
        --param oversold 20,25,30,35 \\
        --param overbought 65,70,75,80 \\
        --param rsi_period 10,14,20

    # Choisir l'actif et le timeframe
    python scripts/optimize.py --strategy rsi_mean_reversion \\
        --asset GBPUSD --timeframe 1H

    # Sauvegarder les meilleurs params en nouveau JSON
    python scripts/optimize.py --strategy rsi_mean_reversion --save

    # Sur plusieurs actifs sequentiellement
    python scripts/optimize.py --strategy rsi_mean_reversion \\
        --asset EURUSD,GBPUSD,USDJPY

Sortie :
    - Tableau des Top 10 combinaisons (score IS)
    - Comparaison IS vs OOS du meilleur
    - Alerte sur-apprentissage si OOS/IS < 50%
    - JSON optionnel avec params optimises
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.loader import OHLCVLoader
from core.data.universe import get_asset, UNIVERSE
from core.strategy_schema.validator import StrategyValidator
from core.optimization.grid_search import GridSearch

STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"

# ─── Grilles de parametres par defaut par type de strategie ──────────────────

DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "rsi_": {
        "rsi_period":  [10, 14, 20],
        "oversold":    [20, 25, 30],
        "overbought":  [70, 75, 80],
    },
    "rsi_filtered_": {
        "rsi_period":   [10, 14, 20],
        "oversold":     [20, 25, 30],
        "overbought":   [70, 75, 80],
        "adx_threshold":[18, 20, 22, 25],
    },
    "vwap_": {
        "entry_std":  [1.5, 2.0, 2.5],
        "exit_std":   [0.2, 0.3, 0.5],
        "atr_period": [10, 14, 20],
    },
    "bb_squeeze_": {
        "bb_period":         [14, 20, 26],
        "squeeze_ma_period": [15, 20, 25],
        "ema_fast":          [7, 9, 12],
        "ema_slow":          [18, 21, 26],
    },
    "momentum_burst_": {
        "ema_fast":         [7, 9, 12],
        "ema_slow":         [18, 21, 26],
        "volume_multiplier":[1.5, 2.0, 2.5, 3.0],
    },
    "orb_": {
        "volume_multiplier": [1.2, 1.5, 2.0],
        "volume_lookback":   [15, 20, 30],
    },
    "seasonality_": {
        "ema_fast":          [7, 9, 12],
        "ema_slow":          [18, 21, 26],
        "session_start_hour":[7, 8, 9],
        "session_end_hour":  [10, 11, 12],
    },
    "breakout_": {
        "channel_period": [15, 20, 25, 30],
    },
}


def resolve_strategy(name: str) -> Path | None:
    """Trouve le fichier JSON correspondant au nom (partial match)."""
    candidates = sorted(STRATEGIES_DIR.glob("*.json"))
    for p in candidates:
        if name.lower() in p.stem.lower():
            return p
    return None


def get_default_grid(strategy_id: str) -> dict[str, list]:
    """Retourne la grille par defaut selon le type de strategie."""
    for prefix, grid in DEFAULT_GRIDS.items():
        if strategy_id.startswith(prefix):
            return copy.deepcopy(grid)
    # Fallback : grille vide (juste les stop/TP)
    return {
        "stop_loss_pct":   [0.3, 0.5, 0.8],
        "take_profit_pct": [0.6, 1.0, 1.5],
    }


def parse_param_grid(param_args: list[list[str]] | None) -> dict[str, list]:
    """Parse les arguments --param name val1,val2,val3 (action=append, nargs=2)."""
    if not param_args:
        return {}
    grid = {}
    for name, values_str in param_args:
        grid[name] = [float(v) for v in values_str.split(",")]
    return grid


def fetch_data(asset_symbol: str, timeframe: str):
    """Telecharge les donnees pour un actif."""
    from core.data.loader import OHLCVLoader
    # Mapping timeframe -> periode yfinance
    period_map = {"1M": "7d", "5M": "60d", "1H": "2y", "4H": "2y", "1D": "5y"}
    # Fallback pour les timeframes courts
    actual_tf = timeframe
    if timeframe in ("1M", "5M"):
        print(f"  [INFO] {timeframe} -> fallback 1H (limite yfinance intraday)")
        actual_tf = "1H"
    period = period_map.get(actual_tf, "1y")
    return OHLCVLoader.from_yfinance(asset_symbol, actual_tf, period=period)


def save_optimized_json(base_strategy: dict, best_params: dict,
                        asset: str, output_path: Path):
    """Cree un nouveau fichier JSON avec les parametres optimises."""
    import datetime
    s = copy.deepcopy(base_strategy)
    s["parameters"].update(best_params)

    # Generer un nouvel ID
    base_id = base_strategy["strategy_id"]
    # Incrementer la version
    if "_opt_v" in base_id:
        parts = base_id.rsplit("_opt_v", 1)
        n = int(parts[1]) + 1
        new_id = f"{parts[0]}_opt_v{n}"
    else:
        # Retirer le _vN final et ajouter _opt_v1
        base_no_ver = base_id.rsplit("_v", 1)[0]
        new_id = f"{base_no_ver}_opt_v1"

    s["strategy_id"] = new_id
    s["asset"] = asset
    s["created_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    s["description"] = (
        f"[OPTIMISE] {base_strategy.get('description', base_id)} "
        f"— params optimises par grid search IS/OOS sur {asset}"
    )

    # Retirer le fingerprint (sera recalcule)
    s.pop("_fingerprint", None)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    print(f"\n  JSON sauvegarde : {output_path}")


def run_optimization(strategy_path: Path, asset_symbol: str,
                     param_grid: dict, args) -> bool:
    """Lance l'optimisation pour un couple strategie/actif."""
    validator = StrategyValidator()
    try:
        strategy = validator.load_and_validate(strategy_path)
    except Exception as e:
        print(f"  [ERREUR] Chargement strategie : {e}")
        return False

    sid = strategy["strategy_id"]
    tf = strategy["timeframe"]
    print(f"\n{'='*70}")
    print(f"  Strategie : {sid}")
    print(f"  Actif     : {asset_symbol}  Timeframe : {tf}")
    print(f"  Grille    : {sum(len(v) for v in param_grid.values())} valeurs, "
          f"{__import__('math').prod(len(v) for v in param_grid.values())} combinaisons")
    print(f"{'='*70}")

    # Telecharger les donnees
    try:
        data = fetch_data(asset_symbol, tf)
        print(f"  Donnees   : {data.n_bars} barres "
              f"({data.df.index[0].date()} -> {data.df.index[-1].date()})")
        is_n = int(data.n_bars * 0.7)
        oos_n = data.n_bars - is_n
        print(f"  Split     : IS={is_n} barres | OOS={oos_n} barres (30%)")
    except Exception as e:
        print(f"  [ERREUR] Donnees : {e}")
        return False

    # Grid search
    gs = GridSearch(initial_capital=args.capital, wf_windows=3)
    t0 = time.time()
    print(f"\n  Recherche en cours...")
    results = gs.run(strategy, data, param_grid)
    elapsed = time.time() - t0

    if not results:
        print("  [ERREUR] Aucun resultat produit (tous les combos ont 0 trades)")
        return False

    print(f"  {len(results)} combinaisons evaluees en {elapsed:.1f}s")

    # Evaluer le meilleur sur OOS
    best = gs.best(results)
    gs.evaluate_oos(best, strategy, data)

    # Afficher les resultats
    print(gs.summary(results, top_n=args.top))

    # Sauvegarder si demande
    if args.save and best:
        out_name = f"{sid.rsplit('_v', 1)[0]}_opt_v1.json"
        out_path = STRATEGIES_DIR / out_name
        save_optimized_json(strategy, best.params, asset_symbol, out_path)

    return True


def main():
    parser = argparse.ArgumentParser(description="Optimisation de parametres par Grid Search")
    parser.add_argument("--strategy", required=True,
                        help="Nom (partiel) de la strategie. Ex: rsi_mean, vwap")
    parser.add_argument("--asset", default="",
                        help="Actif(s) comma-separated. Ex: EURUSD ou EURUSD,GBPUSD,DAX")
    parser.add_argument("--param", action="append", nargs=2, metavar=("NOM", "VALEURS"),
                        help="Parametre a optimiser. Ex: --param oversold 20,25,30,35")
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--top", type=int, default=10,
                        help="Nombre de resultats a afficher")
    parser.add_argument("--save", action="store_true",
                        help="Sauvegarder les meilleurs params en JSON")
    args = parser.parse_args()

    # Trouver la strategie
    path = resolve_strategy(args.strategy)
    if path is None:
        print(f"Strategie '{args.strategy}' introuvable dans {STRATEGIES_DIR}")
        print("Disponibles :", [p.stem for p in STRATEGIES_DIR.glob("*.json")])
        sys.exit(1)
    print(f"Strategie selectionnee : {path.name}")

    # Construire la grille
    custom_grid = parse_param_grid(args.param)
    if custom_grid:
        param_grid = custom_grid
        print(f"Grille personnalisee : {param_grid}")
    else:
        validator = StrategyValidator()
        s = validator.load_and_validate(path)
        param_grid = get_default_grid(s["strategy_id"])
        print(f"Grille par defaut pour {s['strategy_id'][:20]} : {list(param_grid.keys())}")

    # Lister les actifs
    if args.asset:
        assets = [a.strip() for a in args.asset.split(",")]
    else:
        # Utiliser l'actif de la strategie
        validator = StrategyValidator()
        s = validator.load_and_validate(path)
        raw = s["asset"]
        # Convertir epic IG -> symbole court si besoin
        from core.data.loader import OHLCVLoader
        mapped = OHLCVLoader._YF_TICKER_MAP.get(raw, raw)
        assets = [raw]
        print(f"Actif de la strategie : {raw} (ticker yfinance: {mapped})")

    # Lancer l'optimisation pour chaque actif
    for asset_sym in assets:
        run_optimization(path, asset_sym, param_grid, args)


if __name__ == "__main__":
    main()
