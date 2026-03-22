"""
Grid Search — optimisation des parametres de strategie avec protection anti-overfitting.

Architecture anti-overfitting (OBLIGATOIRE) :
  1. Split temporel strict : IS (70%) pour la recherche, OOS (30%) pour la validation
  2. On NE touche jamais l'OOS pendant la recherche — aucun parametre choisi en le voyant
  3. Le winner est le meilleur sur IS — son score OOS est reporte APRES
  4. Walk-forward sur IS uniquement pour la stabilite

Formule de score (IS) :
  score = StrategyRanker.score (0-100) — composite Sharpe/DD/PF/WR/trades/WF

Limitations connues :
  - Grid search exhaustif : O(N^K) combinations — limiter a < 500 combos en pratique
  - Pas de bayesien ni de random search — volontairement simple pour reproductibilite
  - Parallelisme : None par defaut (Windows + asyncio ne tolerent pas multiprocessing)

Usage :
    gs = GridSearch(initial_capital=10_000)
    results = gs.run(base_strategy, data, param_grid={
        "oversold":   [20, 25, 30, 35],
        "overbought": [65, 70, 75, 80],
        "rsi_period": [10, 14, 20],
    })
    best = gs.best(results)
    print(gs.summary(results, top_n=10))
"""
from __future__ import annotations

import copy
import itertools
import logging
from dataclasses import dataclass, field

import numpy as np

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVData
from core.ranking.ranker import StrategyRanker

logger = logging.getLogger(__name__)


@dataclass
class GridResult:
    """Resultat d'une combinaison de parametres."""
    params: dict                   # Parametres testes
    # Metriques IS (in-sample)
    is_sharpe: float
    is_max_dd: float
    is_profit_factor: float
    is_win_rate: float
    is_trades: int
    is_return_pct: float
    is_expectancy: float
    is_score: float                # Score composite StrategyRanker (0-100)
    is_wf_sharpe_mean: float       # Walk-forward mean Sharpe (sur IS)
    is_wf_sharpe_std: float
    # Metriques OOS (out-of-sample) — remplies apres selection
    oos_sharpe: float = 0.0
    oos_max_dd: float = 0.0
    oos_profit_factor: float = 0.0
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    oos_return_pct: float = 0.0
    oos_score: float = 0.0
    oos_evaluated: bool = False


