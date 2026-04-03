#!/usr/bin/env python3
"""
Backtest comparatif des 6 strategies intraday/swing sur SPY.

Lance chaque strategie sur donnees reelles yfinance et affiche un tableau
de comparaison avec metriques cles.

Strategies testees :
  1. ORB (Opening Range Breakout) — SPY 1H, 2 ans
  2. Gap and Go (momentum continuation) — SPY 1D, 5 ans
  3. Gap Fill (mean reversion) — SPY 1D, 5 ans
  4. VWAP Mean Reversion — SPY 1H, 2 ans
  5. RSI(2) Extreme Reversal — SPY 1D, 5 ans
  6. Relative Strength Momentum — SPY 1D, 5 ans

Couts realistes US equities (Alpaca / IBKR) :
  - Commission : $0 (Alpaca) / $0.005/action (IBKR)
  - Spread : ~$0.05 (liquid large cap)
  - Slippage : ~$0.10/side

Usage :
    python scripts/intraday_comparison.py
    python scripts/intraday_comparison.py --asset IWM
    python scripts/intraday_comparison.py --capital 100000
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest.engine import BacktestEngine
from core.data.loader import OHLCVLoader

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────────

STRATEGIES = [
    # (json_file, timeframe_override, period, description_courte)
    ("orb_spy_1h_v1.json",           "1H", "2y",  "ORB"),
    ("gap_go_spy_1d_v1.json",        "1D", "5y",  "Gap & Go"),
    ("gap_fill_spy_1d_v1.json",      "1D", "5y",  "Gap Fill"),
    ("vwap_spy_1h_v1.json",          "1H", "2y",  "VWAP MR"),
    ("rsi_extreme_spy_1d_v1.json",   "1D", "5y",  "RSI Extreme"),
    ("rel_strength_spy_1d_v1.json",  "1D", "5y",  "Rel Strength"),
]


def load_strategy(filename: str) -> dict:
    """Charge un JSON de strategie."""
    path = Path(__file__).parent.parent / "strategies" / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_backtest(strategy: dict, asset: str, timeframe: str,
                 period: str, capital: float) -> dict | None:
    """Lance un backtest et retourne le resultat."""
    try:
        # Override asset si specifie en CLI
        strategy = {**strategy, "asset": asset}

        data = OHLCVLoader.from_yfinance(asset, timeframe, period=period)
        engine = BacktestEngine(initial_capital=capital)
        result = engine.run(data, strategy)
        return result
    except Exception as e:
        logger.error(f"  ERREUR {strategy['strategy_id']}: {e}")
        return None


def format_table(results: list[dict]) -> str:
    """Formate les resultats en tableau ASCII."""
    # Header
    cols = [
        ("Strategie",    18),
        ("TF",            4),
        ("Trades",        7),
        ("Return%",       9),
        ("Sharpe",        8),
        ("Sortino",       8),
        ("MaxDD%",        8),
        ("WinRate%",      9),
        ("ProfitF",       8),
        ("Expect$",       9),
        ("Couts$",        9),
    ]

    header = " | ".join(f"{name:<{width}}" for name, width in cols)
    separator = "-+-".join("-" * width for _, width in cols)

    lines = [
        "",
        "=" * len(header),
        "  COMPARAISON STRATEGIES INTRADAY — BACKTEST REALISTE",
        "=" * len(header),
        "",
        header,
        separator,
    ]

    for r in results:
        if r is None:
            continue
        row = [
            f"{r['name']:<18}",
            f"{r['tf']:<4}",
            f"{r['trades']:>7}",
            f"{r['return']:>+8.2f}%",
            f"{r['sharpe']:>+7.3f}",
            f"{r['sortino']:>+7.3f}",
            f"{r['maxdd']:>7.2f}%",
            f"{r['winrate']:>8.1f}%",
            f"{r['pf']:>7.3f}",
            f"{r['expectancy']:>+8.4f}",
            f"{r['costs']:>8.2f}",
        ]
        lines.append(" | ".join(row))

    lines.append(separator)

    # Best strategy
    valid = [r for r in results if r is not None and r["sharpe"] > 0]
    if valid:
        best = max(valid, key=lambda x: x["sharpe"])
        lines.append(f"\n  MEILLEUR SHARPE : {best['name']} ({best['sharpe']:+.3f})")

        best_pf = max(valid, key=lambda x: x["pf"])
        lines.append(f"  MEILLEUR PROFIT FACTOR : {best_pf['name']} ({best_pf['pf']:.3f})")

        best_wr = max(valid, key=lambda x: x["winrate"])
        lines.append(f"  MEILLEUR WIN RATE : {best_wr['name']} ({best_wr['winrate']:.1f}%)")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Comparaison strategies intraday")
    parser.add_argument("--asset", default="SPY", help="Actif (defaut: SPY)")
    parser.add_argument("--capital", type=float, default=100_000,
                        help="Capital initial (defaut: 100000)")
    parser.add_argument("--position-pct", type=float, default=0.0,
                        help="Override max_position_pct (0 = utilise le JSON)")
    args = parser.parse_args()

    print(f"\n  Backtest comparatif sur {args.asset} — capital ${args.capital:,.0f}")
    print("  Couts : spread_pct + slippage_pct (% du prix, realiste)\n")

    results = []

    for filename, tf, period, short_name in STRATEGIES:
        print(f"  [{short_name:>15}] Chargement {filename}...", end=" ", flush=True)

        strategy = load_strategy(filename)
        if args.position_pct > 0:
            strategy["parameters"]["max_position_pct"] = args.position_pct
        result = run_backtest(strategy, args.asset, tf, period, args.capital)

        if result is not None:
            results.append({
                "name": short_name,
                "tf": tf,
                "trades": result.total_trades,
                "return": result.total_return_pct,
                "sharpe": result.sharpe_ratio,
                "sortino": result.sortino_ratio,
                "maxdd": result.max_drawdown_pct,
                "winrate": result.win_rate_pct,
                "pf": result.profit_factor,
                "expectancy": result.expectancy,
                "costs": result.total_costs,
            })
            print(f"OK — {result.total_trades} trades, "
                  f"Sharpe {result.sharpe_ratio:+.3f}, "
                  f"WR {result.win_rate_pct:.1f}%")
        else:
            results.append(None)
            print("ERREUR")

    # Tableau comparatif
    print(format_table(results))

    # Details par strategie
    print("=" * 70)
    print("  DETAILS PAR STRATEGIE")
    print("=" * 70)

    for i, (filename, tf, period, short_name) in enumerate(STRATEGIES):
        r = results[i]
        if r is None:
            continue
        print(f"\n  --- {short_name} ({tf}, {period}) ---")
        print(f"  Trades    : {r['trades']}")
        print(f"  Return    : {r['return']:+.2f}%")
        print(f"  CAGR est. : {r['return'] / (5 if period == '5y' else 2):+.1f}%/an")
        print(f"  Sharpe    : {r['sharpe']:+.3f}")
        print(f"  Sortino   : {r['sortino']:+.3f}")
        print(f"  Max DD    : {r['maxdd']:.2f}%")
        print(f"  Win Rate  : {r['winrate']:.1f}%")
        print(f"  Profit F  : {r['pf']:.3f}")
        print(f"  Expectancy: {r['expectancy']:+.4f}")
        print(f"  Couts tot : ${r['costs']:.2f}")


if __name__ == "__main__":
    main()
