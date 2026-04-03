"""
P1 Strategies Runner — 7 strategies couvrant SHORT, EU event-driven,
options proxy, futures proxy, et forex.

Strategies :
1. SHORT-5 : Cross-Asset Risk-Off Confirmation (intraday 5M, data_cache)
2. SHORT-6 : OpEx Short Extension (intraday 5M, data_cache)
3. EU-2    : BCE Rate Decision Drift (daily EU, yfinance/synthetic)
4. OPT-1   : Weekly Put Credit Spread SPY (daily, yfinance)
5. OPT-2   : Earnings IV Crush (daily, yfinance)
6. FUT-1   : ES/NQ Trend Following 1H (1H SPY, yfinance)
7. FX-1    : Carry Trade AUD/JPY (daily, yfinance)

Usage :
    python run_p1_strategies.py
    python run_p1_strategies.py --no-yfinance    # Skip yfinance-dependent strategies
    python run_p1_strategies.py --strategy short5 # Run single strategy

Outputs :
    output/session_20260326/p1_strategies_results.json
    output/session_20260326/trades_p1_*.csv
"""
import sys
import os
import io
import json
import traceback
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project paths
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "strategies" / "eu"))

import pandas as pd
import numpy as np

# ── Output ──
OUTPUT_DIR = SCRIPT_DIR.parent / "output" / "session_20260326"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Validation criteria ──
VALIDATION_CRITERIA = {
    "sharpe_min": 0.5,
    "pf_min": 1.2,
    "trades_min": 15,
    "dd_max": 10.0,
}

# Walk-forward params
IS_DAYS = 60
OOS_DAYS = 30
STEP_DAYS = 30


# ═══════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════

def load_intraday_cache(tickers_filter: list[str] = None) -> dict[str, pd.DataFrame]:
    """Charge les donnees 5Min parquet depuis data_cache/."""
    import config
    cache_dir = Path(config.CACHE_DIR)
    if not cache_dir.exists():
        print(f"  [ERROR] Cache directory not found: {cache_dir}")
        return {}

    parquet_files = sorted(cache_dir.glob("*_5Min_*.parquet"))
    if not parquet_files:
        print("  [ERROR] No 5Min parquet files in cache")
        return {}

    # Group by ticker, take widest date range
    ticker_files = {}
    for f in parquet_files:
        name = f.stem
        parts = name.split("_")
        ticker = parts[0]
        if tickers_filter and ticker not in tickers_filter:
            continue
        try:
            date_range = int(parts[3]) - int(parts[2])
        except (IndexError, ValueError):
            date_range = 0
        if ticker not in ticker_files or date_range > ticker_files[ticker][1]:
            ticker_files[ticker] = (f, date_range)

    data = {}
    for ticker, (fpath, _) in sorted(ticker_files.items()):
        try:
            df = pd.read_parquet(fpath)
            if not df.empty:
                data[ticker] = df
        except Exception as e:
            print(f"  [WARN] {ticker}: {e}")

    total_bars = sum(len(v) for v in data.values())
    print(f"  [CACHE] {len(data)} tickers, {total_bars:,} bars")
    return data


def fetch_yfinance_daily(tickers: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
    """Fetch daily data from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [ERROR] yfinance not installed. pip install yfinance")
        return {}

    data = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, auto_adjust=True)
            if df.empty:
                print(f"  [WARN] {ticker}: no data from yfinance")
                continue

            # Normalize column names to lowercase
            df.columns = [c.lower() for c in df.columns]

            # Ensure required columns exist
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    print(f"  [WARN] {ticker}: missing column {col}")
                    continue

            data[ticker] = df
            print(f"  [YF] {ticker}: {len(df)} daily bars ({df.index[0].date()} -> {df.index[-1].date()})")
        except Exception as e:
            print(f"  [WARN] {ticker}: yfinance error: {e}")

    return data


def fetch_yfinance_1h(ticker: str = "SPY", period: str = "2y") -> pd.DataFrame:
    """Fetch 1H data from yfinance (max ~2 years)."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [ERROR] yfinance not installed")
        return pd.DataFrame()

    try:
        t = yf.Ticker(ticker)
        # yfinance: max 730 days for 1h data, but often only provides ~2 years
        df = t.history(period=period, interval="1h", auto_adjust=True)
        if df.empty:
            print(f"  [WARN] {ticker} 1H: no data")
            return pd.DataFrame()

        df.columns = [c.lower() for c in df.columns]
        print(f"  [YF] {ticker} 1H: {len(df)} bars ({df.index[0]} -> {df.index[-1]})")
        return df
    except Exception as e:
        print(f"  [WARN] {ticker} 1H: {e}")
        return pd.DataFrame()


