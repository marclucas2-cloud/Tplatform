"""
Métriques de performance pour évaluer les stratégies.
"""
import pandas as pd
import numpy as np
from typing import Optional


def calculate_metrics(trades: pd.DataFrame, initial_capital: float = 100_000) -> dict:
    """
    Calcule toutes les métriques à partir d'un DataFrame de trades.

    Colonnes attendues dans trades :
        ticker, date, direction, entry_price, exit_price, shares,
        pnl, commission, entry_time, exit_time
    """
    if trades.empty:
        return _empty_metrics()

    total_pnl = trades["pnl"].sum()
    total_commission = trades["commission"].sum()
    net_pnl = total_pnl - total_commission

    winners = trades[trades["pnl"] > 0]
    losers = trades[trades["pnl"] <= 0]

    win_rate = len(winners) / len(trades) * 100 if len(trades) > 0 else 0
    avg_winner = winners["pnl"].mean() if len(winners) > 0 else 0
    avg_loser = losers["pnl"].mean() if len(losers) > 0 else 0

    # Profit factor
    gross_profit = winners["pnl"].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers["pnl"].sum()) if len(losers) > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    equity = _build_equity_curve(trades, initial_capital)
    max_drawdown = _max_drawdown(equity)

    # Sharpe ratio (annualisé, ~252 trading days)
    daily_returns = equity.pct_change().dropna()
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if daily_returns.std() > 0
        else 0
    )

    # Total return
    total_return_pct = (net_pnl / initial_capital) * 100

    # Par jour de semaine
    trades_copy = trades.copy()
    trades_copy["weekday"] = pd.to_datetime(trades_copy["date"]).dt.day_name()
    by_weekday = trades_copy.groupby("weekday")["pnl"].agg(["sum", "count", "mean"])

    # Par ticker
    by_ticker = trades.groupby("ticker")["pnl"].agg(["sum", "count", "mean"])

    # Trading days
    n_days = trades["date"].nunique()
    n_trades = len(trades)

    return {
        "total_return_pct": round(total_return_pct, 2),
        "net_pnl": round(net_pnl, 2),
        "gross_pnl": round(total_pnl, 2),
        "total_commission": round(total_commission, 2),
        "n_trades": n_trades,
        "n_trading_days": n_days,
        "trades_per_day": round(n_trades / max(n_days, 1), 1),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "best_trade": round(trades["pnl"].max(), 2),
        "worst_trade": round(trades["pnl"].min(), 2),
        "avg_rr_ratio": round(abs(avg_winner / avg_loser), 2) if avg_loser != 0 else 0,
        "by_weekday": by_weekday.to_dict(),
        "by_ticker": by_ticker.to_dict(),
        "equity_curve": equity,
    }


def _build_equity_curve(trades: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Construit la courbe d'equity journalière."""
    daily_pnl = trades.groupby("date")["pnl"].sum() - trades.groupby("date")["commission"].sum()
    daily_pnl = daily_pnl.sort_index()
    equity = initial_capital + daily_pnl.cumsum()
    return equity


def _max_drawdown(equity: pd.Series) -> float:
    """Calcule le max drawdown en %."""
    if equity.empty:
        return 0
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak * 100
    return abs(drawdown.min())


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0,
        "net_pnl": 0,
        "gross_pnl": 0,
        "total_commission": 0,
        "n_trades": 0,
        "n_trading_days": 0,
        "trades_per_day": 0,
        "win_rate": 0,
        "profit_factor": 0,
        "sharpe_ratio": 0,
        "max_drawdown_pct": 0,
        "avg_winner": 0,
        "avg_loser": 0,
        "best_trade": 0,
        "worst_trade": 0,
        "avg_rr_ratio": 0,
        "by_weekday": {},
        "by_ticker": {},
        "equity_curve": pd.Series(dtype=float),
    }


def print_metrics(name: str, metrics: dict):
    """Affiche un résumé lisible."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Total Return:     {metrics['total_return_pct']:>8.2f}%")
    print(f"  Net P&L:          ${metrics['net_pnl']:>10,.2f}")
    print(f"  Trades:           {metrics['n_trades']:>8d}  ({metrics['trades_per_day']:.1f}/day)")
    print(f"  Win Rate:         {metrics['win_rate']:>8.1f}%")
    print(f"  Profit Factor:    {metrics['profit_factor']:>8.2f}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:>8.2f}")
    print(f"  Max Drawdown:     {metrics['max_drawdown_pct']:>8.2f}%")
    print(f"  Avg Winner:       ${metrics['avg_winner']:>10,.2f}")
    print(f"  Avg Loser:        ${metrics['avg_loser']:>10,.2f}")
    print(f"  Best Trade:       ${metrics['best_trade']:>10,.2f}")
    print(f"  Worst Trade:      ${metrics['worst_trade']:>10,.2f}")
    print(f"  R:R Ratio:        {metrics['avg_rr_ratio']:>8.2f}")
    print(f"  Commissions:      ${metrics['total_commission']:>10,.2f}")
    print(f"{'='*60}")
