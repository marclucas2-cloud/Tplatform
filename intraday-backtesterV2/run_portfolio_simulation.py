"""
Portfolio Simulation — Backtest portefeuille combine multi-scenario.

Simule 4 scenarios d'expansion du portefeuille en utilisant les
rendements quotidiens estimes des strategies backtestees.

Scenarios :
  A) US ONLY (baseline) — 14 strategies Alpaca actuelles
  B) US + EU actions — + 4 EU winners
  C) US + EU + Forex — + 3 paires FX
  D) US + EU + FX + Futures + Levier — tout + 2 futures

Pour chaque scenario :
  - Sharpe portefeuille estime
  - Max DD estime
  - Return annualise
  - % du capital investi
  - Heures de trading couvertes
  - Nombre de strategies
  - Correlation EU/US (si applicable)

Usage :
    python intraday-backtesterV2/run_portfolio_simulation.py
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# =====================================================================
# Strategy Database — rendements quotidiens estimes
# =====================================================================
# Source : backtests valides (session 26 mars 2026)
# return_daily = return_total / n_trading_days
# volatility_daily = return_daily / (sharpe * sqrt(1/252))
#   ou estimation directe depuis DD / sqrt(n_days)

STRATEGIES_DB = {
    # ---------------------------------------------------------------
    # US INTRADAY (11 strategies) — donnees session reports
    # ---------------------------------------------------------------
    "opex_gamma_pin": {
        "name": "OpEx Gamma Pin",
        "market": "us",
        "tier": "S",
        "sharpe": 10.41,
        "return_annual_pct": 45.0,
        "max_dd_pct": 0.5,
        "volatility_annual": 0.043,
        "allocation_base": 0.12,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "overnight_gap_continuation": {
        "name": "Overnight Gap Continuation",
        "market": "us",
        "tier": "A",
        "sharpe": 5.22,
        "return_annual_pct": 25.0,
        "max_dd_pct": 0.8,
        "volatility_annual": 0.048,
        "allocation_base": 0.10,
        "edge_type": "momentum",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "gold_fear_gauge": {
        "name": "Gold Fear Gauge",
        "market": "us",
        "tier": "B",
        "sharpe": 5.01,
        "return_annual_pct": 12.0,
        "max_dd_pct": 0.12,
        "volatility_annual": 0.024,
        "allocation_base": 0.02,
        "edge_type": "short",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_short",
    },
    "crypto_proxy_regime_v2": {
        "name": "Crypto-Proxy Regime V2",
        "market": "us",
        "tier": "A",
        "sharpe": 3.49,
        "return_annual_pct": 18.0,
        "max_dd_pct": 1.2,
        "volatility_annual": 0.052,
        "allocation_base": 0.08,
        "edge_type": "momentum",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_crypto",
    },
    "dow_seasonal": {
        "name": "Day-of-Week Seasonal",
        "market": "us",
        "tier": "A",
        "sharpe": 3.42,
        "return_annual_pct": 15.0,
        "max_dd_pct": 0.9,
        "volatility_annual": 0.044,
        "allocation_base": 0.08,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "vwap_micro_deviation": {
        "name": "VWAP Micro-Deviation",
        "market": "us",
        "tier": "A",
        "sharpe": 3.08,
        "return_annual_pct": 14.0,
        "max_dd_pct": 1.0,
        "volatility_annual": 0.045,
        "allocation_base": 0.10,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "orb_5min_v2": {
        "name": "ORB 5-Min V2",
        "market": "us",
        "tier": "B",
        "sharpe": 2.28,
        "return_annual_pct": 10.0,
        "max_dd_pct": 1.5,
        "volatility_annual": 0.044,
        "allocation_base": 0.04,
        "edge_type": "momentum",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "mean_reversion_v2": {
        "name": "Mean Reversion V2",
        "market": "us",
        "tier": "B",
        "sharpe": 1.44,
        "return_annual_pct": 6.0,
        "max_dd_pct": 1.8,
        "volatility_annual": 0.042,
        "allocation_base": 0.03,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "corr_regime_hedge": {
        "name": "Correlation Regime Hedge",
        "market": "us",
        "tier": "B",
        "sharpe": 1.09,
        "return_annual_pct": 5.0,
        "max_dd_pct": 0.10,
        "volatility_annual": 0.046,
        "allocation_base": 0.03,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_diversifier",
    },
    "triple_ema_pullback": {
        "name": "Triple EMA Pullback",
        "market": "us",
        "tier": "B",
        "sharpe": 1.06,
        "return_annual_pct": 4.5,
        "max_dd_pct": 1.5,
        "volatility_annual": 0.042,
        "allocation_base": 0.00,  # desactive en bear
        "edge_type": "momentum",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_intraday",
    },
    "late_day_mean_reversion": {
        "name": "Late Day Mean Reversion",
        "market": "us",
        "tier": "B",
        "sharpe": 0.60,
        "return_annual_pct": 2.5,
        "max_dd_pct": 2.0,
        "volatility_annual": 0.042,
        "allocation_base": 0.02,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (20, 22),
        "correlation_group": "us_intraday",
    },

    # ---------------------------------------------------------------
    # US DAILY/MONTHLY (3 strategies)
    # ---------------------------------------------------------------
    "momentum_25etf": {
        "name": "Momentum 25 ETFs",
        "market": "us",
        "tier": "C",
        "sharpe": 0.80,
        "return_annual_pct": 8.0,
        "max_dd_pct": 15.0,
        "volatility_annual": 0.10,
        "allocation_base": 0.02,
        "edge_type": "momentum",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_daily",
    },
    "pairs_mu_amat": {
        "name": "Pairs MU/AMAT",
        "market": "us",
        "tier": "C",
        "sharpe": 1.20,
        "return_annual_pct": 6.0,
        "max_dd_pct": 5.0,
        "volatility_annual": 0.05,
        "allocation_base": 0.02,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_pairs",
    },
    "vrp_rotation": {
        "name": "VRP SVXY/SPY/TLT",
        "market": "us",
        "tier": "C",
        "sharpe": 0.90,
        "return_annual_pct": 7.0,
        "max_dd_pct": 10.0,
        "volatility_annual": 0.078,
        "allocation_base": 0.02,
        "edge_type": "event",
        "trading_hours_cet": (15, 22),
        "correlation_group": "us_vol",
    },

    # ---------------------------------------------------------------
    # EU ACTIONS (4 winners valides)
    # Source: eu_results.json + eu_phase2_p0_results.json + eu_phase2_p1p2_results.json
    # ---------------------------------------------------------------
    "eu_gap_open": {
        "name": "EU Gap Open (US Close Signal)",
        "market": "eu",
        "tier": "A",
        "sharpe": 8.56,
        "return_annual_pct": 15.5,    # 3.1% sur 54 jours -> ~14.5% annualise
        "max_dd_pct": 0.31,
        "volatility_annual": 0.018,
        "allocation_base": 0.06,
        "edge_type": "momentum",
        "trading_hours_cet": (9, 17),
        "correlation_group": "eu_equity",
    },
    "bce_momentum_v2": {
        "name": "BCE Momentum Drift v2",
        "market": "eu",
        "tier": "A",
        "sharpe": 14.93,
        "return_annual_pct": 17.3,    # 8.66% sur ~250 jours (6 ans)
        "max_dd_pct": 0.49,
        "volatility_annual": 0.012,
        "allocation_base": 0.06,
        "edge_type": "event",
        "trading_hours_cet": (9, 17),
        "correlation_group": "eu_event",
    },
    "asml_earnings_chain": {
        "name": "ASML Earnings Chain",
        "market": "eu",
        "tier": "B",
        "sharpe": 0.61,
        "return_annual_pct": 2.4,     # 0.38% sur ~40 jours
        "max_dd_pct": 0.71,
        "volatility_annual": 0.039,
        "allocation_base": 0.03,
        "edge_type": "event",
        "trading_hours_cet": (9, 17),
        "correlation_group": "eu_equity",
    },
    "auto_german_sympathy": {
        "name": "Auto Sector German Sympathy",
        "market": "eu",
        "tier": "A",
        "sharpe": 13.43,
        "return_annual_pct": 9.3,     # 1.86% sur ~50 jours
        "max_dd_pct": 0.06,
        "volatility_annual": 0.007,
        "allocation_base": 0.05,
        "edge_type": "momentum",
        "trading_hours_cet": (9, 17),
        "correlation_group": "eu_equity",
    },

    # ---------------------------------------------------------------
    # FOREX (3 paires validees)
    # Source: eu_phase2_p1p2_results.json
    # ---------------------------------------------------------------
    "fx_eurusd_trend": {
        "name": "EUR/USD Trend Following",
        "market": "fx",
        "tier": "B",
        "sharpe": 4.62,
        "return_annual_pct": 6.5,     # 1.3% sur ~50 jours
        "max_dd_pct": 0.42,
        "volatility_annual": 0.014,
        "allocation_base": 0.04,
        "edge_type": "momentum",
        "trading_hours_cet": (0, 24),  # 24/7
        "correlation_group": "fx",
    },
    "fx_eurgbp_mr": {
        "name": "EUR/GBP Mean Reversion",
        "market": "fx",
        "tier": "B",
        "sharpe": 3.65,
        "return_annual_pct": 5.6,     # 1.12% sur ~50 jours
        "max_dd_pct": 0.62,
        "volatility_annual": 0.015,
        "allocation_base": 0.03,
        "edge_type": "mean_reversion",
        "trading_hours_cet": (0, 24),
        "correlation_group": "fx",
    },
    "fx_eurjpy_carry": {
        "name": "EUR/JPY Carry + Momentum",
        "market": "fx",
        "tier": "B",
        "sharpe": 2.50,
        "return_annual_pct": 4.65,    # 0.93% sur ~50 jours
        "max_dd_pct": 0.43,
        "volatility_annual": 0.019,
        "allocation_base": 0.03,
        "edge_type": "momentum",
        "trading_hours_cet": (0, 24),
        "correlation_group": "fx",
    },

    # ---------------------------------------------------------------
    # FUTURES (2 strategies validees)
    # Source: eu_phase2_p2p3_results.json
    # ---------------------------------------------------------------
    "brent_lag_play": {
        "name": "Brent Lag Play",
        "market": "futures",
        "tier": "A",
        "sharpe": 4.08,
        "return_annual_pct": 25.25,   # sur 5 ans de donnees
        "max_dd_pct": 1.42,
        "volatility_annual": 0.062,
        "allocation_base": 0.05,
        "edge_type": "momentum",
        "trading_hours_cet": (9, 22),
        "correlation_group": "commodity",
        "leverage": 2.0,
    },
    "dax_post_bce": {
        "name": "DAX Breakout Post-BCE",
        "market": "futures",
        "tier": "B",
        "sharpe": 3.49,
        "return_annual_pct": 3.75,    # 0.75% sur ~50 jours
        "max_dd_pct": 0.27,
        "volatility_annual": 0.011,
        "allocation_base": 0.03,
        "edge_type": "event",
        "trading_hours_cet": (9, 17),
        "correlation_group": "eu_event",
        "leverage": 2.0,
    },
}


# =====================================================================
# Correlation matrices estimees entre groupes
# =====================================================================
# Source : correlations empiriques typiques entre classes d'actifs

CORRELATION_MATRIX = {
    ("us_intraday", "us_intraday"): 0.45,
    ("us_intraday", "us_crypto"): 0.35,
    ("us_intraday", "us_short"): -0.30,
    ("us_intraday", "us_diversifier"): -0.10,
    ("us_intraday", "us_daily"): 0.20,
    ("us_intraday", "us_pairs"): 0.05,
    ("us_intraday", "us_vol"): 0.15,
    ("us_intraday", "eu_equity"): 0.25,
    ("us_intraday", "eu_event"): 0.10,
    ("us_intraday", "fx"): 0.05,
    ("us_intraday", "commodity"): 0.15,
    ("us_crypto", "us_short"): -0.20,
    ("us_crypto", "eu_equity"): 0.15,
    ("us_crypto", "fx"): 0.10,
    ("us_crypto", "commodity"): 0.20,
    ("us_short", "eu_equity"): -0.15,
    ("us_short", "fx"): -0.05,
    ("us_short", "commodity"): -0.10,
    ("us_diversifier", "eu_equity"): 0.10,
    ("us_diversifier", "fx"): 0.15,
    ("us_daily", "eu_equity"): 0.30,
    ("us_daily", "fx"): 0.10,
    ("us_daily", "commodity"): 0.25,
    ("us_pairs", "eu_equity"): 0.05,
    ("us_vol", "eu_equity"): 0.20,
    ("eu_equity", "eu_equity"): 0.50,
    ("eu_equity", "eu_event"): 0.30,
    ("eu_equity", "fx"): 0.20,
    ("eu_equity", "commodity"): 0.25,
    ("eu_event", "eu_event"): 0.20,
    ("eu_event", "fx"): 0.10,
    ("eu_event", "commodity"): 0.15,
    ("fx", "fx"): 0.40,
    ("fx", "commodity"): 0.15,
    ("commodity", "commodity"): 0.30,
}


def get_correlation(group_a: str, group_b: str) -> float:
    """Retourne la correlation estimee entre deux groupes."""
    if group_a == group_b:
        return CORRELATION_MATRIX.get((group_a, group_b), 0.30)
    key = (group_a, group_b)
    rev_key = (group_b, group_a)
    return CORRELATION_MATRIX.get(key, CORRELATION_MATRIX.get(rev_key, 0.10))


# =====================================================================
# Portfolio Simulation Engine
# =====================================================================

def calculate_portfolio_metrics(
    strategies: Dict[str, dict],
    capital: float = 100_000,
    leverage_factor: float = 1.0,
) -> dict:
    """Calcule les metriques du portefeuille combine.

    Utilise la formule du Sharpe ratio portefeuille :
      Sharpe_p = (sum(w_i * r_i)) / sqrt(sum(w_i * w_j * cov_ij))

    Args:
        strategies: sous-ensemble de STRATEGIES_DB a inclure
        capital: capital total
        leverage_factor: multiplicateur de levier global

    Returns:
        {sharpe, return_annual, max_dd, capital_invested_pct, hours_covered, ...}
    """
    if not strategies:
        return {"sharpe": 0, "return_annual_pct": 0, "max_dd_pct": 0}

    # Normaliser les allocations pour que sum = capital investi
    total_alloc = sum(s.get("allocation_base", 0.05) for s in strategies.values())
    if total_alloc == 0:
        total_alloc = 1.0

    weights = {}
    for key, s in strategies.items():
        w = s.get("allocation_base", 0.05)
        # Appliquer le levier pour les positions leveragees
        strat_leverage = s.get("leverage", 1.0) if leverage_factor > 1.0 else 1.0
        weights[key] = w * strat_leverage

    # Return portefeuille = sum(w_i * return_i) * leverage
    portfolio_return = sum(
        weights[k] * strategies[k]["return_annual_pct"] / 100.0
        for k in strategies
    )

    # Variance portefeuille = sum_i sum_j (w_i * w_j * sigma_i * sigma_j * rho_ij)
    portfolio_variance = 0.0
    keys = list(strategies.keys())
    for i, ki in enumerate(keys):
        for j, kj in enumerate(keys):
            wi = weights[ki]
            wj = weights[kj]
            si = strategies[ki]["volatility_annual"]
            sj = strategies[kj]["volatility_annual"]
            gi = strategies[ki]["correlation_group"]
            gj = strategies[kj]["correlation_group"]

            if ki == kj:
                rho = 1.0
            else:
                rho = get_correlation(gi, gj)

            portfolio_variance += wi * wj * si * sj * rho

    portfolio_vol = math.sqrt(max(portfolio_variance, 1e-12))
    portfolio_sharpe = portfolio_return / portfolio_vol if portfolio_vol > 0 else 0

    # Max DD estime : approximation par la formule empirique
    # DD_portfolio ~ max(individual DDs * weight) * diversification_factor
    # Ou : DD ~ vol * sqrt(2 * ln(T)) / sharpe (pour un process mean-reverting)
    individual_weighted_dds = [
        weights[k] * strategies[k]["max_dd_pct"] / 100.0
        for k in strategies
    ]
    # Diversification factor ~ 1 / sqrt(N) pour N strategies decorrelees
    n_strats = len(strategies)
    div_factor = 1.0 / math.sqrt(n_strats) if n_strats > 1 else 1.0

    # DD combine = somme ponderee * diversification + correlation term
    avg_corr = 0.0
    n_pairs = 0
    for i, ki in enumerate(keys):
        for j, kj in enumerate(keys):
            if i < j:
                gi = strategies[ki]["correlation_group"]
                gj = strategies[kj]["correlation_group"]
                avg_corr += get_correlation(gi, gj)
                n_pairs += 1
    avg_corr = avg_corr / n_pairs if n_pairs > 0 else 0.3

    max_dd_individual = max(individual_weighted_dds) if individual_weighted_dds else 0
    max_dd_portfolio = max_dd_individual * (div_factor + avg_corr * (1 - div_factor))
    max_dd_portfolio = min(max_dd_portfolio * 100, 25.0)  # cap a 25%

    # Heures couvertes
    all_hours = set()
    for s in strategies.values():
        start, end = s["trading_hours_cet"]
        if start < end:
            all_hours.update(range(start, end))
        else:
            all_hours.update(range(start, 24))
            all_hours.update(range(0, end))
    hours_covered = len(all_hours)

    # Capital investi (% du total)
    capital_invested = sum(weights.values())

    # Markets breakdown
    markets = set(s["market"] for s in strategies.values())

    # Correlation EU/US
    eu_strats = [k for k, s in strategies.items() if s["market"] == "eu"]
    us_strats = [k for k, s in strategies.items() if s["market"] == "us"]
    eu_us_corr = None
    if eu_strats and us_strats:
        corr_sum = 0
        corr_count = 0
        for ek in eu_strats:
            for uk in us_strats:
                gi = strategies[ek]["correlation_group"]
                gj = strategies[uk]["correlation_group"]
                corr_sum += get_correlation(gi, gj)
                corr_count += 1
        eu_us_corr = round(corr_sum / corr_count, 3) if corr_count > 0 else None

    return {
        "n_strategies": n_strats,
        "sharpe": round(portfolio_sharpe, 2),
        "return_annual_pct": round(portfolio_return * 100, 2),
        "volatility_annual_pct": round(portfolio_vol * 100, 2),
        "max_dd_pct": round(max_dd_portfolio, 2),
        "capital_invested_pct": round(capital_invested * 100, 1),
        "hours_covered": hours_covered,
        "hours_covered_pct": round(hours_covered / 24 * 100, 1),
        "markets": sorted(list(markets)),
        "avg_inter_strategy_correlation": round(avg_corr, 3),
        "eu_us_correlation": eu_us_corr,
        "capital": capital,
        "leverage_factor": leverage_factor,
    }


def run_scenario_a() -> dict:
    """Scenario A : US ONLY (baseline actuel).

    14 strategies US Alpaca, allocation Tier S/A/B/C actuelle.
    """
    us_keys = [
        k for k, s in STRATEGIES_DB.items()
        if s["market"] == "us"
    ]
    strategies = {k: STRATEGIES_DB[k] for k in us_keys}
    metrics = calculate_portfolio_metrics(strategies)
    metrics["scenario"] = "A"
    metrics["name"] = "US ONLY (baseline)"
    metrics["description"] = (
        "14 strategies US Alpaca (11 intraday + 3 daily/monthly). "
        "Allocation Tier S/A/B/C. ~58% du capital investi. "
        "Trading 15:30-22:00 CET uniquement."
    )
    return metrics


def run_scenario_b() -> dict:
    """Scenario B : US + EU actions.

    14 US + 4 EU actions winners.
    """
    us_keys = [k for k, s in STRATEGIES_DB.items() if s["market"] == "us"]
    eu_keys = [k for k, s in STRATEGIES_DB.items() if s["market"] == "eu"]
    strategies = {k: STRATEGIES_DB[k] for k in us_keys + eu_keys}
    metrics = calculate_portfolio_metrics(strategies)
    metrics["scenario"] = "B"
    metrics["name"] = "US + EU Actions"
    metrics["description"] = (
        "14 US + 4 EU winners (EU Gap Open, BCE Momentum, ASML Chain, Auto German). "
        "2 creneaux horaires (EU matin 9:00-17:00 + US apres-midi 15:30-22:00). "
        "Overlap 15:30-17:00."
    )
    return metrics


def run_scenario_c() -> dict:
    """Scenario C : US + EU + Forex.

    14 US + 4 EU + 3 FX paires.
    """
    keys = [
        k for k, s in STRATEGIES_DB.items()
        if s["market"] in ("us", "eu", "fx")
    ]
    strategies = {k: STRATEGIES_DB[k] for k in keys}
    metrics = calculate_portfolio_metrics(strategies)
    metrics["scenario"] = "C"
    metrics["name"] = "US + EU + Forex"
    metrics["description"] = (
        "14 US + 4 EU + 3 Forex (EUR/USD, EUR/GBP, EUR/JPY). "
        "3 creneaux + FX 24/7. ~78% du capital investi."
    )
    return metrics


def run_scenario_d() -> dict:
    """Scenario D : US + EU + Forex + Futures + Levier structurel.

    Toutes les strategies + Brent Lag + DAX Post-BCE.
    Levier 2-3:1 sur futures/FX.
    """
    strategies = dict(STRATEGIES_DB)
    metrics = calculate_portfolio_metrics(strategies, leverage_factor=2.0)
    metrics["scenario"] = "D"
    metrics["name"] = "US + EU + FX + Futures + Levier"
    metrics["description"] = (
        "Toutes les strategies (14 US + 4 EU + 3 FX + 2 Futures). "
        "Levier 2-3:1 sur futures/FX. Allocation cross-timezone dynamique. "
        "~90% du capital utilise avec stacking temporel."
    )
    return metrics


def run_all_scenarios() -> dict:
    """Execute les 4 scenarios et retourne les resultats."""
    results = {
        "run_date": datetime.now().isoformat(),
        "capital": 100_000,
        "total_strategies_available": len(STRATEGIES_DB),
        "scenarios": {},
    }

    for label, runner in [
        ("A", run_scenario_a),
        ("B", run_scenario_b),
        ("C", run_scenario_c),
        ("D", run_scenario_d),
    ]:
        print(f"\n{'='*60}")
        print(f"Scenario {label}")
        print(f"{'='*60}")
        scenario = runner()
        results["scenarios"][label] = scenario

        print(f"  Nom         : {scenario['name']}")
        print(f"  Strategies  : {scenario['n_strategies']}")
        print(f"  Sharpe      : {scenario['sharpe']}")
        print(f"  Return ann. : {scenario['return_annual_pct']}%")
        print(f"  Vol ann.    : {scenario['volatility_annual_pct']}%")
        print(f"  Max DD      : {scenario['max_dd_pct']}%")
        print(f"  Capital inv.: {scenario['capital_invested_pct']}%")
        print(f"  Heures      : {scenario['hours_covered']}/24 ({scenario['hours_covered_pct']}%)")
        print(f"  Marches     : {', '.join(scenario['markets'])}")
        if scenario.get("eu_us_correlation") is not None:
            print(f"  Corr EU/US  : {scenario['eu_us_correlation']}")

    # Comparison table
    print(f"\n{'='*60}")
    print("COMPARAISON DES SCENARIOS")
    print(f"{'='*60}")
    print(f"{'Metrique':<25} {'A (US)':<15} {'B (+EU)':<15} {'C (+FX)':<15} {'D (+Fut+Lev)':<15}")
    print("-" * 85)

    for metric_key, metric_name in [
        ("n_strategies", "Strategies"),
        ("sharpe", "Sharpe"),
        ("return_annual_pct", "Return ann. %"),
        ("volatility_annual_pct", "Vol ann. %"),
        ("max_dd_pct", "Max DD %"),
        ("capital_invested_pct", "Capital inv. %"),
        ("hours_covered", "Heures /24"),
    ]:
        vals = []
        for sc in ["A", "B", "C", "D"]:
            v = results["scenarios"][sc].get(metric_key, "-")
            vals.append(str(v))
        print(f"{metric_name:<25} {vals[0]:<15} {vals[1]:<15} {vals[2]:<15} {vals[3]:<15}")

    return results


def save_results(results: dict, output_dir: str):
    """Sauvegarde les resultats en JSON."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "portfolio_simulation.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResultats sauvegardes dans {output_path}")
    return output_path


# =====================================================================
# Main
# =====================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("PORTFOLIO SIMULATION — Multi-Scenario Backtest")
    print(f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Capital : $100,000")
    print(f"Strategies disponibles : {len(STRATEGIES_DB)}")
    print("=" * 60)

    results = run_all_scenarios()

    output_dir = str(PROJECT_ROOT / "output" / "session_20260326")
    save_results(results, output_dir)

    print("\n[OK] Simulation terminee.")