def generate_synthetic_eu_data() -> dict[str, pd.DataFrame]:
    """Generate synthetic daily EU bank data (BNP, GLE, DBK) for BCE strategy."""
    np.random.seed(42)
    trading_days = pd.bdate_range(
        start=datetime.now() - timedelta(days=5 * 365),
        end=datetime.now(),
        freq="B",
    )

    profiles = {
        "BNP": (55.0, 1.5),   # BNP Paribas
        "GLE": (25.0, 1.8),   # Societe Generale
        "DBK": (12.0, 2.0),   # Deutsche Bank
    }

    data = {}
    for symbol, (init_price, daily_vol_pct) in profiles.items():
        vol = daily_vol_pct / 100
        n = len(trading_days)
        drift = 0.04 / 252  # ~4% annual

        returns = np.random.randn(n) * vol + drift
        # Add fat tails
        for i in range(n):
            if np.random.rand() < 0.03:
                returns[i] *= np.random.choice([2.0, 2.5, 3.0])

        prices = np.zeros(n)
        prices[0] = init_price
        for i in range(1, n):
            prices[i] = prices[i - 1] * (1 + returns[i])

        closes = prices.copy()
        opens = np.zeros(n)
        opens[0] = prices[0]
        for i in range(1, n):
            gap = np.random.randn() * vol * 0.3
            opens[i] = closes[i - 1] * (1 + gap)

        highs = np.maximum(opens, closes) * (1 + abs(np.random.randn(n)) * vol * 0.5)
        lows = np.minimum(opens, closes) * (1 - abs(np.random.randn(n)) * vol * 0.5)
        highs = np.maximum(highs, np.maximum(opens, closes))
        lows = np.minimum(lows, np.minimum(opens, closes))
        volumes = np.random.lognormal(np.log(2_000_000), 0.3, n).astype(int)

        df = pd.DataFrame({
            "open": opens.round(2),
            "high": highs.round(2),
            "low": lows.round(2),
            "close": closes.round(2),
            "volume": volumes,
        }, index=trading_days)

        data[symbol] = df
        print(f"  [SYNTH] {symbol}: {len(df)} daily bars")

    return data


# ═══════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════

