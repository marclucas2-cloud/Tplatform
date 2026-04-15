#!/usr/bin/env python3
"""WP-03 — Portfolio marginal score engine.

Mesure la valeur AJOUTEE d'une strategie candidate dans le portefeuille
existant, pas son Sharpe standalone.

Usage:
    # Programmatic:
    from scripts.research.portfolio_marginal_score import score_candidate
    result = score_candidate(
        candidate_returns=pd.Series(...),   # daily PnL of candidate
        portfolio_returns=pd.DataFrame(...), # existing portfolio (WP-01 output)
        initial_equity=10000.0,
        candidate_weight=1.0,                # weight in combined portfolio
    )

    # CLI (demo on synthetic candidate):
    python scripts/research/portfolio_marginal_score.py --demo

Output: dict with Delta_Sharpe, Delta_CAGR, Delta_MaxDD, Delta_Calmar,
Delta_ROC, Corr_To_Portfolio, Tail_Overlap, Worst_Day_Overlap,
Diversification_Benefit, Final_Score, Verdict.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
IN_TS = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"

INITIAL_EQUITY = 10_000.0


@dataclass
class ScoreResult:
    candidate_id: str
    delta_sharpe: float
    delta_cagr: float
    delta_maxdd: float
    delta_calmar: float
    delta_roc: float
    corr_to_portfolio: float
    max_corr_to_strat: float
    tail_overlap: float
    worst_day_overlap: int
    diversification_benefit: float
    capital_utilization_benefit: float
    marginal_score: float
    verdict: str
    penalties: list
    details: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _metrics(daily_pnl: pd.Series, initial: float = INITIAL_EQUITY) -> dict:
    """Return dict with sharpe / cagr / max_dd / calmar for a daily PnL series."""
    arr = daily_pnl.values
    if len(arr) == 0 or arr.std() == 0:
        return {"sharpe": 0, "cagr": 0, "max_dd": 0, "calmar": 0, "total_pnl": 0}
    eq = initial + daily_pnl.cumsum()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min())
    final = float(eq.iloc[-1])
    years = max(len(arr) / 252.0, 0.001)
    cagr = (final / initial) ** (1 / years) - 1 if final > 0 else -1.0
    sharpe = float(arr.mean() / arr.std() * np.sqrt(252))
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0
    return {
        "sharpe": round(sharpe, 3),
        "cagr": round(cagr * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "calmar": round(calmar, 3),
        "total_pnl": round(daily_pnl.sum(), 0),
    }


def score_candidate(
    candidate_id: str,
    candidate_returns: pd.Series,
    portfolio_returns: pd.DataFrame,
    initial_equity: float = INITIAL_EQUITY,
    candidate_weight: float = 1.0,
) -> ScoreResult:
    """Compute marginal score of adding `candidate` to `portfolio`.

    Args:
        candidate_id: unique identifier for the candidate
        candidate_returns: daily PnL series of the candidate
        portfolio_returns: DataFrame of daily PnL per strategy (WP-01 output)
        initial_equity: reference equity for compute metrics
        candidate_weight: how much weight to give the candidate when adding
                          (1.0 = same as existing strats)
    """
    # Align indices
    common_idx = portfolio_returns.index.intersection(candidate_returns.index)
    if len(common_idx) < 30:
        raise ValueError(f"Insufficient overlap: {len(common_idx)} days")
    portf = portfolio_returns.loc[common_idx]
    cand = candidate_returns.loc[common_idx]

    # 1. Baseline portfolio metrics (sum of strats)
    baseline_daily = portf.sum(axis=1)
    baseline = _metrics(baseline_daily, initial_equity)

    # 2. Portfolio + candidate metrics
    combined_daily = baseline_daily + cand * candidate_weight
    combined = _metrics(combined_daily, initial_equity)

    # 3. Deltas
    delta_sharpe = combined["sharpe"] - baseline["sharpe"]
    delta_cagr = combined["cagr"] - baseline["cagr"]
    delta_maxdd = combined["max_dd"] - baseline["max_dd"]  # less negative = better
    delta_calmar = combined["calmar"] - baseline["calmar"]

    # 4. Correlations
    corr_to_portfolio = float(cand.corr(baseline_daily))
    max_corr_to_strat = float(max(abs(cand.corr(portf[c])) for c in portf.columns))

    # 5. Tail overlap (worst 30 days of candidate vs portfolio)
    cand_worst = cand.nsmallest(30).index
    portf_worst = baseline_daily.nsmallest(30).index
    worst_day_overlap = len(set(cand_worst) & set(portf_worst))
    tail_overlap = worst_day_overlap / 30.0

    # 6. Diversification benefit = 1 - max abs correlation
    diversification_benefit = 1.0 - abs(corr_to_portfolio)

    # 7. ROC approximation: total PnL / total capital committed (proxy)
    # Use running max equity as proxy for capital committed
    baseline_eq = initial_equity + baseline_daily.cumsum()
    baseline_capital_proxy = baseline_eq.mean()
    combined_eq = initial_equity + combined_daily.cumsum()
    combined_capital_proxy = combined_eq.mean()
    baseline_roc = baseline_daily.sum() / baseline_capital_proxy if baseline_capital_proxy > 0 else 0
    combined_roc = combined_daily.sum() / combined_capital_proxy if combined_capital_proxy > 0 else 0
    delta_roc = float(combined_roc - baseline_roc)

    # 8. Capital utilization benefit (how many days are newly active)
    baseline_active_days = (baseline_daily != 0).sum()
    combined_active_days = (combined_daily != 0).sum()
    capital_utilization_benefit = float(
        (combined_active_days - baseline_active_days) / max(len(common_idx), 1)
    )

    # 9. Penalties
    penalties = []
    penalty_amount = 0.0
    if max_corr_to_strat > 0.70:
        penalties.append(f"high_corr_to_strat={max_corr_to_strat:.2f}")
        penalty_amount += 0.3
    if abs(corr_to_portfolio) > 0.70:
        penalties.append(f"high_corr_to_portfolio={corr_to_portfolio:.2f}")
        penalty_amount += 0.3
    if tail_overlap > 0.5:
        penalties.append(f"tail_overlap={tail_overlap:.0%}")
        penalty_amount += 0.2
    if len(common_idx) < 100:
        penalties.append(f"too_few_days={len(common_idx)}")
        penalty_amount += 0.2
    # Bad if worsens portfolio significantly
    if delta_sharpe < -0.1:
        penalties.append(f"sharpe_worsens={delta_sharpe:.2f}")
        penalty_amount += 0.5
    if delta_maxdd < -5.0:  # DD becomes 5pp worse
        penalties.append(f"maxdd_worsens={delta_maxdd:.1f}pp")
        penalty_amount += 0.3

    # 10. Composite score (interpretable)
    # MarginalScore = 0.30*delta_sharpe + 0.20*delta_calmar + 0.20*delta_roc
    #               + 0.15*div_benefit + 0.15*cap_utilization - penalties
    marginal_score = (
        0.30 * delta_sharpe
        + 0.20 * delta_calmar
        + 0.20 * (delta_roc * 10)  # scale up ROC delta
        + 0.15 * diversification_benefit
        + 0.15 * capital_utilization_benefit
        - penalty_amount
    )

    # 11. Verdict — hard gates first, then score-based tier
    # Hard DROP conditions (non-negotiable regardless of score):
    hard_drop = []
    if max_corr_to_strat > 0.70:
        hard_drop.append(f"corr_to_strat={max_corr_to_strat:.2f}>0.70")
    if abs(corr_to_portfolio) > 0.70:
        hard_drop.append(f"corr_to_portfolio={corr_to_portfolio:.2f}>0.70")
    if tail_overlap > 0.50:
        hard_drop.append(f"tail_overlap={tail_overlap:.0%}>50%")
    if delta_sharpe < -0.1:
        hard_drop.append(f"sharpe_degrades={delta_sharpe:.2f}")
    if delta_maxdd < -5.0:
        hard_drop.append(f"maxdd_degrades={delta_maxdd:.1f}pp")

    if hard_drop:
        verdict = "DROP"
        # Append hard_drop reasons to penalties for visibility
        penalties = penalties + [f"HARD_DROP: {', '.join(hard_drop)}"]
    elif marginal_score > 0.3 and delta_sharpe > 0 and delta_maxdd > -2.0:
        verdict = "PROMOTE_LIVE"
    elif marginal_score > 0.1 and delta_sharpe >= 0:
        verdict = "PROMOTE_PAPER"
    elif marginal_score > -0.1:
        verdict = "KEEP_FOR_RESEARCH"
    else:
        verdict = "DROP"

    return ScoreResult(
        candidate_id=candidate_id,
        delta_sharpe=round(delta_sharpe, 3),
        delta_cagr=round(delta_cagr, 2),
        delta_maxdd=round(delta_maxdd, 2),
        delta_calmar=round(delta_calmar, 3),
        delta_roc=round(delta_roc, 5),
        corr_to_portfolio=round(corr_to_portfolio, 3),
        max_corr_to_strat=round(max_corr_to_strat, 3),
        tail_overlap=round(tail_overlap, 3),
        worst_day_overlap=worst_day_overlap,
        diversification_benefit=round(diversification_benefit, 3),
        capital_utilization_benefit=round(capital_utilization_benefit, 3),
        marginal_score=round(marginal_score, 3),
        verdict=verdict,
        penalties=penalties,
        details={
            "baseline": baseline,
            "combined": combined,
            "n_days": len(common_idx),
        },
    )


def score_candidate_from_portfolio(
    candidate_id: str,
    candidate_returns: pd.Series,
) -> ScoreResult:
    """Convenience: loads baseline portfolio from WP-01 output."""
    if not IN_TS.exists():
        raise FileNotFoundError(f"{IN_TS} — run build_portfolio_baseline.py first")
    portfolio = pd.read_parquet(IN_TS)
    portfolio.index = pd.to_datetime(portfolio.index)
    return score_candidate(candidate_id, candidate_returns, portfolio)


def _print_result(r: ScoreResult):
    print(f"\n=== SCORECARD: {r.candidate_id} ===")
    print(f"  Delta Sharpe:        {r.delta_sharpe:+.3f}")
    print(f"  Delta CAGR:          {r.delta_cagr:+.2f}%")
    print(f"  Delta MaxDD:         {r.delta_maxdd:+.2f}pp")
    print(f"  Delta Calmar:        {r.delta_calmar:+.3f}")
    print(f"  Delta ROC:           {r.delta_roc:+.5f}")
    print(f"  Corr to portfolio:   {r.corr_to_portfolio:+.3f}")
    print(f"  Max corr to strat:   {r.max_corr_to_strat:+.3f}")
    print(f"  Tail overlap:        {r.tail_overlap:.0%} ({r.worst_day_overlap}/30)")
    print(f"  Div benefit:         {r.diversification_benefit:+.3f}")
    print(f"  Cap util benefit:    {r.capital_utilization_benefit:+.3f}")
    if r.penalties:
        print(f"  Penalties:           {', '.join(r.penalties)}")
    print(f"  MARGINAL SCORE:      {r.marginal_score:+.3f}")
    print(f"  VERDICT:             {r.verdict}")


def demo():
    """Demo: test 3 synthetic candidates against the baseline."""
    print("Loading baseline portfolio...")
    portfolio = pd.read_parquet(IN_TS)
    portfolio.index = pd.to_datetime(portfolio.index)
    print(f"  {portfolio.shape[0]} days x {portfolio.shape[1]} strategies")

    np.random.seed(42)
    n = len(portfolio.index)

    # Candidate 1: Pure noise (low everything)
    noise = pd.Series(np.random.normal(0, 50, n), index=portfolio.index)
    r1 = score_candidate("candidate_noise", noise, portfolio)
    _print_result(r1)

    # Candidate 2: Mildly positive, uncorrelated
    positive_uncorr = pd.Series(np.random.normal(5, 40, n), index=portfolio.index)
    r2 = score_candidate("candidate_positive_uncorr", positive_uncorr, portfolio)
    _print_result(r2)

    # Candidate 3: Highly correlated to gold_trend_mgc (redundant)
    gt = portfolio["gold_trend_mgc"]
    redundant = gt * 0.9 + pd.Series(np.random.normal(0, 10, n), index=portfolio.index)
    r3 = score_candidate("candidate_redundant", redundant, portfolio)
    _print_result(r3)

    print("\n=== SUMMARY ===")
    print(f"{'Candidate':<30s} {'Score':>8s} {'Verdict':>20s}")
    for r in [r1, r2, r3]:
        print(f"{r.candidate_id:<30s} {r.marginal_score:>+8.3f} {r.verdict:>20s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="Run demo with synthetic candidates")
    args = ap.parse_args()
    if args.demo:
        demo()
        return 0
    print("No action specified. Use --demo or import score_candidate() programmatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
