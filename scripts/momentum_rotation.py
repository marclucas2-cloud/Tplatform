#!/usr/bin/env python3
"""
Strategie Momentum Rotation mensuelle sur ETFs.

Edge academique : Jegadeesh & Titman (1993), AQR Capital.
Les actifs ayant le plus performe sur les 3-12 derniers mois
tendent a continuer de surperformer a court terme.

Methode :
  1. Univers : 10-15 ETFs diversifies (secteurs, geographies, obligations)
  2. Chaque mois, classer les ETFs par momentum (ROC 3/6/12 mois)
  3. Acheter les N meilleurs, vendre les N pires (ou 100% long top N)
  4. Rebalancer mensuellement
  5. Filtre regime : si SPY < SMA(200), tout en cash (crash filter)

Avantage vs intraday :
  - Rebalancement mensuel → 12 trades/an/ETF → couts negligeables
  - Donnees daily sur 10+ ans disponibles via yfinance
  - Edge documente academiquement et robuste cross-market

Usage :
    python scripts/momentum_rotation.py
    python scripts/momentum_rotation.py --top 3 --lookback 6
    python scripts/momentum_rotation.py --long-only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.loader import OHLCVLoader


# ─── Configuration ────────────────────────────────────────────────────────────

# Univers ETFs diversifie (actions, obligations, commodites, international)
ETF_UNIVERSE = [
    "SPY",   # S&P 500
    "QQQ",   # NASDAQ 100
    "IWM",   # Russell 2000
    "EFA",   # MSCI EAFE (International Developed)
    "EEM",   # MSCI Emerging Markets
    "TLT",   # US Treasury 20+ ans
    "IEF",   # US Treasury 7-10 ans
    "GLD",   # Or
    "XLE",   # Energy
    "XLF",   # Financials
    "XLK",   # Technology
    "XLV",   # Healthcare
    "VNQ",   # Real Estate (REIT)
]

# Benchmark pour le crash filter
BENCHMARK = "SPY"
SMA_CRASH_PERIOD = 200  # SMA(200) daily


def load_monthly_data(tickers: list[str], period: str = "10y") -> pd.DataFrame:
    """Charge les prix mensuels (dernier close du mois) pour tous les tickers."""
    all_closes = {}

    for ticker in tickers:
        try:
            data = OHLCVLoader.from_yfinance(ticker, "1D", period=period)
            monthly = data.df["close"].resample("ME").last()
            all_closes[ticker] = monthly
        except Exception as e:
            print(f"  WARN: {ticker} ignore ({e})")

    df = pd.DataFrame(all_closes).dropna()
    return df


def compute_momentum(prices: pd.DataFrame, lookback_months: int) -> pd.DataFrame:
    """Calcule le momentum (ROC) sur N mois pour chaque ETF."""
    return prices.pct_change(lookback_months)


def run_rotation_backtest(
    prices: pd.DataFrame,
    lookback: int = 6,
    top_n: int = 3,
    long_only: bool = True,
    crash_filter: bool = True,
    cost_pct: float = 0.03,  # cout round-trip en % (0.03% = realiste)
    initial_capital: float = 100_000,
) -> dict:
    """
    Backtest de la strategie momentum rotation.

    Chaque mois :
      1. Calculer le momentum (ROC) sur `lookback` mois
      2. Classer les ETFs
      3. Acheter les `top_n` meilleurs (equal weight)
      4. Si crash_filter et SPY < SMA(200), tout en cash

    Retourne un dict avec equity curve et metriques.
    """
    momentum = compute_momentum(prices, lookback)
    n_months = len(prices)

    # SMA crash filter sur SPY (daily) — approxime via monthly
    if crash_filter and BENCHMARK in prices.columns:
        spy_sma = prices[BENCHMARK].rolling(10).mean()  # ~10 mois ≈ SMA(200) daily
    else:
        spy_sma = pd.Series(0, index=prices.index)

    capital = initial_capital
    equity = [capital]
    dates = [prices.index[lookback]]
    trades_total = 0
    costs_total = 0.0
    monthly_returns = []
    holdings_log = []

    prev_holdings = set()

    for i in range(lookback + 1, n_months):
        date = prices.index[i]
        mom = momentum.iloc[i - 1]  # momentum calcule sur les mois precedents (no lookahead)

        # Crash filter : si SPY < SMA(200), tout en cash
        if crash_filter and BENCHMARK in prices.columns:
            spy_price = prices[BENCHMARK].iloc[i - 1]
            spy_sma_val = spy_sma.iloc[i - 1]
            if spy_price < spy_sma_val and not np.isnan(spy_sma_val):
                # Tout en cash — vendre les positions
                if prev_holdings:
                    costs = len(prev_holdings) * capital * cost_pct / 100
                    costs_total += costs
                    trades_total += len(prev_holdings)
                    prev_holdings = set()
                equity.append(capital)
                dates.append(date)
                monthly_returns.append(0.0)
                holdings_log.append({"date": date, "holdings": "CASH (crash filter)"})
                continue

        # Classer par momentum (descending)
        valid_mom = mom.dropna().sort_values(ascending=False)

        if len(valid_mom) < top_n:
            equity.append(capital)
            dates.append(date)
            monthly_returns.append(0.0)
            continue

        # Selectionner les top N
        if long_only:
            selected = set(valid_mom.head(top_n).index)
        else:
            # Long top N, short bottom N
            selected = set(valid_mom.head(top_n).index) | set(valid_mom.tail(top_n).index)

        # Calculer les couts de rebalancement
        turnover = selected.symmetric_difference(prev_holdings)
        n_trades = len(turnover)
        costs = n_trades * (capital / top_n) * cost_pct / 100
        costs_total += costs
        trades_total += n_trades

        # Rendement du mois (equal weight sur les top N, long only)
        if long_only:
            top_tickers = valid_mom.head(top_n).index.tolist()
            month_returns = []
            for ticker in top_tickers:
                if prices[ticker].iloc[i - 1] > 0:
                    ret = (prices[ticker].iloc[i] / prices[ticker].iloc[i - 1]) - 1
                    month_returns.append(ret)
            avg_return = np.mean(month_returns) if month_returns else 0.0
        else:
            # Long-short : long top N, short bottom N
            top_tickers = valid_mom.head(top_n).index.tolist()
            bottom_tickers = valid_mom.tail(top_n).index.tolist()
            long_rets = []
            short_rets = []
            for t in top_tickers:
                if prices[t].iloc[i - 1] > 0:
                    long_rets.append((prices[t].iloc[i] / prices[t].iloc[i - 1]) - 1)
            for t in bottom_tickers:
                if prices[t].iloc[i - 1] > 0:
                    short_rets.append(-((prices[t].iloc[i] / prices[t].iloc[i - 1]) - 1))
            all_rets = long_rets + short_rets
            avg_return = np.mean(all_rets) if all_rets else 0.0

        capital = capital * (1 + avg_return) - costs
        equity.append(capital)
        dates.append(date)
        monthly_returns.append(avg_return)
        prev_holdings = selected

        holdings_log.append({
            "date": date,
            "holdings": ", ".join(sorted(selected)),
            "return": f"{avg_return:+.2%}",
        })

    # Metriques
    equity_series = pd.Series(equity, index=dates)
    total_return = (capital - initial_capital) / initial_capital * 100

    monthly_rets = np.array(monthly_returns)
    n_years = len(monthly_rets) / 12

    if len(monthly_rets) > 1 and monthly_rets.std() > 0:
        sharpe = (monthly_rets.mean() / monthly_rets.std()) * np.sqrt(12)
    else:
        sharpe = 0.0

    downside = monthly_rets[monthly_rets < 0]
    if len(downside) > 0 and downside.std() > 0:
        sortino = (monthly_rets.mean() / downside.std()) * np.sqrt(12)
    else:
        sortino = 0.0

    # Max drawdown
    peak = equity_series.cummax()
    dd = (equity_series - peak) / peak * 100
    max_dd = abs(dd.min())

    # CAGR
    if n_years > 0 and capital > 0:
        cagr = ((capital / initial_capital) ** (1 / n_years) - 1) * 100
    else:
        cagr = 0.0

    # Win rate mensuel
    wins = (monthly_rets > 0).sum()
    total_months = len(monthly_rets)
    win_rate = wins / total_months * 100 if total_months > 0 else 0

    # Buy & hold SPY benchmark
    if BENCHMARK in prices.columns:
        spy_start = prices[BENCHMARK].iloc[lookback]
        spy_end = prices[BENCHMARK].iloc[-1]
        spy_return = (spy_end / spy_start - 1) * 100
        spy_cagr = ((spy_end / spy_start) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    else:
        spy_return = 0
        spy_cagr = 0

    return {
        "equity": equity_series,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
        "trades": trades_total,
        "costs": costs_total,
        "win_rate": win_rate,
        "n_months": total_months,
        "n_years": n_years,
        "capital_final": capital,
        "spy_return": spy_return,
        "spy_cagr": spy_cagr,
        "holdings_log": holdings_log,
    }


def print_results(r: dict, config: dict) -> None:
    """Affiche les resultats du backtest."""
    print(f"\n{'='*70}")
    print(f"  MOMENTUM ROTATION — RESULTATS BACKTEST")
    print(f"{'='*70}")
    print(f"  Config    : top {config['top_n']}, lookback {config['lookback']}m, "
          f"{'long-only' if config['long_only'] else 'long-short'}, "
          f"crash_filter={'ON' if config['crash_filter'] else 'OFF'}")
    print(f"  Univers   : {len(config['tickers'])} ETFs")
    print(f"  Periode   : {r['n_years']:.1f} ans ({r['n_months']} mois)")
    print(f"  Capital   : ${config['capital']:,.0f} -> ${r['capital_final']:,.0f}")
    print(f"{'='*70}")

    print(f"\n  {'Metrique':<25} {'Momentum':>12} {'SPY B&H':>12}")
    print(f"  {'-'*50}")
    print(f"  {'Return total':<25} {r['total_return']:>+11.2f}% {r['spy_return']:>+11.2f}%")
    print(f"  {'CAGR':<25} {r['cagr']:>+11.2f}% {r['spy_cagr']:>+11.2f}%")
    print(f"  {'Sharpe (annualise)':<25} {r['sharpe']:>+11.3f}")
    print(f"  {'Sortino':<25} {r['sortino']:>+11.3f}")
    print(f"  {'Max Drawdown':<25} {r['max_dd']:>11.2f}%")
    print(f"  {'Win Rate mensuel':<25} {r['win_rate']:>11.1f}%")
    print(f"  {'Trades totaux':<25} {r['trades']:>11}")
    cost_str = f"${r['costs']:.2f}"
    print(f"  {'Couts totaux':<25} {cost_str:>11}")

    # Derniers holdings
    if r["holdings_log"]:
        print(f"\n  Derniers rebalancements :")
        for h in r["holdings_log"][-5:]:
            ret = h.get("return", "")
            print(f"    {h['date'].strftime('%Y-%m')} : {h['holdings']} {ret}")

    print(f"\n{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Momentum Rotation ETFs")
    parser.add_argument("--lookback", type=int, default=6,
                        help="Lookback momentum en mois (defaut: 6)")
    parser.add_argument("--top", type=int, default=3,
                        help="Nombre d'ETFs a detenir (defaut: 3)")
    parser.add_argument("--long-only", action="store_true", default=True,
                        help="Mode long-only (defaut)")
    parser.add_argument("--long-short", action="store_true",
                        help="Mode long-short")
    parser.add_argument("--no-crash-filter", action="store_true",
                        help="Desactiver le crash filter (SPY < SMA200)")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--period", default="10y",
                        help="Periode yfinance (defaut: 10y)")
    parser.add_argument("--cost", type=float, default=0.03,
                        help="Cout round-trip en %% (defaut: 0.03)")
    args = parser.parse_args()

    long_only = not args.long_short

    print(f"\n  Chargement des donnees ({args.period})...")
    prices = load_monthly_data(ETF_UNIVERSE, period=args.period)
    print(f"  {len(prices.columns)} ETFs charges, {len(prices)} mois")

    # Tester plusieurs lookbacks
    lookbacks = [3, 6, 9, 12] if args.lookback == 6 else [args.lookback]
    tops = [2, 3, 4, 5] if args.top == 3 else [args.top]

    best_sharpe = -999
    best_config = None
    best_result = None

    for lb in lookbacks:
        for top_n in tops:
            r = run_rotation_backtest(
                prices,
                lookback=lb,
                top_n=top_n,
                long_only=long_only,
                crash_filter=not args.no_crash_filter,
                cost_pct=args.cost,
                initial_capital=args.capital,
            )

            label = f"top={top_n} lb={lb}m"
            print(f"  [{label:>15}] Sharpe {r['sharpe']:+.3f}, "
                  f"CAGR {r['cagr']:+.1f}%, DD {r['max_dd']:.1f}%, "
                  f"WR {r['win_rate']:.0f}%, {r['trades']} trades")

            if r["sharpe"] > best_sharpe:
                best_sharpe = r["sharpe"]
                best_config = {"lookback": lb, "top_n": top_n,
                               "long_only": long_only,
                               "crash_filter": not args.no_crash_filter,
                               "tickers": list(prices.columns),
                               "capital": args.capital}
                best_result = r

    if best_result:
        print_results(best_result, best_config)


if __name__ == "__main__":
    main()