def calculate_standalone_metrics(trades_df: pd.DataFrame, initial_capital: float = 100_000) -> dict:
    """Calculate metrics for standalone (non-BaseStrategy) strategies."""
    if trades_df.empty:
        return {
            "total_return_pct": 0, "net_pnl": 0, "gross_pnl": 0,
            "total_commission": 0, "n_trades": 0, "n_trading_days": 0,
            "trades_per_day": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe_ratio": 0, "max_drawdown_pct": 0,
            "avg_winner": 0, "avg_loser": 0,
            "best_trade": 0, "worst_trade": 0,
            "avg_rr_ratio": 0,
        }

    # Use net_pnl if available, otherwise pnl - commission
    if "net_pnl" in trades_df.columns:
        pnl_col = "net_pnl"
    else:
        trades_df = trades_df.copy()
        trades_df["net_pnl"] = trades_df["pnl"] - trades_df.get("commission", 0)
        pnl_col = "net_pnl"

    total_pnl = trades_df["pnl"].sum()
    total_commission = trades_df["commission"].sum() if "commission" in trades_df.columns else 0
    net_pnl = trades_df[pnl_col].sum()

    winners = trades_df[trades_df[pnl_col] > 0]
    losers = trades_df[trades_df[pnl_col] <= 0]

    win_rate = len(winners) / len(trades_df) * 100 if len(trades_df) > 0 else 0
    avg_winner = winners[pnl_col].mean() if len(winners) > 0 else 0
    avg_loser = losers[pnl_col].mean() if len(losers) > 0 else 0

    gross_profit = winners[pnl_col].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers[pnl_col].sum()) if len(losers) > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    daily_pnl = trades_df.groupby("date")[pnl_col].sum()
    daily_pnl = daily_pnl.sort_index()
    equity = initial_capital + daily_pnl.cumsum()

    # Max drawdown
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak * 100
    max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0

    # Sharpe ratio
    daily_returns = equity.pct_change().dropna()
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if len(daily_returns) > 1 and daily_returns.std() > 0
        else 0
    )

    total_return_pct = (net_pnl / initial_capital) * 100
    n_trades = len(trades_df)
    n_days = trades_df["date"].nunique()

    return {
        "total_return_pct": round(total_return_pct, 2),
        "net_pnl": round(float(net_pnl), 2),
        "gross_pnl": round(float(total_pnl), 2),
        "total_commission": round(float(total_commission), 2),
        "n_trades": n_trades,
        "n_trading_days": n_days,
        "trades_per_day": round(n_trades / max(n_days, 1), 1),
        "win_rate": round(float(win_rate), 1),
        "profit_factor": round(float(profit_factor), 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown_pct": round(float(max_dd), 2),
        "avg_winner": round(float(avg_winner), 2),
        "avg_loser": round(float(avg_loser), 2),
        "best_trade": round(float(trades_df[pnl_col].max()), 2),
        "worst_trade": round(float(trades_df[pnl_col].min()), 2),
        "avg_rr_ratio": round(abs(float(avg_winner) / float(avg_loser)), 2) if avg_loser != 0 else 0,
    }


def print_result(name: str, m: dict):
    """Pretty print strategy metrics."""
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    print(f"  Total Return:     {m['total_return_pct']:>8.2f}%")
    print(f"  Net P&L:          ${m['net_pnl']:>10,.2f}")
    print(f"  Trades:           {m['n_trades']:>8d}  ({m['trades_per_day']:.1f}/day)")
    print(f"  Win Rate:         {m['win_rate']:>8.1f}%")
    print(f"  Profit Factor:    {m['profit_factor']:>8.2f}")
    print(f"  Sharpe Ratio:     {m['sharpe_ratio']:>8.2f}")
    print(f"  Max Drawdown:     {m['max_drawdown_pct']:>8.2f}%")
    print(f"  Avg Winner:       ${m['avg_winner']:>10,.2f}")
    print(f"  Avg Loser:        ${m['avg_loser']:>10,.2f}")
    print(f"  Best Trade:       ${m['best_trade']:>10,.2f}")
    print(f"  Worst Trade:      ${m['worst_trade']:>10,.2f}")
    print(f"  R:R Ratio:        {m['avg_rr_ratio']:>8.2f}")
    print(f"{'='*65}")


def evaluate_verdict(m: dict) -> str:
    """Determine WINNER / POTENTIEL / REJETE based on validation criteria."""
    s = m.get("sharpe_ratio", 0)
    pf = m.get("profit_factor", 0)
    n = m.get("n_trades", 0)
    dd = m.get("max_drawdown_pct", 0)
    pnl = m.get("net_pnl", 0)

    if (s >= VALIDATION_CRITERIA["sharpe_min"]
            and pf >= VALIDATION_CRITERIA["pf_min"]
            and n >= VALIDATION_CRITERIA["trades_min"]
            and dd < VALIDATION_CRITERIA["dd_max"]
            and pnl > 0):
        return "WINNER"
    elif s > 0 and pnl > 0:
        return "POTENTIEL"
    else:
        return "REJETE"


# ═══════════════════════════════════════════════════════════════════
# WALK-FORWARD (for intraday strategies only)
# ═══════════════════════════════════════════════════════════════════

def run_walk_forward_intraday(strategy_class, data: dict, all_dates: list) -> dict | None:
    """Walk-forward for strategies using BacktestEngine."""
    import config
    from backtest_engine import BacktestEngine
    from utils.metrics import calculate_metrics

    total_days = len(all_dates)
    if total_days < IS_DAYS + OOS_DAYS:
        return None

    windows = []
    i = 0
    while i + IS_DAYS + OOS_DAYS <= total_days:
        oos_start = all_dates[i + IS_DAYS]
        oos_end = all_dates[min(i + IS_DAYS + OOS_DAYS - 1, total_days - 1)]
        windows.append((oos_start, oos_end))
        i += STEP_DAYS

    if not windows:
        return None

    oos_results = []
    for w_idx, (oos_start, oos_end) in enumerate(windows):
        # Filter data to OOS window
        oos_data = {}
        for ticker, df in data.items():
            mask = (df.index.date >= oos_start) & (df.index.date <= oos_end)
            sub = df[mask]
            if not sub.empty:
                oos_data[ticker] = sub

        if not oos_data:
            continue

        strategy = strategy_class()
        engine = BacktestEngine(strategy, initial_capital=config.INITIAL_CAPITAL)

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        trades = engine.run(oos_data)
        sys.stdout = old_stdout

        metrics = calculate_metrics(trades, config.INITIAL_CAPITAL)

        oos_results.append({
            "window": w_idx + 1,
            "oos_start": str(oos_start),
            "oos_end": str(oos_end),
            "return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe_ratio"],
            "trades": metrics["n_trades"],
        })

        status = "+" if metrics["total_return_pct"] > 0 else "-"
        print(f"    {status} W{w_idx+1}: {oos_start} -> {oos_end} | "
              f"Ret={metrics['total_return_pct']:>6.2f}% | "
              f"Sharpe={metrics['sharpe_ratio']:>5.2f} | "
              f"Trades={metrics['n_trades']:>3d}")

    if not oos_results:
        return None

    profitable = sum(1 for r in oos_results if r["return_pct"] > 0)
    hit_rate = profitable / len(oos_results) * 100
    avg_return = np.mean([r["return_pct"] for r in oos_results])
    avg_sharpe = np.mean([r["sharpe"] for r in oos_results])

    verdict = "VALIDATED" if hit_rate >= 50 and avg_return > 0 else "REJECTED"

    return {
        "hit_rate": round(hit_rate, 0),
        "profitable_windows": profitable,
        "total_windows": len(oos_results),
        "avg_oos_return": round(avg_return, 2),
        "avg_oos_sharpe": round(avg_sharpe, 2),
        "verdict": verdict,
    }


