"""
Meta-analyse : détecte les régimes de marché et identifie
quelle stratégie performe le mieux dans chaque régime.

Régimes basés sur :
- VIX level (low <15, medium 15-25, high >25)
- Trend SPY (au-dessus/en dessous SMA20 daily)
- Market breadth (proxy via QQQ/SPY ratio)
"""
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

import config
from data_fetcher import fetch_bars


def detect_regimes(spy_daily: pd.DataFrame, vix_daily: pd.DataFrame = None) -> pd.DataFrame:
    """
    Classifie chaque jour de trading en régime.

    Régimes :
    - BULL_LOW_VOL : SPY > SMA20, VIX < 15
    - BULL_MED_VOL : SPY > SMA20, VIX 15-25
    - BULL_HIGH_VOL : SPY > SMA20, VIX > 25
    - BEAR_LOW_VOL : SPY < SMA20, VIX < 15
    - BEAR_MED_VOL : SPY < SMA20, VIX 15-25
    - BEAR_HIGH_VOL : SPY < SMA20, VIX > 25
    """
    df = spy_daily.copy()
    df["sma20"] = df["close"].rolling(20).mean()
    df["trend"] = np.where(df["close"] > df["sma20"], "BULL", "BEAR")

    # Si on n'a pas le VIX, on utilise la volatilité réalisée comme proxy
    if vix_daily is not None and not vix_daily.empty:
        df["vix"] = vix_daily["close"].reindex(df.index, method="ffill")
    else:
        # Proxy : volatilité réalisée 20 jours annualisée
        df["vix"] = df["close"].pct_change().rolling(20).std() * np.sqrt(252) * 100

    df["vol_regime"] = pd.cut(
        df["vix"],
        bins=[0, 15, 25, 100],
        labels=["LOW_VOL", "MED_VOL", "HIGH_VOL"],
    )

    df["regime"] = df["trend"] + "_" + df["vol_regime"].astype(str)
    return df[["close", "sma20", "trend", "vix", "vol_regime", "regime"]].dropna()


def regime_strategy_matrix(
    regimes: pd.DataFrame,
    all_trades: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Crée une matrice régime × stratégie avec les métriques clés.
    """
    results = []

    for strategy_name, trades in all_trades.items():
        if trades.empty:
            continue

        trades = trades.copy()
        trades["date"] = pd.to_datetime(trades["date"])

        for regime_name, regime_dates in regimes.groupby("regime"):
            regime_date_set = set(regime_dates.index.date if hasattr(regime_dates.index, "date") else regime_dates.index)

            regime_trades = trades[trades["date"].dt.date.isin(regime_date_set)]

            if regime_trades.empty:
                continue

            n = len(regime_trades)
            total_pnl = regime_trades["pnl"].sum()
            win_rate = (regime_trades["pnl"] > 0).mean() * 100
            avg_pnl = regime_trades["pnl"].mean()

            winners = regime_trades[regime_trades["pnl"] > 0]["pnl"]
            losers = regime_trades[regime_trades["pnl"] <= 0]["pnl"]
            profit_factor = (
                winners.sum() / abs(losers.sum())
                if len(losers) > 0 and losers.sum() != 0
                else float("inf")
            )

            results.append({
                "regime": regime_name,
                "strategy": strategy_name,
                "n_trades": n,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(avg_pnl, 2),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
            })

    matrix = pd.DataFrame(results)
    return matrix


def print_regime_analysis(matrix: pd.DataFrame):
    """Affiche la matrice régime × stratégie."""
    if matrix.empty:
        print("No regime data available.")
        return

    print("\n" + "=" * 80)
    print("  REGIME × STRATEGY MATRIX")
    print("=" * 80)

    for regime in sorted(matrix["regime"].unique()):
        regime_data = matrix[matrix["regime"] == regime].sort_values("total_pnl", ascending=False)
        print(f"\n  [{regime}]")
        for _, row in regime_data.iterrows():
            print(f"    {row['strategy']:30s} "
                  f"PnL=${row['total_pnl']:>8,.0f}  "
                  f"WR={row['win_rate']:>5.1f}%  "
                  f"PF={row['profit_factor']:>5.2f}  "
                  f"Trades={row['n_trades']:>4d}")

    # Best strategy per regime
    print("\n  BEST STRATEGY PER REGIME:")
    best = matrix.loc[matrix.groupby("regime")["total_pnl"].idxmax()]
    for _, row in best.iterrows():
        print(f"    {row['regime']:25s} → {row['strategy']}")


if __name__ == "__main__":
    print("Run this after run_backtest.py to analyze regime performance.")
    print("Usage: Import detect_regimes() and regime_strategy_matrix() from your analysis script.")
