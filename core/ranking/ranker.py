"""
Strategy Ranker — scorer et classement multi-critères des stratégies.

Score composite (0-100) basé sur :
  - Sharpe ratio       (30%) — rendement ajusté risque
  - Max drawdown       (20%) — pire perte consécutive
  - Profit factor      (20%) — ratio gains/pertes
  - Win rate           (10%) — % de trades gagnants
  - Consistance WF     (10%) — stabilité walk-forward
  - Nombre de trades   (10%) — significativité statistique

Usage :
  ranker = StrategyRanker()
  results = ranker.rank([result1, result2, result3])
  ranker.print_leaderboard(results)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RankedStrategy:
    strategy_id: str
    score: float              # 0-100
    rank: int
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate_pct: float
    total_trades: int
    total_return_pct: float
    wf_sharpe_mean: float
    wf_sharpe_std: float
    passes_validation: bool
    score_breakdown: dict[str, float]


class StrategyRanker:
    """
    Classe le résultat de plusieurs backtests par score composite.
    Permet d'identifier rapidement les meilleures stratégies.
    """

    # Poids des critères (somme = 1.0)
    WEIGHTS = {
        "sharpe":         0.30,
        "drawdown":       0.20,
        "profit_factor":  0.20,
        "win_rate":       0.10,
        "consistency":    0.10,
        "n_trades":       0.10,
    }

    def rank(self, results: list[dict]) -> list[RankedStrategy]:
        """
        results : liste de dicts issus de BacktestResult.to_dict()
                  enrichis optionnellement de wf_sharpes (list[float])
        Retourne une liste triée par score décroissant.
        """
        if not results:
            return []

        scored = [self._score_one(r) for r in results]
        scored.sort(key=lambda x: x.score, reverse=True)
        for i, s in enumerate(scored):
            s.rank = i + 1
        return scored

    def _score_one(self, result: dict) -> RankedStrategy:
        """Calcule le score composite d'un résultat."""
        sharpe      = result.get("sharpe_ratio", 0.0)
        max_dd      = result.get("max_drawdown_pct", 100.0)
        pf          = result.get("profit_factor", 0.0)
        win_rate    = result.get("win_rate_pct", 0.0)
        n_trades    = result.get("total_trades", 0)
        wf_sharpes  = result.get("wf_sharpes", [])

        # Normalisation de chaque critère en score 0-1
        # Sharpe : 0=mauvais, 2+=excellent
        s_sharpe = float(np.clip(sharpe / 2.0, 0, 1))

        # Drawdown : 0%=parfait, 30%+=nul
        s_dd = float(np.clip(1 - max_dd / 30.0, 0, 1))

        # Profit factor : 1.0=neutre, 2.5+=excellent
        s_pf = float(np.clip((pf - 1.0) / 1.5, 0, 1))

        # Win rate : 40%=nul, 70%+=excellent
        s_wr = float(np.clip((win_rate - 40.0) / 30.0, 0, 1))

        # Consistance walk-forward : faible std/mean = bien
        if len(wf_sharpes) >= 2:
            mean_s = np.mean(wf_sharpes)
            std_s  = np.std(wf_sharpes)
            cv = std_s / abs(mean_s) if mean_s != 0 else 10.0
            s_consistency = float(np.clip(1 - cv / 2.0, 0, 1))
        elif len(wf_sharpes) == 1:
            s_consistency = 0.5
        else:
            s_consistency = 0.0

        # Nombre de trades : 30=faible, 200+=bien
        s_trades = float(np.clip((n_trades - 30) / 170.0, 0, 1))

        breakdown = {
            "sharpe":        round(s_sharpe * self.WEIGHTS["sharpe"] * 100, 2),
            "drawdown":      round(s_dd     * self.WEIGHTS["drawdown"] * 100, 2),
            "profit_factor": round(s_pf     * self.WEIGHTS["profit_factor"] * 100, 2),
            "win_rate":      round(s_wr     * self.WEIGHTS["win_rate"] * 100, 2),
            "consistency":   round(s_consistency * self.WEIGHTS["consistency"] * 100, 2),
            "n_trades":      round(s_trades * self.WEIGHTS["n_trades"] * 100, 2),
        }

        total_score = sum(breakdown.values())

        return RankedStrategy(
            strategy_id=result.get("strategy_id", "unknown"),
            score=round(total_score, 2),
            rank=0,
            sharpe=round(sharpe, 3),
            max_drawdown_pct=round(max_dd, 2),
            profit_factor=round(pf, 3),
            win_rate_pct=round(win_rate, 1),
            total_trades=n_trades,
            total_return_pct=round(result.get("total_return_pct", 0.0), 2),
            wf_sharpe_mean=round(float(np.mean(wf_sharpes)), 3) if wf_sharpes else 0.0,
            wf_sharpe_std=round(float(np.std(wf_sharpes)), 3) if wf_sharpes else 0.0,
            passes_validation=result.get("passes_validation", False),
            score_breakdown=breakdown,
        )

    def print_leaderboard(self, ranked: list[RankedStrategy]):
        """Affiche le classement en tableau lisible."""
        print(f"\n{'='*75}")
        print(f"  STRATEGY LEADERBOARD")
        print(f"{'='*75}")
        print(f"  {'#':<3} {'Strategy':<35} {'Score':>6} {'Sharpe':>7} {'DD%':>6} {'WR%':>6} {'Trades':>7}")
        print(f"  {'-'*70}")
        for r in ranked:
            valid = "[OK]" if r.passes_validation else "[--]"
            print(
                f"  {r.rank:<3} {r.strategy_id:<35} "
                f"{r.score:>6.1f} {r.sharpe:>7.3f} {r.max_drawdown_pct:>6.1f} "
                f"{r.win_rate_pct:>6.1f} {r.total_trades:>7}  {valid}"
            )
        print(f"{'='*75}")
        if ranked:
            best = ranked[0]
            print(f"\n  Meilleure strategie : {best.strategy_id} (score={best.score})")
            print(f"  Breakdown : {best.score_breakdown}")
