#!/usr/bin/env python3
"""T2-D — US cross-sectional mean reversion.

Logique: sur univers 30 SP500, chaque jour :
  - Long top 5 oversold (lowest RSI14)
  - Short top 5 overbought (highest RSI14)
  - Equal weight, daily rebalance
  - Costs: 3 bps RT Alpaca

Output: daily PnL aggregate.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from scripts.research.portfolio_marginal_score import score_candidate  # noqa: E402

BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
CACHE_PATH = ROOT / "data" / "us_research" / "sp500_prices_cache.parquet"
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "MA",
    "UNH", "HD", "PG", "JNJ", "LLY", "ABBV", "WMT", "XOM", "CVX", "COST",
    "BAC", "ORCL", "AVGO", "CSCO", "ADBE", "CRM", "NFLX", "PEP", "KO", "DIS",
]
CAPITAL_PER_LEG = 500.0
COST_BPS = 3
TOP_N = 5
HOLDING_DAYS = 5


def get_prices():
    if CACHE_PATH.exists():
        print(f"Loading cache {CACHE_PATH}")
        return pd.read_parquet(CACHE_PATH)
    import yfinance as yf
    frames = []
    for i, sym in enumerate(UNIVERSE):
        print(f"  [{i+1}/{len(UNIVERSE)}] {sym}...")
        try:
            df = yf.download(sym, start="2018-01-01", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df["symbol"] = sym
            frames.append(df[["Close", "symbol"]])
            time.sleep(0.15)
        except Exception as e:
            print(f"  ERR: {e}")
    all_df = pd.concat(frames)
    pivot = all_df.reset_index().pivot(index="Date", columns="symbol", values="Close")
    pivot.to_parquet(CACHE_PATH)
    print(f"Cached -> {CACHE_PATH}")
    return pivot


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    ru = up.ewm(com=window - 1, adjust=False).mean()
    rd = dn.ewm(com=window - 1, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def main():
    print("=== T2-D : US cross-sectional mean reversion ===\n")
    prices = get_prices()
    print(f"Prices: {prices.shape}, range={prices.index.min().date()} -> {prices.index.max().date()}")

    # RSI per symbol
    rsi_df = prices.apply(rsi, axis=0)
    ret = prices.pct_change()

    # Each day: rank, form L/S baskets, hold HOLDING_DAYS, compute PnL
    # Simplification: compute daily PnL as if rebalancing every HOLDING_DAYS
    daily_pnl = pd.Series(0.0, index=prices.index)

    last_rebalance = None
    positions = {s: 0.0 for s in prices.columns}
    for i, dt in enumerate(prices.index):
        if i < 14:
            continue
        do_reb = (last_rebalance is None) or ((dt - last_rebalance).days >= HOLDING_DAYS)
        if do_reb:
            ranks = rsi_df.loc[dt].dropna().sort_values()
            new_pos = {s: 0.0 for s in prices.columns}
            for s in ranks.head(TOP_N).index:
                new_pos[s] = 1.0
            for s in ranks.tail(TOP_N).index:
                new_pos[s] = -1.0
            reb_cost = sum(abs(new_pos[s] - positions[s]) for s in prices.columns) * CAPITAL_PER_LEG * COST_BPS / 10_000
            positions = new_pos
            last_rebalance = dt
        else:
            reb_cost = 0
        day = sum(positions[s] * ret.loc[dt, s] for s in prices.columns if not pd.isna(ret.loc[dt, s])) * CAPITAL_PER_LEG
        daily_pnl.loc[dt] = day - reb_cost

    daily_pnl.name = "us_cross_sectional_mr"
    total = daily_pnl.sum()
    sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(252) if daily_pnl.std() > 0 else 0
    print(f"Standalone: total=${total:+,.0f}, Sharpe={sharpe:+.2f}")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    sc = score_candidate("us_cross_sectional_mr", daily_pnl, baseline, 10_000.0, 1.0)
    print(f"[{sc.verdict}] score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
          f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")

    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    (JSON_OUT_DIR / "T2-04_scorecards.json").write_text(
        json.dumps([sc.to_dict()], indent=2, default=str))
    md = [
        "# T2-D — US cross-sectional mean reversion",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Univers** : {len(UNIVERSE)} tickers SP500",
        f"**Methodologie** : top {TOP_N} oversold long, top {TOP_N} overbought short (RSI14), "
        f"hold {HOLDING_DAYS}d",
        f"**Data range** : {prices.index.min().date()} -> {prices.index.max().date()} ({len(prices)}d)",
        "",
        "## Results",
        "",
        f"- Total PnL : ${total:+,.0f}",
        f"- Sharpe : {sharpe:+.2f}",
        f"- Verdict : **{sc.verdict}**",
        f"- Score : {sc.marginal_score:+.3f}, dSharpe {sc.delta_sharpe:+.3f}, "
        f"dMaxDD {sc.delta_maxdd:+.2f}pp, corr {sc.corr_to_portfolio:+.2f}",
        f"- Penalties : {', '.join(sc.penalties) if sc.penalties else '-'}",
        "",
    ]
    (MD_OUT_DIR / "T2-04_us_cross_sectional_mr.md").write_text("\n".join(md), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