# ═══════════════════════════════════════════════════════════════════
# INDIVIDUAL STRATEGY RUNNERS
# ═══════════════════════════════════════════════════════════════════

def run_short5():
    """SHORT-5: Cross-Asset Risk-Off Confirmation."""
    import config
    from backtest_engine import BacktestEngine
    from utils.metrics import calculate_metrics
    from strategies.cross_asset_riskoff_short import CrossAssetRiskOffShortStrategy

    print("\n[SHORT-5] Cross-Asset Risk-Off Confirmation")
    print("  Loading intraday data for GLD, TLT, TSLA, NVDA, AMD, COIN, MARA...")

    required = ["GLD", "TLT", "TSLA", "NVDA", "AMD", "COIN", "MARA"]
    data = load_intraday_cache(tickers_filter=set(required))

    if not data or "GLD" not in data or "TLT" not in data:
        return {"n_trades": 0, "verdict": "NO_DATA", "error": "Missing GLD/TLT data"}

    strategy = CrossAssetRiskOffShortStrategy()
    engine = BacktestEngine(strategy, initial_capital=config.INITIAL_CAPITAL)
    trades_df = engine.run(data)

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
    print_result(strategy.name, m)

    # Save trades CSV
    csv_path = OUTPUT_DIR / "trades_p1_short5_riskoff.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    verdict = evaluate_verdict(m)

    # Walk-forward
    wf_result = None
    if m["n_trades"] >= 30:
        print(f"  --- Walk-Forward ---")
        all_dates = sorted(set(d for df in data.values() for d in df.index.date))
        wf_result = run_walk_forward_intraday(CrossAssetRiskOffShortStrategy, data, all_dates)
        if wf_result:
            print(f"  WF: {wf_result['profitable_windows']}/{wf_result['total_windows']} "
                  f"({wf_result['hit_rate']:.0f}%) -> {wf_result['verdict']}")
            if verdict == "WINNER" and wf_result["verdict"] == "VALIDATED":
                verdict = "WINNER+WF"

    result = {**m, "verdict": verdict}
    if wf_result:
        result["walk_forward"] = wf_result
    return result


