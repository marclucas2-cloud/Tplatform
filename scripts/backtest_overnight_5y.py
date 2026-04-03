"""
Backtest Overnight 5 ANS — SPY + Sector ETFs (daily data, ZERO filtre).

Strategies:
  1. Overnight SPY: buy close, sell open next day
  2. Overnight Sector Winner: buy strongest sector ETF vs SPY at close, sell open next day

Data: yfinance daily 5y (2021-2026)
Costs: $0.005/share (~$0.10 per trade) + 0.05% slippage round-trip
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# -------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------
TICKERS_SECTOR = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE"]
TICKERS_ALL = ["SPY"] + TICKERS_SECTOR
PERIOD = "5y"
INTERVAL = "1d"

# Costs
COMMISSION_PER_SHARE = 0.005
ESTIMATED_SHARES_PER_TRADE = 20  # ~$500/share * 20 = $10K notional
COMMISSION_PER_TRADE = COMMISSION_PER_SHARE * ESTIMATED_SHARES_PER_TRADE  # $0.10
SLIPPAGE_PCT = 0.0005  # 0.05% round-trip (0.025% each way)

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "session_20260326"

# -------------------------------------------------------------------------
# Download data
# -------------------------------------------------------------------------
def download_data():
    """Download daily data for all tickers."""
    print(f"Downloading {TICKERS_ALL} - period={PERIOD}, interval={INTERVAL}")
    data = yf.download(TICKERS_ALL, period=PERIOD, interval=INTERVAL, group_by="ticker")
    print(f"Downloaded {len(data)} rows")
    return data


def extract_ohlc(data, ticker):
    """Extract Open/Close for a ticker from multi-ticker download."""
    if len(TICKERS_ALL) == 1:
        df = data[["Open", "Close"]].copy()
    else:
        df = data[ticker][["Open", "Close"]].copy()
    df.columns = ["Open", "Close"]
    df.dropna(inplace=True)
    return df


# -------------------------------------------------------------------------
# Strategy 1: Overnight SPY
# -------------------------------------------------------------------------
def backtest_overnight_spy(data):
    """Buy SPY at close, sell at next day open."""
    spy = extract_ohlc(data, "SPY")

    trades = []
    for i in range(len(spy) - 1):
        close_today = spy.iloc[i]["Close"]
        open_next = spy.iloc[i + 1]["Open"]

        # Raw return
        raw_ret = (open_next - close_today) / close_today

        # Net return after costs
        # Commission: $0.10 buy + $0.10 sell = $0.20 on ~$10K = 0.002%
        # Slippage: 0.05% round-trip
        cost_pct = (2 * COMMISSION_PER_TRADE / (close_today * ESTIMATED_SHARES_PER_TRADE)) + SLIPPAGE_PCT
        net_ret = raw_ret - cost_pct

        trades.append({
            "date": str(spy.index[i].date()),
            "close": round(float(close_today), 2),
            "open_next": round(float(open_next), 2),
            "raw_return": round(float(raw_ret), 6),
            "net_return": round(float(net_ret), 6),
        })

    return trades


# -------------------------------------------------------------------------
# Strategy 2: Overnight Sector Winner
# -------------------------------------------------------------------------
def backtest_overnight_sector(data):
    """Buy the strongest sector ETF vs SPY at close, sell at next open."""
    spy = extract_ohlc(data, "SPY")
    sectors = {}
    for t in TICKERS_SECTOR:
        sectors[t] = extract_ohlc(data, t)

    # Align all indices
    common_idx = spy.index.copy()
    for t in TICKERS_SECTOR:
        common_idx = common_idx.intersection(sectors[t].index)

    spy = spy.loc[common_idx]
    for t in TICKERS_SECTOR:
        sectors[t] = sectors[t].loc[common_idx]

    # Calculate relative strength (5-day trailing return vs SPY)
    lookback = 5
    trades = []

    for i in range(lookback, len(common_idx) - 1):
        # Pick the sector ETF with highest 5-day relative return vs SPY
        best_ticker = None
        best_rel_ret = -999

        spy_ret_5d = (spy.iloc[i]["Close"] - spy.iloc[i - lookback]["Close"]) / spy.iloc[i - lookback]["Close"]

        for t in TICKERS_SECTOR:
            sec_ret_5d = (sectors[t].iloc[i]["Close"] - sectors[t].iloc[i - lookback]["Close"]) / sectors[t].iloc[i - lookback]["Close"]
            rel_ret = sec_ret_5d - spy_ret_5d
            if rel_ret > best_rel_ret:
                best_rel_ret = rel_ret
                best_ticker = t

        # Trade: buy best sector at close, sell at next open
        close_today = sectors[best_ticker].iloc[i]["Close"]
        open_next = sectors[best_ticker].iloc[i + 1]["Open"]

        raw_ret = (open_next - close_today) / close_today
        cost_pct = (2 * COMMISSION_PER_TRADE / (close_today * ESTIMATED_SHARES_PER_TRADE)) + SLIPPAGE_PCT
        net_ret = raw_ret - cost_pct

        trades.append({
            "date": str(common_idx[i].date()),
            "ticker": best_ticker,
            "rel_strength_5d": round(float(best_rel_ret), 6),
            "close": round(float(close_today), 2),
            "open_next": round(float(open_next), 2),
            "raw_return": round(float(raw_ret), 6),
            "net_return": round(float(net_ret), 6),
        })

    return trades


# -------------------------------------------------------------------------
# Metrics
# -------------------------------------------------------------------------
def compute_metrics(trades, label):
    """Compute Sharpe, WR, PF, Max DD from trade list."""
    if not trades:
        return {"strategy": label, "error": "no trades"}

    returns = np.array([t["net_return"] for t in trades])
    n = len(returns)

    # Win rate
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    wr = len(wins) / n if n > 0 else 0

    # Profit factor
    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe (annualized, ~252 trading days)
    mean_ret = returns.mean()
    std_ret = returns.std(ddof=1) if n > 1 else 1e-9
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    # Max drawdown (cumulative equity curve)
    cum = (1 + returns).cumprod()
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    max_dd = float(dd.min())

    # Cumulative return
    cum_ret = float(cum[-1] - 1)

    # Average return per trade
    avg_ret = float(mean_ret)

    # CAGR (approx: n trades over ~252 trades/year)
    years = n / 252
    cagr = float(cum[-1] ** (1 / years) - 1) if years > 0 and cum[-1] > 0 else 0

    results = {
        "strategy": label,
        "n_trades": n,
        "years": round(years, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(wr, 4),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 4),
        "cumulative_return": round(cum_ret, 4),
        "cagr": round(cagr, 4),
        "avg_return_per_trade": round(avg_ret, 6),
        "std_return_per_trade": round(float(std_ret), 6),
        "total_gross_profit": round(float(gross_profit), 4),
        "total_gross_loss": round(float(-gross_loss), 4),
        "best_trade": round(float(returns.max()), 6),
        "worst_trade": round(float(returns.min()), 6),
    }

    return results


def print_results(metrics):
    """Pretty print metrics."""
    print(f"\n{'='*60}")
    print(f"  {metrics['strategy']}")
    print(f"{'='*60}")
    print(f"  Trades     : {metrics.get('n_trades', 'N/A')}")
    print(f"  Years      : {metrics.get('years', 'N/A')}")
    print(f"  Sharpe     : {metrics.get('sharpe', 'N/A')}")
    print(f"  Win Rate   : {metrics.get('win_rate', 0):.1%}")
    print(f"  Profit Fac : {metrics.get('profit_factor', 'N/A')}")
    print(f"  Max DD     : {metrics.get('max_drawdown', 0):.2%}")
    print(f"  Cumul Ret  : {metrics.get('cumulative_return', 0):.2%}")
    print(f"  CAGR       : {metrics.get('cagr', 0):.2%}")
    print(f"  Avg Ret    : {metrics.get('avg_return_per_trade', 0):.4%}")
    print(f"  Best Trade : {metrics.get('best_trade', 0):.4%}")
    print(f"  Worst Trade: {metrics.get('worst_trade', 0):.4%}")


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  OVERNIGHT BACKTEST - 5 ANS DAILY - ZERO FILTRE")
    print("=" * 60)
    print(f"  Costs: ${COMMISSION_PER_TRADE:.2f}/trade commission + {SLIPPAGE_PCT:.2%} slippage")
    print()

    data = download_data()

    # Strategy 1: Overnight SPY
    print("\n[1/2] Backtesting Overnight SPY...")
    spy_trades = backtest_overnight_spy(data)
    spy_metrics = compute_metrics(spy_trades, "Overnight SPY (buy close, sell open)")
    print_results(spy_metrics)

    # Strategy 2: Overnight Sector Winner
    print("\n[2/2] Backtesting Overnight Sector Winner...")
    sector_trades = backtest_overnight_sector(data)
    sector_metrics = compute_metrics(sector_trades, "Overnight Sector Winner (best 5d rel strength)")
    print_results(sector_metrics)

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "generated_at": datetime.now().isoformat(),
        "period": PERIOD,
        "interval": INTERVAL,
        "costs": {
            "commission_per_trade_usd": COMMISSION_PER_TRADE,
            "slippage_roundtrip_pct": SLIPPAGE_PCT,
        },
        "strategies": [spy_metrics, sector_metrics],
        "verdict": {
            "overnight_spy": "DEPLOY" if spy_metrics.get("sharpe", 0) >= 0.5 and spy_metrics.get("profit_factor", 0) >= 1.1 else "REJECT",
            "overnight_sector": "DEPLOY" if sector_metrics.get("sharpe", 0) >= 0.5 and sector_metrics.get("profit_factor", 0) >= 1.1 else "REJECT",
        },
    }

    out_path = OUTPUT_DIR / "overnight_5y_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")

    # Also save trade-level CSVs
    pd.DataFrame(spy_trades).to_csv(OUTPUT_DIR / "trades_overnight_5y_spy.csv", index=False)
    pd.DataFrame(sector_trades).to_csv(OUTPUT_DIR / "trades_overnight_5y_sector.csv", index=False)
    print(f"Trade CSVs saved to {OUTPUT_DIR}")

    print("\n" + "=" * 60)
    print("  VERDICT")
    print("=" * 60)
    for k, v in results["verdict"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
