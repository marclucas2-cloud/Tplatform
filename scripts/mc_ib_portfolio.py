#!/usr/bin/env python3
"""Monte Carlo sur V2 portefeuille IB — distributions CAGR / DD / Sharpe.

Methode:
  1. Extrait les daily PnL du backtest 10Y V2 (+slippage, MCL fix)
  2. Bootstrap resample 10,000 trajectoires de meme longueur (N=2830 bars)
     - i.i.d. simple: tirage avec remise bar par bar
     - block bootstrap (taille 20): preserve l'autocorrelation/clustering vol
  3. Calcule pour chaque trajectoire: CAGR, Sharpe, Max DD, worst month, P(ruin)
  4. Percentiles P5/P10/P25/P50/P75/P90/P95

Ce MC ne genere pas de NEW scenarios de marche. Il reshuffle les rendements
realises. Donc il estime: "si l'ordre des jours avait ete different, quelle
trajectoire de DD aurait-on pu avoir ?" C'est la vraie question pour
calibrer le capital psychologique.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
INITIAL_EQUITY = 10_000.0


def load_v2_trades():
    path = ROOT / "reports" / "research" / "ib_portfolio_10y_v2_slip_trades.csv"
    if not path.exists():
        raise FileNotFoundError(f"Run backtest_ib_portfolio_10y.py first ({path})")
    df = pd.read_csv(path)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df


def build_daily_pnl(df_trades, start, end):
    """Compute daily portfolio PnL from trade list."""
    idx = pd.date_range(start, end, freq="B")  # business days
    daily = pd.Series(0.0, index=idx)
    for _, t in df_trades.iterrows():
        d = t["exit_date"].normalize()
        if d in daily.index:
            daily.loc[d] += t["pnl"]
        else:
            # snap to nearest business day
            pos = daily.index.get_indexer([d], method="nearest")[0]
            if 0 <= pos < len(daily):
                daily.iloc[pos] += t["pnl"]
    return daily


def simulate_path(pnl_sequence: np.ndarray, initial: float = INITIAL_EQUITY) -> dict:
    """Given an array of daily PnL, compute trajectory metrics."""
    eq = initial + np.cumsum(pnl_sequence)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min()
    final = eq[-1]
    total_return = final / initial - 1
    years = len(pnl_sequence) / 252.0
    cagr = (final / initial) ** (1 / years) - 1 if final > 0 else -1.0
    sharpe = (pnl_sequence.mean() / pnl_sequence.std() * np.sqrt(252)
              if pnl_sequence.std() > 0 else 0)
    # Longest drawdown duration (days)
    in_dd = dd < -0.001
    longest_dd = 0
    current = 0
    for flag in in_dd:
        if flag:
            current += 1
            longest_dd = max(longest_dd, current)
        else:
            current = 0
    # Worst 30-day window
    if len(pnl_sequence) >= 30:
        rolling30 = pd.Series(pnl_sequence).rolling(30).sum()
        worst30 = rolling30.min()
    else:
        worst30 = pnl_sequence.sum()
    return {
        "final": final,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "longest_dd_days": longest_dd,
        "worst30": worst30,
        "ruined": final <= initial * 0.5,  # ruin if lose > 50% of capital
    }


def bootstrap_iid(daily_pnl: np.ndarray, n_sims: int = 10000, seed: int = 42) -> pd.DataFrame:
    """i.i.d. bootstrap: sample bar-by-bar with replacement."""
    rng = np.random.default_rng(seed)
    n = len(daily_pnl)
    results = []
    for s in range(n_sims):
        idx = rng.integers(0, n, size=n)
        path = daily_pnl[idx]
        results.append(simulate_path(path))
    return pd.DataFrame(results)


def bootstrap_block(daily_pnl: np.ndarray, block_size: int = 20,
                    n_sims: int = 10000, seed: int = 43) -> pd.DataFrame:
    """Block bootstrap: preserves short-range autocorrelation (vol clustering)."""
    rng = np.random.default_rng(seed)
    n = len(daily_pnl)
    n_blocks = int(np.ceil(n / block_size))
    results = []
    for s in range(n_sims):
        path = []
        for _ in range(n_blocks):
            start_idx = rng.integers(0, n - block_size + 1)
            path.extend(daily_pnl[start_idx:start_idx + block_size])
        path = np.array(path[:n])
        results.append(simulate_path(path))
    return pd.DataFrame(results)


def percentiles_report(df: pd.DataFrame, label: str):
    print(f"\n{'='*78}")
    print(f"  {label}  (n={len(df)} simulations)")
    print(f"{'='*78}")
    pcts = [5, 10, 25, 50, 75, 90, 95]
    metrics = [
        ("CAGR",         "cagr",          lambda v: f"{v*100:>7.1f}%"),
        ("Final equity", "final",         lambda v: f"${v:>10,.0f}"),
        ("Max DD",       "max_dd",        lambda v: f"{v*100:>7.1f}%"),
        ("Longest DD",   "longest_dd_days", lambda v: f"{int(v):>5d}d"),
        ("Sharpe",       "sharpe",        lambda v: f"{v:>7.2f}"),
        ("Worst 30d",    "worst30",       lambda v: f"${v:>+8,.0f}"),
    ]
    header = f"{'Metric':<14s}" + "".join(f"{'P'+str(p):>10s}" for p in pcts)
    print(header)
    print("-" * len(header))
    for name, col, fmt in metrics:
        vals = np.percentile(df[col], pcts)
        line = f"{name:<14s}" + "".join(f"{fmt(v).strip():>10s}" for v in vals)
        print(line)

    print()
    print(f"Mean CAGR:       {df['cagr'].mean()*100:.2f}%")
    print(f"Mean Max DD:     {df['max_dd'].mean()*100:.2f}%")
    print(f"P(CAGR > 10%):   {(df['cagr'] > 0.10).mean()*100:.1f}%")
    print(f"P(CAGR > 0%):    {(df['cagr'] > 0.0).mean()*100:.1f}%")
    print(f"P(Max DD > -20%): {(df['max_dd'] < -0.20).mean()*100:.1f}%")
    print(f"P(Max DD > -30%): {(df['max_dd'] < -0.30).mean()*100:.1f}%")
    print(f"P(Max DD > -40%): {(df['max_dd'] < -0.40).mean()*100:.1f}%")
    print(f"P(ruined >= -50%): {df['ruined'].mean()*100:.1f}%")


def main():
    print("Loading V2 trades from 10Y backtest...")
    df_trades = load_v2_trades()
    print(f"  {len(df_trades)} trades loaded")
    print(f"  Period: {df_trades['exit_date'].min().date()} -> {df_trades['exit_date'].max().date()}")

    # Daily PnL series
    start = df_trades["entry_date"].min().normalize()
    end = df_trades["exit_date"].max().normalize()
    daily = build_daily_pnl(df_trades, start, end)
    # Filter out completely flat tail (no trades = no PnL movement)
    daily_arr = daily.values
    print(f"  Daily PnL series: {len(daily_arr)} bars, mean=${daily_arr.mean():+.2f}, std=${daily_arr.std():.2f}")
    print(f"  Historical cumulative: ${daily_arr.sum():+,.0f}")

    # Historical trajectory
    hist = simulate_path(daily_arr)
    print(f"\nHistorical trajectory (realized):")
    print(f"  CAGR:        {hist['cagr']*100:.2f}%")
    print(f"  Max DD:      {hist['max_dd']*100:.2f}%")
    print(f"  Sharpe:      {hist['sharpe']:.2f}")
    print(f"  Longest DD:  {hist['longest_dd_days']}d")
    print(f"  Final:       ${hist['final']:,.0f}")

    # Monte Carlo
    print("\nRunning Monte Carlo 10,000 sims (iid + block20)...")
    iid = bootstrap_iid(daily_arr, n_sims=10000)
    block = bootstrap_block(daily_arr, block_size=20, n_sims=10000)

    percentiles_report(iid, "MC i.i.d. bootstrap (daily shuffle)")
    percentiles_report(block, "MC block bootstrap (20d blocks, preserves vol clustering)")

    # Save
    out = ROOT / "reports" / "research"
    iid.to_csv(out / "mc_iid.csv", index=False)
    block.to_csv(out / "mc_block20.csv", index=False)
    print(f"\n[ok] Saved mc_iid.csv and mc_block20.csv to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
