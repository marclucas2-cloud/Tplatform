"""
P1 — Backtest FX strategies (GBP/USD Trend + USD/CHF MR) + FOMC Reaction.
Sauvegarde les resultats dans output/p1_fx_results.json.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))

import numpy as np
import pandas as pd


def _compute_metrics(trades_df: pd.DataFrame, initial_capital: float = 100_000) -> dict:
    """Calcule les metriques standard a partir d'un DataFrame de trades."""
    if trades_df.empty:
        return {
            "n_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
            "sharpe": 0, "max_dd_pct": 0, "profit_factor": 0,
            "avg_win": 0, "avg_loss": 0,
        }

    pnl_col = "net_pnl" if "net_pnl" in trades_df.columns else "pnl"
    pnls = trades_df[pnl_col].values

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    total_pnl = float(pnls.sum())
    n_trades = len(pnls)
    win_rate = len(wins) / n_trades if n_trades > 0 else 0

    avg_win = float(wins.mean()) if len(wins) > 0 else 0
    avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0

    # Sharpe annualise (daily trades -> ~252 trading days)
    if len(pnls) > 1 and pnls.std() > 0:
        sharpe = float((pnls.mean() / pnls.std()) * np.sqrt(252))
    else:
        sharpe = 0.0

    # Max drawdown
    cumulative = initial_capital + np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / peak
    max_dd_pct = float(drawdown.min()) * 100

    # Profit factor
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / n_trades, 2) if n_trades > 0 else 0,
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


def run_gbpusd():
    """Backtest GBP/USD Trend."""
    print("\n" + "=" * 60)
    print("[BACKTEST] GBP/USD Trend Following")
    print("=" * 60)

    import yfinance as yf
    data = yf.download("GBPUSD=X", period="5y", interval="1d", progress=False)
    if data.empty:
        print("  [ERROR] Pas de donnees yfinance pour GBPUSD=X")
        return None, pd.DataFrame()

    # Normaliser les colonnes
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data.columns = [c.lower() for c in data.columns]

    print(f"  Donnees: {len(data)} jours ({data.index[0].date()} -> {data.index[-1].date()})")

    from strategies.forex.gbpusd_trend import GBPUSDTrendStrategy
    strat = GBPUSDTrendStrategy()
    trades = strat.backtest(data)

    metrics = _compute_metrics(trades)
    print(f"  Trades: {metrics['n_trades']}")
    print(f"  Win rate: {metrics['win_rate']*100:.1f}%")
    print(f"  Sharpe: {metrics['sharpe']}")
    print(f"  Total PnL: ${metrics['total_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")

    return metrics, trades


def run_usdchf():
    """Backtest USD/CHF Mean Reversion."""
    print("\n" + "=" * 60)
    print("[BACKTEST] USD/CHF Mean Reversion (Z-Score)")
    print("=" * 60)

    import yfinance as yf
    data = yf.download("USDCHF=X", period="5y", interval="1d", progress=False)
    if data.empty:
        print("  [ERROR] Pas de donnees yfinance pour USDCHF=X")
        return None, pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data.columns = [c.lower() for c in data.columns]

    print(f"  Donnees: {len(data)} jours ({data.index[0].date()} -> {data.index[-1].date()})")

    from strategies.forex.usdchf_mr import USDCHFMeanReversionStrategy
    strat = USDCHFMeanReversionStrategy()
    trades = strat.backtest(data)

    metrics = _compute_metrics(trades)
    print(f"  Trades: {metrics['n_trades']}")
    print(f"  Win rate: {metrics['win_rate']*100:.1f}%")
    print(f"  Sharpe: {metrics['sharpe']}")
    print(f"  Total PnL: ${metrics['total_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")

    return metrics, trades


def run_fomc():
    """Backtest FOMC Reaction."""
    print("\n" + "=" * 60)
    print("[BACKTEST] FOMC Reaction — Next-Day Continuation")
    print("=" * 60)

    import yfinance as yf
    data = yf.download("SPY", period="5y", interval="1d", progress=False)
    if data.empty:
        print("  [ERROR] Pas de donnees yfinance pour SPY")
        return None, pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    print(f"  Donnees: {len(data)} jours ({data.index[0].date()} -> {data.index[-1].date()})")

    from strategies.fomc_reaction import FOMCReactionStrategy
    strat = FOMCReactionStrategy()
    trades = strat.backtest(data)

    metrics = _compute_metrics(trades)
    print(f"  Trades: {metrics['n_trades']}")
    print(f"  Win rate: {metrics['win_rate']*100:.1f}%")
    print(f"  Sharpe: {metrics['sharpe']}")
    print(f"  Total PnL: ${metrics['total_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")

    return metrics, trades


def main():
    results = {}

    # ── GBP/USD ──
    m, t = run_gbpusd()
    if m:
        results["gbpusd_trend"] = m
        if not t.empty:
            out = ROOT / "archive" / "intraday-backtesterV2" / "output"
            out.mkdir(parents=True, exist_ok=True)
            t.to_csv(out / "trades_gbpusd_trend.csv", index=False)

    # ── USD/CHF ──
    m, t = run_usdchf()
    if m:
        results["usdchf_mean_reversion"] = m
        if not t.empty:
            out = ROOT / "archive" / "intraday-backtesterV2" / "output"
            t.to_csv(out / "trades_usdchf_mr.csv", index=False)

    # ── FOMC ──
    m, t = run_fomc()
    if m:
        results["fomc_reaction"] = m
        if not t.empty:
            out = ROOT / "archive" / "intraday-backtesterV2" / "output"
            t.to_csv(out / "trades_fomc_reaction.csv", index=False)

    # ── Sauvegarde ──
    output_path = ROOT / "output" / "p1_fx_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "timestamp": datetime.now().isoformat(),
            "strategies_tested": len(results),
        },
        "results": results,
    }

    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"[DONE] Resultats sauvegardes dans {output_path}")
    print(f"       {len(results)} strategies backtestees")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
