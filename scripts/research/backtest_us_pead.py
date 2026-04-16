#!/usr/bin/env python3
"""T1-D — US Post-Earnings Announcement Drift (PEAD).

Hypothese: les stocks qui surprennent positivement continuent de surperformer
20-60j apres. Effet documente academiquement (Bernard-Thomas 1989 a Beyer-Wu 2024).

Methodologie:
  - Univers : top 30 SP500 liquides (AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA,
    JPM, V, MA, UNH, HD, PG, JNJ, LLY, ABBV, WMT, XOM, CVX, COST, BAC, ORCL,
    AVGO, CSCO, ADBE, CRM, NFLX, PEP, KO, DIS)
  - Earnings data : yfinance Ticker.earnings_dates (surprises historiques)
  - Signal : si Surprise(%) > 5% ET gap up > 1% day+1 open
  - Entry : day+1 open
  - Exit : 20 jours apres OU TP 8% OU SL 3%
  - Cost : 3 bps RT (Alpaca gratuit)

Output: daily PnL aggregate, scorecard marginal.
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
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"
EARNINGS_CACHE = ROOT / "data" / "us_research" / "earnings_history.parquet"
EARNINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "MA",
    "UNH", "HD", "PG", "JNJ", "LLY", "ABBV", "WMT", "XOM", "CVX", "COST",
    "BAC", "ORCL", "AVGO", "CSCO", "ADBE", "CRM", "NFLX", "PEP", "KO", "DIS",
]

CAPITAL_PER_TRADE = 2_000.0
COST_BPS_RT = 3  # 3 bps Alpaca
COST_RT = COST_BPS_RT / 10_000
TP_PCT = 0.08
SL_PCT = 0.03
HOLDING_DAYS = 20
SURPRISE_THRESHOLD = 0.05
GAP_UP_THRESHOLD = 0.01


def get_earnings_and_prices():
    """Download earnings + daily prices for each ticker. Cache result."""
    if EARNINGS_CACHE.exists():
        print(f"Loading cached earnings from {EARNINGS_CACHE}")
        earnings = pd.read_parquet(EARNINGS_CACHE)
        return earnings

    import yfinance as yf
    rows = []
    for i, sym in enumerate(UNIVERSE):
        print(f"  [{i+1}/{len(UNIVERSE)}] {sym} earnings...")
        try:
            t = yf.Ticker(sym)
            e = t.earnings_dates
            if e is None or e.empty:
                continue
            e = e.reset_index()
            e.columns = [c if not hasattr(c, "strip") else c.strip() for c in e.columns]
            e["symbol"] = sym
            rows.append(e)
            time.sleep(0.2)
        except Exception as ex:
            print(f"    ERR: {ex}")

    if not rows:
        raise RuntimeError("No earnings data downloaded")
    df = pd.concat(rows, ignore_index=True)
    df.to_parquet(EARNINGS_CACHE)
    print(f"Saved earnings to {EARNINGS_CACHE}")
    return df


def get_prices(symbols: list, start: str = "2018-01-01") -> dict:
    """Download daily prices once per symbol, cache in dict."""
    import yfinance as yf
    prices = {}
    for i, sym in enumerate(symbols):
        print(f"  [{i+1}/{len(symbols)}] {sym} prices...")
        try:
            df = yf.download(sym, start=start, progress=False, auto_adjust=True)
            if not df.empty:
                # Handle multi-level columns from yfinance
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                prices[sym] = df
            time.sleep(0.2)
        except Exception as ex:
            print(f"    ERR: {ex}")
    return prices


def backtest_pead(earnings: pd.DataFrame, prices: dict) -> pd.Series:
    """Generate daily PnL aggregate for PEAD signal across all symbols."""
    earnings_date_col = "Earnings Date"
    surprise_col = "Surprise(%)"

    # Normalize column names and date types
    if earnings_date_col not in earnings.columns:
        # Try common alternatives
        for c in earnings.columns:
            if "Date" in str(c):
                earnings_date_col = c
                break
    # Filter: qualifying earnings
    earnings["earnings_date_norm"] = pd.to_datetime(
        earnings[earnings_date_col], utc=True, errors="coerce"
    ).dt.tz_localize(None).dt.normalize()
    qualifying = earnings[
        (earnings[surprise_col].notna())
        & (earnings[surprise_col] >= SURPRISE_THRESHOLD * 100)
    ].copy()
    print(f"  {len(qualifying)} qualifying earnings (surprise >= {SURPRISE_THRESHOLD*100}%)")

    trades = []
    for _, row in qualifying.iterrows():
        sym = row["symbol"]
        ed = row["earnings_date_norm"]
        if pd.isna(ed):
            continue
        if sym not in prices:
            continue
        px = prices[sym]
        # Entry day = first trading day AFTER earnings
        future_dates = px.index[px.index > ed]
        if len(future_dates) < 2:
            continue
        entry_date = future_dates[0]
        entry_row = px.loc[entry_date]
        # Gap up check: entry day open vs prev close
        prev_close_rows = px.loc[px.index < entry_date]
        if prev_close_rows.empty:
            continue
        prev_close = prev_close_rows["Close"].iloc[-1]
        gap = (entry_row["Open"] - prev_close) / prev_close
        if gap < GAP_UP_THRESHOLD:
            continue
        entry_price = entry_row["Open"]
        # Exit logic: TP / SL / holding days
        exit_price = None
        exit_date = None
        for dt in future_dates[1: HOLDING_DAYS + 1]:
            bar = px.loc[dt]
            hi = bar["High"]
            lo = bar["Low"]
            if hi / entry_price - 1 >= TP_PCT:
                exit_price = entry_price * (1 + TP_PCT)
                exit_date = dt
                break
            if 1 - lo / entry_price >= SL_PCT:
                exit_price = entry_price * (1 - SL_PCT)
                exit_date = dt
                break
        if exit_price is None:
            exit_date = future_dates[min(HOLDING_DAYS, len(future_dates) - 1)]
            exit_price = px.loc[exit_date, "Close"]

        ret = exit_price / entry_price - 1
        pnl_dollars = ret * CAPITAL_PER_TRADE - COST_RT * CAPITAL_PER_TRADE
        trades.append({
            "symbol": sym, "earnings_date": ed, "entry_date": entry_date,
            "exit_date": exit_date, "entry_price": entry_price,
            "exit_price": exit_price, "return": ret, "pnl_dollars": pnl_dollars,
            "gap": gap, "surprise": row[surprise_col],
        })

    if not trades:
        raise RuntimeError("No trades generated")
    trades_df = pd.DataFrame(trades)
    print(f"  {len(trades_df)} trades generated")

    # Aggregate daily PnL: PnL assigned to exit_date
    daily_pnl = trades_df.groupby("exit_date")["pnl_dollars"].sum().sort_index()
    # Reindex on calendar starting from first trade date
    all_dates = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="B")
    daily_pnl = daily_pnl.reindex(all_dates).fillna(0)
    daily_pnl.index = daily_pnl.index.normalize()
    return daily_pnl, trades_df


def main():
    print("=== T1-D : US PEAD (Post-Earnings Announcement Drift) ===\n")
    print(f"Universe: {len(UNIVERSE)} tickers")

    # 1. Download / load earnings
    earnings = get_earnings_and_prices()
    print(f"Earnings records: {len(earnings)}")

    # 2. Download prices (cached in memory only)
    print("\nDownloading prices...")
    prices = get_prices(UNIVERSE)
    print(f"Prices loaded for {len(prices)}/{len(UNIVERSE)} symbols")

    # 3. Run backtest
    print("\nRunning PEAD backtest...")
    daily_pnl, trades_df = backtest_pead(earnings, prices)
    print(f"\nStats:")
    total = daily_pnl.sum()
    wins = (trades_df["return"] > 0).sum()
    wr = wins / len(trades_df)
    avg_ret = trades_df["return"].mean()
    print(f"  Trades: {len(trades_df)}")
    print(f"  Win rate: {wr:.1%}")
    print(f"  Avg return per trade: {avg_ret:+.2%}")
    print(f"  Total PnL: ${total:+,.0f}")

    # Save trades
    trades_path = ROOT / "data" / "us_research" / "pead_trades.parquet"
    trades_df.to_parquet(trades_path, index=False)

    # 4. Score
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    daily_pnl.name = "us_pead"
    print(f"\nScoring vs baseline ({baseline.shape[1]} strats)...")
    try:
        sc = score_candidate("us_pead", daily_pnl, baseline, 10_000.0, 1.0)
        print(f"  [{sc.verdict}] score={sc.marginal_score:+.3f} "
              f"dSharpe={sc.delta_sharpe:+.3f} dMaxDD={sc.delta_maxdd:+.2f}pp "
              f"corr={sc.corr_to_portfolio:+.2f}")
    except Exception as e:
        print(f"  SCORE ERR: {e}")
        sc = None

    # 5. Report
    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    if sc:
        (JSON_OUT_DIR / "T1-02_scorecards.json").write_text(
            json.dumps([sc.to_dict()], indent=2, default=str))

    md = [
        "# T1-D — US PEAD backtest",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Univers** : {len(UNIVERSE)} tickers SP500 (top liquidity)",
        f"**Methodologie** : long day+1 open si Surprise >= {SURPRISE_THRESHOLD*100}% ET gap up >= {GAP_UP_THRESHOLD*100}%",
        f"**Exit** : {HOLDING_DAYS}D hold OR TP {TP_PCT*100}% OR SL {SL_PCT*100}%",
        f"**Sizing** : ${CAPITAL_PER_TRADE}/trade, cost {COST_BPS_RT} bps RT",
        "",
        "## Results",
        "",
        f"- Trades : {len(trades_df)}",
        f"- Win rate : {wr:.1%}",
        f"- Avg return : {avg_ret:+.2%}",
        f"- Total PnL : ${total:+,.0f}",
        f"- Period : {daily_pnl.index.min().date()} -> {daily_pnl.index.max().date()}",
        "",
    ]
    if sc:
        md += [
            "## Scorecard",
            "",
            f"- Verdict : **{sc.verdict}**",
            f"- Marginal score : {sc.marginal_score:+.3f}",
            f"- dSharpe : {sc.delta_sharpe:+.3f}",
            f"- dCAGR : {sc.delta_cagr:+.2f}%",
            f"- dMaxDD : {sc.delta_maxdd:+.2f}pp",
            f"- Corr to portfolio : {sc.corr_to_portfolio:+.2f}",
            f"- Tail overlap : {sc.tail_overlap:.0%}",
            f"- Penalties : {', '.join(sc.penalties) if sc.penalties else '-'}",
        ]
    (MD_OUT_DIR / "T1-02_us_pead.md").write_text("\n".join(md), encoding="utf-8")
    print("\nReports OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