def run_short6():
    """SHORT-6: OpEx Short Extension."""
    import config
    from backtest_engine import BacktestEngine
    from utils.metrics import calculate_metrics
    from strategies.opex_short_only import OpExShortOnlyStrategy

    print("\n[SHORT-6] OpEx Short Extension")
    print("  Loading intraday data for SPY, QQQ, TSLA...")

    required = ["SPY", "QQQ", "TSLA"]
    data = load_intraday_cache(tickers_filter=set(required))

    if not data:
        return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = OpExShortOnlyStrategy()
    engine = BacktestEngine(strategy, initial_capital=config.INITIAL_CAPITAL)
    trades_df = engine.run(data)

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_metrics(trades_df, config.INITIAL_CAPITAL)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_short6_opex.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    verdict = evaluate_verdict(m)

    # Walk-forward
    wf_result = None
    if m["n_trades"] >= 30:
        print(f"  --- Walk-Forward ---")
        all_dates = sorted(set(d for df in data.values() for d in df.index.date))
        wf_result = run_walk_forward_intraday(OpExShortOnlyStrategy, data, all_dates)
        if wf_result:
            print(f"  WF: {wf_result['profitable_windows']}/{wf_result['total_windows']} "
                  f"({wf_result['hit_rate']:.0f}%) -> {wf_result['verdict']}")
            if verdict == "WINNER" and wf_result["verdict"] == "VALIDATED":
                verdict = "WINNER+WF"

    result = {**m, "verdict": verdict}
    if wf_result:
        result["walk_forward"] = wf_result
    return result


def run_eu_bce(use_yfinance: bool = True):
    """EU-2: BCE Rate Decision Drift."""
    from eu_backtest_engine import EUBacktestEngine, EU_INITIAL_CAPITAL
    from strategies.eu.eu_bce_drift import EUBCEDriftStrategy

    print("\n[EU-2] BCE Rate Decision Drift")

    # Try yfinance first, fallback to synthetic
    eu_data = {}
    yf_tickers = {"BNP": "BNP.PA", "GLE": "GLE.PA", "DBK": "DBK.DE"}

    if use_yfinance:
        print("  Fetching EU bank data from yfinance...")
        for local_name, yf_ticker in yf_tickers.items():
            df_data = fetch_yfinance_daily([yf_ticker], period="5y")
            if yf_ticker in df_data:
                eu_data[local_name] = df_data[yf_ticker]

    if not eu_data:
        print("  [FALLBACK] Using synthetic EU data")
        eu_data = generate_synthetic_eu_data()

    if not eu_data:
        return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = EUBCEDriftStrategy()
    engine = EUBacktestEngine(strategy, initial_capital=EU_INITIAL_CAPITAL)
    trades_df = engine.run(eu_data)

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_standalone_metrics(trades_df, EU_INITIAL_CAPITAL)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_eu_bce.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    return {**m, "verdict": evaluate_verdict(m)}


def run_opt1_put_spread(use_yfinance: bool = True):
    """OPT-1: Weekly Put Credit Spread SPY."""
    from strategies.options.put_spread_weekly import PutSpreadWeeklyStrategy

    print("\n[OPT-1] Weekly Put Credit Spread SPY (Proxy)")

    # Get SPY daily data
    spy_data = {}
    if use_yfinance:
        print("  Fetching SPY daily from yfinance...")
        spy_data = fetch_yfinance_daily(["SPY"], period="5y")

    if "SPY" not in spy_data:
        print("  [WARN] No SPY data available. Trying from cache...")
        # Try loading from parquet cache (daily)
        import config
        cache_dir = Path(config.CACHE_DIR)
        daily_files = list(cache_dir.glob("SPY_1Day_*.parquet"))
        if daily_files:
            spy_data["SPY"] = pd.read_parquet(daily_files[0])
        else:
            return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = PutSpreadWeeklyStrategy()
    trades_df = strategy.backtest(spy_data["SPY"])

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_standalone_metrics(trades_df, 100_000)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_opt1_put_spread.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    return {**m, "verdict": evaluate_verdict(m)}


def run_opt2_earnings_iv(use_yfinance: bool = True):
    """OPT-2: Earnings IV Crush."""
    from strategies.options.earnings_iv_crush import EarningsIVCrushStrategy, EARNINGS_TICKERS

    print("\n[OPT-2] Earnings IV Crush (Proxy)")

    stock_data = {}
    if use_yfinance:
        print(f"  Fetching {len(EARNINGS_TICKERS)} mega-cap tickers from yfinance...")
        stock_data = fetch_yfinance_daily(EARNINGS_TICKERS, period="5y")

    if not stock_data:
        return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = EarningsIVCrushStrategy()
    trades_df = strategy.backtest(stock_data)

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_standalone_metrics(trades_df, 100_000)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_opt2_earnings_iv.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    # Extra stats for earnings
    if not trades_df.empty and "exit_reason" in trades_df.columns:
        wins = len(trades_df[trades_df["exit_reason"] == "iv_crush_win"])
        losses = len(trades_df[trades_df["exit_reason"] == "move_exceeded_loss"])
        print(f"  IV Crush Wins: {wins} | Move Exceeded Losses: {losses}")
        if "ticker" in trades_df.columns:
            by_ticker = trades_df.groupby("ticker")["net_pnl"].agg(["sum", "count", "mean"])
            print(f"  By ticker:\n{by_ticker.to_string()}")

    return {**m, "verdict": evaluate_verdict(m)}