class GridSearch:
    """
    Optimisation exhaustive des parametres avec split IS/OOS strict.

    Le principe fondamental : on cherche les meilleurs parametres sur IS,
    puis on reporte les performances OOS sans toucher aux parametres.
    Un bon systeme doit avoir des performances IS et OOS correlees.
    """

    def __init__(self, initial_capital: float = 10_000,
                 is_pct: float = 0.70,
                 wf_windows: int = 3):
        """
        initial_capital : capital pour les backtests
        is_pct          : fraction des donnees pour l'in-sample (defaut 70%)
        wf_windows      : fenetres walk-forward sur IS pour evaluer la stabilite
        """
        self.initial_capital = initial_capital
        self.is_pct = is_pct
        self.wf_windows = wf_windows
        self._engine = BacktestEngine(initial_capital)
        self._ranker = StrategyRanker()

    def run(self, base_strategy: dict, data: OHLCVData,
            param_grid: dict[str, list]) -> list[GridResult]:
        """
        Optimise les parametres de `base_strategy` sur la partie IS des donnees.

        base_strategy : dict valide (issu de StrategyValidator)
        data          : donnees OHLCV completes (IS + OOS)
        param_grid    : {param_name: [valeur1, valeur2, ...]}

        Retourne : liste de GridResult triee par is_score decroissant.
        """
        # 1. Split IS / OOS
        is_data, oos_data = data.split(train_pct=self.is_pct)

        # 2. Generer toutes les combinaisons
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))
        n = len(combinations)

        logger.info(
            f"GridSearch : {n} combinaisons sur {is_data.n_bars} barres IS "
            f"({oos_data.n_bars} barres OOS reserves)"
        )

        if n > 1000:
            logger.warning(
                f"ATTENTION : {n} combinaisons — peut etre lent. "
                f"Reduire le param_grid ou utiliser un sous-ensemble."
            )

        # 3. Recherche sur IS
        results: list[GridResult] = []
        for combo in combinations:
            params_override = dict(zip(param_names, combo))
            result = self._eval_combo(base_strategy, is_data, params_override)
            if result is not None:
                results.append(result)

        if not results:
            logger.warning("GridSearch : aucun resultat produit")
            return []

        # 4. Trier par score IS
        results.sort(key=lambda r: r.is_score, reverse=True)

        logger.info(
            f"GridSearch complete : {len(results)} resultats. "
            f"Meilleur IS score={results[0].is_score:.1f} "
            f"(Sharpe={results[0].is_sharpe:.3f})"
        )

        return results

    def evaluate_oos(self, best: GridResult, base_strategy: dict,
                     data: OHLCVData) -> GridResult:
        """
        Evalue les meilleurs parametres sur l'OOS (appel explicite apres selection).
        Ne doit etre appele QU'UNE FOIS sur le winner final.
        """
        _, oos_data = data.split(train_pct=self.is_pct)

        strategy = copy.deepcopy(base_strategy)
        strategy["parameters"].update(best.params)

        try:
            r = self._engine.run(oos_data, strategy)
            d = r.to_dict()
            ranked = self._ranker.rank([d])
            oos_score = ranked[0].score if ranked else 0.0

            best.oos_sharpe = d["sharpe_ratio"]
            best.oos_max_dd = d["max_drawdown_pct"]
            best.oos_profit_factor = d["profit_factor"]
            best.oos_win_rate = d["win_rate_pct"]
            best.oos_trades = d["total_trades"]
            best.oos_return_pct = d["total_return_pct"]
            best.oos_score = oos_score
            best.oos_evaluated = True
        except Exception as e:
            logger.warning(f"Erreur evaluation OOS : {e}")

        return best

    def best(self, results: list[GridResult]) -> GridResult | None:
        """Retourne le meilleur resultat (IS score le plus haut)."""
        return results[0] if results else None

    def summary(self, results: list[GridResult], top_n: int = 10) -> str:
        """Tableau lisible des meilleurs resultats."""
        top = results[:top_n]
        lines = [
            f"\n{'='*100}",
            f"  GRID SEARCH RESULTS — Top {min(top_n, len(results))}/{len(results)} combinaisons",
            f"{'='*100}",
            f"  {'Parametres':<45} {'Score':>6} {'Sharpe':>7} {'DD%':>6} "
            f"{'PF':>5} {'WR%':>5} {'Trades':>7} {'WF_sh':>7}",
            f"  {'-'*95}",
        ]
        for r in top:
            param_str = ", ".join(f"{k}={v}" for k, v in r.params.items())
            wf_str = f"{r.is_wf_sharpe_mean:+.2f}" if r.is_wf_sharpe_mean != 0 else "  N/A"
            lines.append(
                f"  {param_str:<45} {r.is_score:>6.1f} {r.is_sharpe:>+7.3f} "
                f"{r.is_max_dd:>6.1f} {r.is_profit_factor:>5.2f} "
                f"{r.is_win_rate:>5.1f} {r.is_trades:>7} {wf_str:>7}"
            )

        # OOS du meilleur
        best = results[0] if results else None
        if best and best.oos_evaluated:
            lines += [
                f"\n  {'IS vs OOS du meilleur':-<60}",
                f"  {'':45} {'IS':>8} {'OOS':>8}",
                f"  {'Score':<45} {best.is_score:>8.1f} {best.oos_score:>8.1f}",
                f"  {'Sharpe':<45} {best.is_sharpe:>+8.3f} {best.oos_sharpe:>+8.3f}",
                f"  {'Max DD%':<45} {best.is_max_dd:>8.1f} {best.oos_max_dd:>8.1f}",
                f"  {'Profit Factor':<45} {best.is_profit_factor:>8.2f} {best.oos_profit_factor:>8.2f}",
                f"  {'Win Rate%':<45} {best.is_win_rate:>8.1f} {best.oos_win_rate:>8.1f}",
                f"  {'Trades':<45} {best.is_trades:>8} {best.oos_trades:>8}",
            ]
            # Alerte sur-apprentissage
            if best.oos_sharpe < best.is_sharpe * 0.5:
                lines.append(
                    f"\n  [ALERTE] Sharpe OOS ({best.oos_sharpe:.3f}) < 50% IS ({best.is_sharpe:.3f})"
                    f" — risque de sur-apprentissage"
                )
            elif best.oos_sharpe > 0 and best.is_sharpe > 0:
                ratio = best.oos_sharpe / best.is_sharpe
                lines.append(
                    f"\n  [OK] Ratio OOS/IS = {ratio:.2f} "
                    f"({'robuste' if ratio > 0.7 else 'acceptable' if ratio > 0.4 else 'fragile'})"
                )

        lines.append(f"{'='*100}")
        return "\n".join(lines)

    # ─── Internals ────────────────────────────────────────────────────────────

    def _eval_combo(self, base_strategy: dict, is_data: OHLCVData,
                    params_override: dict) -> GridResult | None:
        """Evalue une combinaison de parametres sur is_data."""
        strategy = copy.deepcopy(base_strategy)
        strategy["parameters"].update(params_override)

        try:
            r = self._engine.run(is_data, strategy)
            if r.total_trades == 0:
                return None  # Ignorer les combos sans trades

            d = r.to_dict()
            d["equity_curve"] = r.equity_curve

            # Walk-forward sur IS
            wf_sharpes = []
            try:
                windows = is_data.walk_forward_windows(
                    n_windows=self.wf_windows, oos_pct=0.3
                )
                for is_w, oos_w in windows:
                    if len(oos_w.df) < 20:
                        continue
                    wr = self._engine.run(oos_w, strategy)
                    wf_sharpes.append(wr.sharpe_ratio)
            except Exception:
                pass

            d["wf_sharpes"] = wf_sharpes
            ranked = self._ranker.rank([d])
            score = ranked[0].score if ranked else 0.0

            wf_mean = float(np.mean(wf_sharpes)) if wf_sharpes else 0.0
            wf_std  = float(np.std(wf_sharpes))  if len(wf_sharpes) > 1 else 0.0

            return GridResult(
                params=params_override,
                is_sharpe=d["sharpe_ratio"],
                is_max_dd=d["max_drawdown_pct"],
                is_profit_factor=d["profit_factor"],
                is_win_rate=d["win_rate_pct"],
                is_trades=d["total_trades"],
                is_return_pct=d["total_return_pct"],
                is_expectancy=d.get("expectancy", 0.0),
                is_score=score,
                is_wf_sharpe_mean=wf_mean,
                is_wf_sharpe_std=wf_std,
            )
        except Exception as e:
            logger.debug(f"Combo {params_override} echoue : {e}")
            return None