def run_fut1_es_trend(use_yfinance: bool = True):
    """FUT-1: ES/NQ Trend Following 1H."""
    from strategies.futures.es_trend_1h import ESTrend1HStrategy

    print("\n[FUT-1] ES/NQ Trend Following 1H (SPY Proxy)")

    spy_1h = pd.DataFrame()
    if use_yfinance:
        print("  Fetching SPY 1H from yfinance...")
        spy_1h = fetch_yfinance_1h("SPY", period="2y")

    if spy_1h.empty:
        # yfinance limits 1h to 730 days, try shorter period
        if use_yfinance:
            spy_1h = fetch_yfinance_1h("SPY", period="730d")

    if spy_1h.empty:
        return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = ESTrend1HStrategy()
    trades_df = strategy.backtest(spy_1h)

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_standalone_metrics(trades_df, 100_000)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_fut1_es_trend.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    return {**m, "verdict": evaluate_verdict(m)}


def run_fx1_carry(use_yfinance: bool = True):
    """FX-1: Carry Trade AUD/JPY."""
    from strategies.forex.audjpy_carry import AUDJPYCarryStrategy

    print("\n[FX-1] Carry Trade AUD/JPY")

    audjpy_data = {}
    if use_yfinance:
        print("  Fetching AUDJPY=X from yfinance...")
        audjpy_data = fetch_yfinance_daily(["AUDJPY=X"], period="5y")

    if "AUDJPY=X" not in audjpy_data:
        return {"n_trades": 0, "verdict": "NO_DATA"}

    strategy = AUDJPYCarryStrategy()
    trades_df = strategy.backtest(audjpy_data["AUDJPY=X"])

    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}

    m = calculate_standalone_metrics(trades_df, 100_000)
    print_result(strategy.name, m)

    csv_path = OUTPUT_DIR / "trades_p1_fx1_carry.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  [CSV] {csv_path}")

    # Extra carry stats
    if not trades_df.empty and "carry_earned" in trades_df.columns:
        total_carry = trades_df["carry_earned"].sum()
        total_price_pnl = trades_df["price_pnl"].sum() if "price_pnl" in trades_df.columns else 0
        avg_hold = trades_df["holding_days"].mean() if "holding_days" in trades_df.columns else 0
        print(f"  Carry earned: ${total_carry:,.2f} | Price P&L: ${total_price_pnl:,.2f}")
        print(f"  Avg holding: {avg_hold:.0f} days")

    return {**m, "verdict": evaluate_verdict(m)}


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="P1 Strategies Runner")
    parser.add_argument("--no-yfinance", action="store_true",
                        help="Skip strategies that require yfinance")
    parser.add_argument("--strategy", type=str, default="all",
                        help="Run single strategy: short5, short6, eu_bce, opt1, opt2, fut1, fx1")
    args = parser.parse_args()

    use_yf = not args.no_yfinance

    print("=" * 80)
    print("  P1 STRATEGIES — 7 New Strategies (SHORT + EU + Options + Futures + FX)")
    print("=" * 80)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  yfinance: {'enabled' if use_yf else 'disabled'}")
    print()

    # Strategy registry
    ALL_RUNNERS = {
        "short5": ("SHORT-5: Cross-Asset Risk-Off", run_short5, False),
        "short6": ("SHORT-6: OpEx Short Extension", run_short6, False),
        "eu_bce": ("EU-2: BCE Rate Decision Drift", lambda: run_eu_bce(use_yf), True),
        "opt1":   ("OPT-1: Put Credit Spread SPY", lambda: run_opt1_put_spread(use_yf), True),
        "opt2":   ("OPT-2: Earnings IV Crush", lambda: run_opt2_earnings_iv(use_yf), True),
        "fut1":   ("FUT-1: ES Trend Following 1H", lambda: run_fut1_es_trend(use_yf), True),
        "fx1":    ("FX-1: Carry Trade AUD/JPY", lambda: run_fx1_carry(use_yf), True),
    }

    # Filter strategies
    if args.strategy != "all":
        if args.strategy not in ALL_RUNNERS:
            print(f"[ERROR] Unknown strategy: {args.strategy}")
            print(f"  Available: {list(ALL_RUNNERS.keys())}")
            sys.exit(1)
        runners = {args.strategy: ALL_RUNNERS[args.strategy]}
    else:
        runners = ALL_RUNNERS

    results = {}

    for key, (name, runner_fn, needs_yf) in runners.items():
        if needs_yf and not use_yf:
            print(f"\n  [SKIP] {name} (requires yfinance)")
            results[key] = {"n_trades": 0, "verdict": "SKIPPED"}
            continue

        print(f"\n{'='*80}")
        print(f"  {name}")
        print(f"{'='*80}")
        sys.stdout.flush()

        try:
            result = runner_fn()
            results[key] = result
        except Exception as e:
            print(f"  [ERROR] {e}")
            traceback.print_exc()
            results[key] = {"n_trades": 0, "verdict": "ERROR", "error": str(e)}

        sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*100}")
    print(f"  P1 STRATEGIES — RESULTATS FINAUX")
    print(f"{'='*100}")
    print(f"  {'#':<8} {'Strategie':<40} {'Trades':>6} {'Net PnL':>12} "
          f"{'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Verdict':>14}")
    print(f"  {'-'*8} {'-'*40} {'-'*6} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*14}")

    strategy_names = {
        "short5": "Cross-Asset Risk-Off Short",
        "short6": "OpEx Short Extension",
        "eu_bce": "BCE Rate Decision Drift",
        "opt1": "Put Credit Spread SPY",
        "opt2": "Earnings IV Crush",
        "fut1": "ES Trend Following 1H",
        "fx1": "Carry Trade AUD/JPY",
    }

    for key, m in results.items():
        display_name = strategy_names.get(key, key)
        if "error" in m:
            print(f"  {key:<8} {display_name:<40} ERROR: {m['error'][:40]}")
        elif m.get("n_trades", 0) == 0:
            print(f"  {key:<8} {display_name:<40}      0 "
                  f"{'':>12} {'':>8} {'':>6} {'':>6} {'':>6} "
                  f"{m.get('verdict', 'NO_TRADES'):>14}")
        else:
            print(f"  {key:<8} {display_name:<40} {m['n_trades']:>6} "
                  f"${m['net_pnl']:>10,.2f} "
                  f"{m['sharpe_ratio']:>8.2f} {m['win_rate']:>5.1f}% "
                  f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>5.2f}% "
                  f"{m['verdict']:>14}")

    # ── Export JSON ──
    json_path = OUTPUT_DIR / "p1_strategies_results.json"

    # Clean results for JSON serialization
    json_results = {}
    for key, m in results.items():
        clean = {}
        for k, v in m.items():
            if isinstance(v, (pd.Series, pd.DataFrame)):
                continue  # Skip non-serializable
            elif isinstance(v, (np.integer, np.int64)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating, np.float64)):
                clean[k] = float(v)
            elif isinstance(v, dict):
                # Nested dict (walk_forward, by_weekday, etc.)
                nested = {}
                for nk, nv in v.items():
                    if isinstance(nv, (np.integer, np.int64)):
                        nested[nk] = int(nv)
                    elif isinstance(nv, (np.floating, np.float64)):
                        nested[nk] = float(nv)
                    elif isinstance(nv, (pd.Series, pd.DataFrame)):
                        continue
                    else:
                        nested[nk] = nv
                clean[k] = nested
            else:
                clean[k] = v
        clean["strategy_name"] = strategy_names.get(key, key)
        json_results[key] = clean

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_results, f, indent=2, default=str)
    print(f"\n  [JSON] {json_path}")

    # ── Count verdicts ──
    winners = [k for k, m in results.items() if "WINNER" in m.get("verdict", "")]
    potentiels = [k for k, m in results.items() if m.get("verdict", "") == "POTENTIEL"]
    rejetes = [k for k, m in results.items() if m.get("verdict", "") == "REJETE"]

    print(f"\n  WINNERS: {len(winners)}")
    for w in winners:
        print(f"    + {strategy_names.get(w, w)}")
    print(f"  POTENTIELS: {len(potentiels)}")
    for p in potentiels:
        print(f"    ~ {strategy_names.get(p, p)}")
    print(f"  REJETES: {len(rejetes)}")
    for r in rejetes:
        print(f"    - {strategy_names.get(r, r)}")

    print(f"\n{'='*80}")
    return results


if __name__ == "__main__":
    main()
