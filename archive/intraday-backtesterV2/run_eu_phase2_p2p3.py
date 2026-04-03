"""
EU Phase 2 -- P2 + P3 Strategies Backtest (yfinance data, 5Y daily).

Strategies:
  P2-4: Brent Lag Play (Energy EU) -- Brent return > 1% -> TTE.PA follows
  P2-5: Sector Rotation EU Weekly -- L/S top/bottom 2 EU sector ETFs
  P3-1: DAX Breakout Post-BCE -- directional trade on ECB days
  P3-2: VSTOXX-proxy/VIX Spread Mean Reversion -- z-score based
  P3-3: Brent Crude Momentum (Swing) -- EMA crossover + ATR SL/TP

Usage:
    python run_eu_phase2_p2p3.py
    python run_eu_phase2_p2p3.py --strategy brent_lag
    python run_eu_phase2_p2p3.py --no-wf

Generates:
    - output/session_20260326/eu_phase2_p2p3_results.json
"""
import sys
import os
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime, timedelta

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

# -- Paths --
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output" / "session_20260326"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -- Capital & Costs --
INITIAL_CAPITAL = 200_000  # EUR
EU_ACTION_COST_RT = 0.0026  # 0.26% round-trip (actions EU)
FUTURES_COST_RT = 0.00003   # 0.003% round-trip (futures/index)
VIX_FUTURES_COST_RT = 0.00005  # 0.005% round-trip (vol futures)
BRENT_FUTURES_COST_RT = 0.00005  # 0.005% round-trip (Brent futures)


# ======================================================================
#  DATA FETCHING (yfinance)
# ======================================================================

def fetch_yfinance(tickers: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
    """Download daily OHLCV from yfinance for a list of tickers."""
    data = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                # Flatten MultiIndex columns if present
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                # Normalize column names to lowercase
                df.columns = [c.lower() for c in df.columns]
                # Ensure we have required columns
                required = {"open", "high", "low", "close", "volume"}
                if required.issubset(set(df.columns)):
                    data[ticker] = df.sort_index()
                    print(f"  [OK] {ticker}: {len(df)} bars "
                          f"({df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')})")
                else:
                    print(f"  [WARN] {ticker}: missing columns {required - set(df.columns)}")
            else:
                print(f"  [WARN] {ticker}: no data returned")
        except Exception as e:
            print(f"  [ERROR] {ticker}: {e}")
    return data


# ======================================================================
#  METRICS ENGINE
# ======================================================================

def calculate_metrics(trades: list[dict], initial_capital: float) -> dict:
    """Compute standard strategy metrics from a list of trade dicts."""
    if not trades:
        return {
            "sharpe_ratio": 0, "win_rate": 0, "profit_factor": 0,
            "max_drawdown_pct": 0, "n_trades": 0, "total_return_pct": 0,
            "net_pnl": 0, "avg_winner": 0, "avg_loser": 0,
            "best_trade": 0, "worst_trade": 0,
        }

    df = pd.DataFrame(trades)
    net_pnls = df["net_pnl"].values
    total_pnl = net_pnls.sum()

    winners = net_pnls[net_pnls > 0]
    losers = net_pnls[net_pnls <= 0]

    win_rate = len(winners) / len(net_pnls) * 100 if len(net_pnls) > 0 else 0
    gross_profit = winners.sum() if len(winners) > 0 else 0
    gross_loss = abs(losers.sum()) if len(losers) > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_winner = float(winners.mean()) if len(winners) > 0 else 0
    avg_loser = float(losers.mean()) if len(losers) > 0 else 0

    # Equity curve for Sharpe & DD
    dates = df["date"].values
    daily_pnl = df.groupby("date")["net_pnl"].sum().sort_index()
    equity = initial_capital + daily_pnl.cumsum()

    # Max drawdown
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak * 100
    max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0

    # Annualized Sharpe
    daily_returns = equity.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))
    else:
        sharpe = 0

    return {
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(float(max_dd), 2),
        "n_trades": len(net_pnls),
        "total_return_pct": round(total_pnl / initial_capital * 100, 2),
        "net_pnl": round(total_pnl, 2),
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "best_trade": round(float(net_pnls.max()), 2),
        "worst_trade": round(float(net_pnls.min()), 2),
    }


# ======================================================================
#  WALK-FORWARD ENGINE
# ======================================================================

def walk_forward(run_fn, data: dict, n_windows: int = 5,
                 is_days: int = 120, oos_days: int = 60) -> dict | None:
    """
    Walk-forward validation.
    run_fn(data, start_date, end_date) -> list[dict] of trades.
    """
    # Collect all dates across all tickers
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.date if hasattr(df.index, 'date') else
                         [pd.Timestamp(x).date() for x in df.index])
    all_dates = sorted(all_dates)

    if len(all_dates) < is_days + oos_days:
        return None

    step = max((len(all_dates) - is_days - oos_days) // max(n_windows - 1, 1), 30)
    windows = []
    i = 0
    while i + is_days + oos_days <= len(all_dates):
        oos_start = all_dates[i + is_days]
        oos_end = all_dates[min(i + is_days + oos_days - 1, len(all_dates) - 1)]
        windows.append((oos_start, oos_end))
        i += step

    if not windows:
        return None

    oos_results = []
    for oos_start, oos_end in windows:
        trades = run_fn(data, oos_start, oos_end)
        m = calculate_metrics(trades, INITIAL_CAPITAL)
        oos_results.append({
            "oos_start": str(oos_start),
            "oos_end": str(oos_end),
            "return_pct": m["total_return_pct"],
            "sharpe": m["sharpe_ratio"],
            "pf": m["profit_factor"],
            "trades": m["n_trades"],
            "win_rate": m["win_rate"],
        })

    profitable = sum(1 for r in oos_results if r["return_pct"] > 0)
    hit_rate = profitable / len(oos_results) * 100

    return {
        "hit_rate": round(hit_rate, 0),
        "avg_return": round(np.mean([r["return_pct"] for r in oos_results]), 2),
        "avg_sharpe": round(np.mean([r["sharpe"] for r in oos_results]), 2),
        "n_windows": len(oos_results),
        "profitable_windows": profitable,
        "verdict": "VALIDATED" if hit_rate >= 50 and np.mean([r["return_pct"] for r in oos_results]) > 0 else "REJECTED",
        "windows": oos_results,
    }


# ======================================================================
#  P2-4 : BRENT LAG PLAY (Energy EU)
# ======================================================================

def run_brent_lag(data: dict, start_date=None, end_date=None) -> list[dict]:
    """
    Signal: Brent daily return > 1.0% -> LONG TTE.PA same day.
    Measures lagged correlation Brent -> TTE.PA at lag 0, 1, 2 days.
    Cost: 0.26% RT (EU equity).
    """
    brent = data.get("BZ=F")
    tte = data.get("TTE.PA")
    if brent is None or tte is None:
        return []

    # Align dates
    brent_ret = brent["close"].pct_change().dropna()
    tte_ret = tte["close"].pct_change().dropna()

    common = brent_ret.index.intersection(tte_ret.index)
    brent_ret = brent_ret.loc[common]
    tte_ret = tte_ret.loc[common]

    # Date filter
    if start_date:
        mask = brent_ret.index.date >= start_date
        brent_ret = brent_ret[mask]
        tte_ret = tte_ret[mask]
    if end_date:
        mask = brent_ret.index.date <= end_date
        brent_ret = brent_ret[mask]
        tte_ret = tte_ret[mask]

    if len(brent_ret) < 20:
        return []

    trades = []
    position_size = INITIAL_CAPITAL * 0.10  # 10% per trade

    for i in range(len(brent_ret)):
        dt = brent_ret.index[i]
        brent_r = brent_ret.iloc[i]

        # Signal: Brent return > 1% -> LONG TTE same day
        if brent_r > 0.01:
            tte_r = tte_ret.iloc[i]
            tte_close = tte["close"].loc[dt] if dt in tte["close"].index else None
            if tte_close is None or tte_close <= 0:
                continue

            shares = int(position_size / tte_close)
            if shares < 1:
                continue

            # P&L = TTE return on the same day (open to close approximation)
            tte_open = tte["open"].loc[dt] if dt in tte["open"].index else tte_close
            if tte_open <= 0:
                continue

            gross_pnl = (tte_close - tte_open) * shares
            commission = shares * tte_open * EU_ACTION_COST_RT
            net_pnl = gross_pnl - commission

            trades.append({
                "date": dt.date() if hasattr(dt, 'date') else dt,
                "ticker": "TTE.PA",
                "direction": "LONG",
                "entry_price": round(float(tte_open), 4),
                "exit_price": round(float(tte_close), 4),
                "shares": shares,
                "pnl": round(float(gross_pnl), 2),
                "commission": round(float(commission), 2),
                "net_pnl": round(float(net_pnl), 2),
                "brent_return": round(float(brent_r) * 100, 2),
                "tte_return": round(float(tte_r) * 100, 2),
            })

        # Also trade the inverse: Brent < -1% -> SHORT TTE
        elif brent_r < -0.01:
            tte_r = tte_ret.iloc[i]
            tte_close = tte["close"].loc[dt] if dt in tte["close"].index else None
            if tte_close is None or tte_close <= 0:
                continue

            shares = int(position_size / tte_close)
            if shares < 1:
                continue

            tte_open = tte["open"].loc[dt] if dt in tte["open"].index else tte_close
            if tte_open <= 0:
                continue

            gross_pnl = (tte_open - tte_close) * shares  # SHORT
            commission = shares * tte_open * EU_ACTION_COST_RT
            net_pnl = gross_pnl - commission

            trades.append({
                "date": dt.date() if hasattr(dt, 'date') else dt,
                "ticker": "TTE.PA",
                "direction": "SHORT",
                "entry_price": round(float(tte_open), 4),
                "exit_price": round(float(tte_close), 4),
                "shares": shares,
                "pnl": round(float(gross_pnl), 2),
                "commission": round(float(commission), 2),
                "net_pnl": round(float(net_pnl), 2),
                "brent_return": round(float(brent_r) * 100, 2),
                "tte_return": round(float(tte_r) * 100, 2),
            })

    return trades


def analyze_brent_tte_correlation(data: dict):
    """Print lag correlation analysis Brent -> TTE.PA."""
    brent = data.get("BZ=F")
    tte = data.get("TTE.PA")
    if brent is None or tte is None:
        print("  [SKIP] Missing data for correlation analysis")
        return {}

    brent_ret = brent["close"].pct_change().dropna()
    tte_ret = tte["close"].pct_change().dropna()

    common = brent_ret.index.intersection(tte_ret.index)
    brent_ret = brent_ret.loc[common]
    tte_ret = tte_ret.loc[common]

    correlations = {}
    print("\n  Brent -> TTE.PA Lag Correlation:")
    for lag in [0, 1, 2]:
        if lag == 0:
            corr = float(brent_ret.corr(tte_ret))
        else:
            corr = float(brent_ret.iloc[:-lag].reset_index(drop=True).corr(
                tte_ret.iloc[lag:].reset_index(drop=True)))
        correlations[f"lag_{lag}"] = round(corr, 4)
        print(f"    Lag {lag}: {corr:.4f}")

    # Conditional: when Brent > 1%
    big_move_mask = brent_ret.abs() > 0.01
    if big_move_mask.sum() > 10:
        same_dir = ((brent_ret[big_move_mask] > 0) & (tte_ret[big_move_mask] > 0)) | \
                   ((brent_ret[big_move_mask] < 0) & (tte_ret[big_move_mask] < 0))
        hit_rate = same_dir.sum() / big_move_mask.sum() * 100
        correlations["conditional_hit_rate"] = round(float(hit_rate), 1)
        print(f"    Conditional hit rate (|Brent| > 1%): {hit_rate:.1f}% ({big_move_mask.sum()} events)")

    return correlations


# ======================================================================
#  P2-5 : SECTOR ROTATION EU WEEKLY
# ======================================================================

def run_sector_rotation(data: dict, start_date=None, end_date=None) -> list[dict]:
    """
    Weekly: LONG top 2 sectors, SHORT bottom 2 (dollar-neutral).
    Rebalance Monday, close Friday.
    Tickers: EXV1.DE (Banks), EXV3.DE (Tech), EXH1.DE (Energy), EXV4.DE (Healthcare).
    Cost: 0.26% x 4 positions per week.
    """
    sector_tickers = ["EXV1.DE", "EXV3.DE", "EXH1.DE", "EXV4.DE"]
    sector_names = {
        "EXV1.DE": "Banks",
        "EXV3.DE": "Tech",
        "EXH1.DE": "Energy",
        "EXV4.DE": "Healthcare",
    }

    # Check all tickers present
    available = [t for t in sector_tickers if t in data]
    if len(available) < 4:
        print(f"  [WARN] Only {len(available)}/4 sector ETFs available: {available}")
        if len(available) < 2:
            return []

    # Build weekly returns
    weekly_returns = {}
    for ticker in available:
        df = data[ticker]
        weekly = df["close"].resample("W-FRI").last().pct_change().dropna()
        weekly_returns[ticker] = weekly

    # Align dates
    common_weeks = weekly_returns[available[0]].index
    for t in available[1:]:
        common_weeks = common_weeks.intersection(weekly_returns[t].index)

    if len(common_weeks) < 5:
        return []

    # Date filter
    if start_date:
        common_weeks = common_weeks[common_weeks.date >= start_date]
    if end_date:
        common_weeks = common_weeks[common_weeks.date <= end_date]

    trades = []
    position_size_per_leg = INITIAL_CAPITAL * 0.10  # 10% per position

    for i in range(1, len(common_weeks)):
        week_end = common_weeks[i]
        prev_week = common_weeks[i - 1]

        # Rank sectors by previous week's return
        perf = {}
        for ticker in available:
            if prev_week in weekly_returns[ticker].index:
                perf[ticker] = float(weekly_returns[ticker].loc[prev_week])

        if len(perf) < 4:
            continue

        ranked = sorted(perf.items(), key=lambda x: x[1], reverse=True)
        longs = [ranked[0][0], ranked[1][0]]     # Top 2
        shorts = [ranked[-1][0], ranked[-2][0]]   # Bottom 2

        # Calculate P&L for the current week
        for ticker in longs:
            df = data[ticker]
            # Get Monday open (or first available after prev Friday)
            week_data = df[(df.index > prev_week) & (df.index <= week_end)]
            if week_data.empty:
                continue

            entry_price = float(week_data["open"].iloc[0])
            exit_price = float(week_data["close"].iloc[-1])
            if entry_price <= 0:
                continue

            shares = int(position_size_per_leg / entry_price)
            if shares < 1:
                continue

            gross_pnl = (exit_price - entry_price) * shares
            commission = shares * entry_price * EU_ACTION_COST_RT
            net_pnl = gross_pnl - commission

            trades.append({
                "date": week_end.date() if hasattr(week_end, 'date') else week_end,
                "ticker": ticker,
                "direction": "LONG",
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "shares": shares,
                "pnl": round(gross_pnl, 2),
                "commission": round(commission, 2),
                "net_pnl": round(net_pnl, 2),
                "sector": sector_names.get(ticker, ticker),
                "prev_week_return": round(perf[ticker] * 100, 2),
            })

        for ticker in shorts:
            df = data[ticker]
            week_data = df[(df.index > prev_week) & (df.index <= week_end)]
            if week_data.empty:
                continue

            entry_price = float(week_data["open"].iloc[0])
            exit_price = float(week_data["close"].iloc[-1])
            if entry_price <= 0:
                continue

            shares = int(position_size_per_leg / entry_price)
            if shares < 1:
                continue

            gross_pnl = (entry_price - exit_price) * shares  # SHORT
            commission = shares * entry_price * EU_ACTION_COST_RT
            net_pnl = gross_pnl - commission

            trades.append({
                "date": week_end.date() if hasattr(week_end, 'date') else week_end,
                "ticker": ticker,
                "direction": "SHORT",
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "shares": shares,
                "pnl": round(gross_pnl, 2),
                "commission": round(commission, 2),
                "net_pnl": round(net_pnl, 2),
                "sector": sector_names.get(ticker, ticker),
                "prev_week_return": round(perf[ticker] * 100, 2),
            })

    return trades


# ======================================================================
#  P3-1 : DAX BREAKOUT POST-BCE
# ======================================================================

# ECB (BCE) meeting dates -- historical and projected
# Source: ECB published schedule (8 meetings/year)
ECB_DATES = [
    # 2021
    "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
    "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
    # 2022
    "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
    "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
    # 2023
    "2023-02-02", "2023-03-16", "2023-04-06", "2023-06-15",
    "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
    # 2024
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    # 2025
    "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
    "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
    # 2026 (projected)
    "2026-01-22", "2026-03-05", "2026-04-02", "2026-06-04",
]
ECB_DATES_SET = set(pd.to_datetime(ECB_DATES).date)


def run_dax_bce(data: dict, start_date=None, end_date=None) -> list[dict]:
    """
    On ECB days: if DAX open-to-close return > 0.3% -> LONG signal.
    If < -0.3% -> SHORT signal. Trade at close, exit next day close.
    Cost: 0.003% RT (futures).
    """
    dax = data.get("^GDAXI")
    if dax is None:
        return []

    trades = []
    position_size = INITIAL_CAPITAL * 0.20  # 20% per futures trade (leveraged)

    dates = dax.index
    date_list = [d.date() if hasattr(d, 'date') else pd.Timestamp(d).date() for d in dates]

    for i in range(len(date_list) - 1):
        dt = date_list[i]

        # Date filter
        if start_date and dt < start_date:
            continue
        if end_date and dt > end_date:
            continue

        if dt not in ECB_DATES_SET:
            continue

        row = dax.iloc[i]
        open_price = float(row["open"])
        close_price = float(row["close"])

        if open_price <= 0:
            continue

        intraday_ret = (close_price - open_price) / open_price

        # Next trading day for exit
        next_row = dax.iloc[i + 1]
        next_close = float(next_row["close"])

        if abs(intraday_ret) < 0.003:  # < 0.3% -> no signal
            continue

        direction = "LONG" if intraday_ret > 0.003 else "SHORT"

        # Notional sizing (futures)
        contracts = max(1, int(position_size / close_price))

        if direction == "LONG":
            gross_pnl = (next_close - close_price) * contracts
        else:
            gross_pnl = (close_price - next_close) * contracts

        commission = contracts * close_price * FUTURES_COST_RT
        net_pnl = gross_pnl - commission

        trades.append({
            "date": dt,
            "ticker": "^GDAXI",
            "direction": direction,
            "entry_price": round(close_price, 2),
            "exit_price": round(next_close, 2),
            "shares": contracts,
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(net_pnl, 2),
            "bce_day_return": round(intraday_ret * 100, 2),
        })

    return trades


# ======================================================================
#  P3-2 : VSTOXX/VIX SPREAD MEAN REVERSION
# ======================================================================

def run_vstoxx_vix_spread(data: dict, start_date=None, end_date=None) -> list[dict]:
    """
    Daily spread = VSTOXX_proxy - VIX.
    VSTOXX proxy = annualized 21d realized vol of STOXX50E (^STOXX50E).
    Z-score (60d lookback).
    If z > 2.0 -> SHORT spread (sell VSTOXX, buy VIX).
    If z < -2.0 -> LONG spread.
    Close when z reverts to 0.5 (or stop at z=3.0).
    Proxy P&L: change in spread * notional.
    Cost: 0.005% RT (vol futures).
    """
    stoxx50 = data.get("^STOXX50E")
    vix = data.get("^VIX")
    if stoxx50 is None or vix is None:
        return []

    # Build VSTOXX proxy: annualized 21d realized vol of STOXX50E
    stoxx_ret = stoxx50["close"].pct_change()
    vstoxx_proxy = stoxx_ret.rolling(21).std() * np.sqrt(252) * 100  # in vol points like VIX
    vstoxx_proxy = vstoxx_proxy.dropna()

    # Align with VIX
    vix_close = vix["close"]
    common = vstoxx_proxy.index.intersection(vix_close.index)
    vstoxx_proxy = vstoxx_proxy.loc[common]
    vix_close = vix_close.loc[common]

    if len(common) < 80:
        return []

    spread = vstoxx_proxy - vix_close

    # Z-score (60d rolling)
    lookback = 60
    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    z_score = (spread - spread_mean) / spread_std.replace(0, np.nan)
    z_score = z_score.dropna()

    # Date filter
    if start_date:
        z_score = z_score[z_score.index.date >= start_date]
    if end_date:
        z_score = z_score[z_score.index.date <= end_date]

    if len(z_score) < 10:
        return []

    trades = []
    position = None  # {"direction", "entry_spread", "entry_date", "entry_z"}
    notional = INITIAL_CAPITAL * 0.15  # 15% notional per trade

    for dt in z_score.index:
        z = float(z_score.loc[dt])
        current_spread = float(spread.loc[dt])

        if position is None:
            # Entry signals
            if z > 2.0:
                position = {
                    "direction": "SHORT",
                    "entry_spread": current_spread,
                    "entry_date": dt,
                    "entry_z": z,
                }
            elif z < -2.0:
                position = {
                    "direction": "LONG",
                    "entry_spread": current_spread,
                    "entry_date": dt,
                    "entry_z": z,
                }
        else:
            # Exit conditions
            should_close = False
            reason = ""

            if position["direction"] == "SHORT":
                if z <= 0.5:
                    should_close = True
                    reason = "reversion"
                elif z >= 3.0:
                    should_close = True
                    reason = "stop_loss"
            else:  # LONG
                if z >= -0.5:
                    should_close = True
                    reason = "reversion"
                elif z <= -3.0:
                    should_close = True
                    reason = "stop_loss"

            if should_close:
                spread_change = current_spread - position["entry_spread"]
                denom = max(abs(position["entry_spread"]), 0.01)
                if position["direction"] == "SHORT":
                    gross_pnl = -spread_change * (notional / denom)
                else:
                    gross_pnl = spread_change * (notional / denom)

                commission = notional * VIX_FUTURES_COST_RT * 2  # entry + exit
                net_pnl = gross_pnl - commission

                entry_date = position["entry_date"]
                trades.append({
                    "date": entry_date.date() if hasattr(entry_date, 'date') else entry_date,
                    "ticker": "VSTOXX-VIX",
                    "direction": position["direction"],
                    "entry_price": round(position["entry_spread"], 2),
                    "exit_price": round(current_spread, 2),
                    "shares": 1,
                    "pnl": round(float(gross_pnl), 2),
                    "commission": round(float(commission), 2),
                    "net_pnl": round(float(net_pnl), 2),
                    "entry_z": round(position["entry_z"], 2),
                    "exit_z": round(z, 2),
                    "exit_reason": reason,
                    "holding_days": (dt - entry_date).days,
                })

                position = None

    return trades


# ======================================================================
#  P3-3 : BRENT CRUDE MOMENTUM (SWING)
# ======================================================================

def run_brent_momentum(data: dict, start_date=None, end_date=None) -> list[dict]:
    """
    Signal: price > EMA(20) AND EMA(20) > EMA(50) -> LONG. Inverse -> SHORT.
    SL = 2.5 x ATR(14). TP = 4.0 x ATR(14) or trailing stop.
    Holding: 2-10 days (swing).
    Cost: 0.005% RT (Brent futures).
    """
    brent = data.get("BZ=F")
    if brent is None:
        return []

    df = brent.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # ATR(14)
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr14"] = df["tr"].rolling(14).mean()

    # Remove NaN rows
    df = df.dropna(subset=["ema50", "atr14"])

    # Date filter
    if start_date:
        df = df[df.index.date >= start_date]
    if end_date:
        df = df[df.index.date <= end_date]

    if len(df) < 10:
        return []

    trades = []
    position = None  # {"direction", "entry_price", "entry_date", "sl", "tp", "trail_stop", "days_held"}
    notional = INITIAL_CAPITAL * 0.15  # 15% per trade
    max_hold = 10

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        dt = df.index[i]
        price = float(row["close"])
        atr = float(row["atr14"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        high = float(row["high"])
        low = float(row["low"])

        if position is not None:
            position["days_held"] += 1

            # Check stops
            should_close = False
            exit_price = price
            reason = ""

            if position["direction"] == "LONG":
                # Trailing stop update
                trail = max(position["trail_stop"], price - 2.5 * atr)
                position["trail_stop"] = trail

                if low <= position["sl"]:
                    should_close = True
                    exit_price = position["sl"]
                    reason = "stop_loss"
                elif low <= trail and trail > position["sl"]:
                    should_close = True
                    exit_price = trail
                    reason = "trailing_stop"
                elif high >= position["tp"]:
                    should_close = True
                    exit_price = position["tp"]
                    reason = "take_profit"
                elif position["days_held"] >= max_hold:
                    should_close = True
                    reason = "max_hold"
            else:  # SHORT
                trail = min(position["trail_stop"], price + 2.5 * atr)
                position["trail_stop"] = trail

                if high >= position["sl"]:
                    should_close = True
                    exit_price = position["sl"]
                    reason = "stop_loss"
                elif high >= trail and trail < position["sl"]:
                    should_close = True
                    exit_price = trail
                    reason = "trailing_stop"
                elif low <= position["tp"]:
                    should_close = True
                    exit_price = position["tp"]
                    reason = "take_profit"
                elif position["days_held"] >= max_hold:
                    should_close = True
                    reason = "max_hold"

            if should_close:
                contracts = max(1, int(notional / position["entry_price"]))

                if position["direction"] == "LONG":
                    gross_pnl = (exit_price - position["entry_price"]) * contracts
                else:
                    gross_pnl = (position["entry_price"] - exit_price) * contracts

                commission = contracts * position["entry_price"] * BRENT_FUTURES_COST_RT
                net_pnl = gross_pnl - commission

                entry_date = position["entry_date"]
                trades.append({
                    "date": entry_date.date() if hasattr(entry_date, 'date') else entry_date,
                    "ticker": "BZ=F",
                    "direction": position["direction"],
                    "entry_price": round(position["entry_price"], 2),
                    "exit_price": round(exit_price, 2),
                    "shares": contracts,
                    "pnl": round(float(gross_pnl), 2),
                    "commission": round(float(commission), 2),
                    "net_pnl": round(float(net_pnl), 2),
                    "exit_reason": reason,
                    "holding_days": position["days_held"],
                })

                position = None

        # Entry signal (only if no position)
        if position is None:
            prev_ema20 = float(prev["ema20"])
            prev_ema50 = float(prev["ema50"])

            # LONG: price > EMA20 AND EMA20 > EMA50 (and prev was not aligned)
            if price > ema20 and ema20 > ema50:
                # Only enter on fresh signal (prev was NOT in this regime)
                if not (float(prev["close"]) > prev_ema20 and prev_ema20 > prev_ema50):
                    sl = price - 2.5 * atr
                    tp = price + 4.0 * atr
                    position = {
                        "direction": "LONG",
                        "entry_price": price,
                        "entry_date": dt,
                        "sl": sl,
                        "tp": tp,
                        "trail_stop": sl,
                        "days_held": 0,
                    }

            # SHORT: price < EMA20 AND EMA20 < EMA50
            elif price < ema20 and ema20 < ema50:
                if not (float(prev["close"]) < prev_ema20 and prev_ema20 < prev_ema50):
                    sl = price + 2.5 * atr
                    tp = price - 4.0 * atr
                    position = {
                        "direction": "SHORT",
                        "entry_price": price,
                        "entry_date": dt,
                        "sl": sl,
                        "tp": tp,
                        "trail_stop": sl,
                        "days_held": 0,
                    }

    return trades


# ======================================================================
#  STRATEGY REGISTRY
# ======================================================================

STRATEGIES = {
    "brent_lag": {
        "name": "P2-4 Brent Lag Play",
        "tickers": ["BZ=F", "TTE.PA"],
        "run_fn": run_brent_lag,
        "category": "P2",
    },
    "sector_rotation": {
        "name": "P2-5 Sector Rotation EU Weekly",
        "tickers": ["EXV1.DE", "EXV3.DE", "EXH1.DE", "EXV4.DE"],
        "run_fn": run_sector_rotation,
        "category": "P2",
    },
    "dax_bce": {
        "name": "P3-1 DAX Breakout Post-BCE",
        "tickers": ["^GDAXI"],
        "run_fn": run_dax_bce,
        "category": "P3",
    },
    "vstoxx_vix": {
        "name": "P3-2 VSTOXX-proxy/VIX Spread MR",
        "tickers": ["^STOXX50E", "^VIX"],
        "run_fn": run_vstoxx_vix_spread,
        "category": "P3",
    },
    "brent_momentum": {
        "name": "P3-3 Brent Crude Momentum",
        "tickers": ["BZ=F"],
        "run_fn": run_brent_momentum,
        "category": "P3",
    },
}


# ======================================================================
#  PRETTY PRINT
# ======================================================================

def print_results(name: str, metrics: dict, wf: dict | None = None):
    """Pretty print strategy results."""
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    print(f"  Total Return:     {metrics['total_return_pct']:>8.2f}%")
    print(f"  Net P&L:          EUR {metrics['net_pnl']:>10,.2f}")
    print(f"  Trades:           {metrics['n_trades']:>8d}")
    print(f"  Win Rate:         {metrics['win_rate']:>8.1f}%")
    print(f"  Profit Factor:    {metrics['profit_factor']:>8.2f}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:>8.2f}")
    print(f"  Max Drawdown:     {metrics['max_drawdown_pct']:>8.2f}%")
    print(f"  Avg Winner:       EUR {metrics['avg_winner']:>10,.2f}")
    print(f"  Avg Loser:        EUR {metrics['avg_loser']:>10,.2f}")
    print(f"  Best Trade:       EUR {metrics['best_trade']:>10,.2f}")
    print(f"  Worst Trade:      EUR {metrics['worst_trade']:>10,.2f}")
    if wf:
        print(f"  -- Walk-Forward --")
        print(f"  Hit Rate:         {wf['hit_rate']:>8.0f}% ({wf['profitable_windows']}/{wf['n_windows']})")
        print(f"  Avg OOS Return:   {wf['avg_return']:>8.2f}%")
        print(f"  Avg OOS Sharpe:   {wf['avg_sharpe']:>8.2f}")
        print(f"  Verdict:          {wf['verdict']}")
    print(f"{'='*65}")


# ======================================================================
#  MAIN
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="EU Phase 2 P2+P3 Backtest")
    parser.add_argument("--strategy", type=str, default="all",
                        help="Strategy key (brent_lag, sector_rotation, dax_bce, vstoxx_vix, brent_momentum, all)")
    parser.add_argument("--no-wf", action="store_true",
                        help="Skip walk-forward validation")
    args = parser.parse_args()

    print("=" * 65)
    print("  EU PHASE 2 -- P2 + P3 STRATEGIES BACKTEST")
    print(f"  Capital: EUR {INITIAL_CAPITAL:,.0f}")
    print(f"  Data: yfinance 5Y daily")
    print("=" * 65)

    # -- Select strategies --
    if args.strategy == "all":
        to_run = STRATEGIES
    elif args.strategy in STRATEGIES:
        to_run = {args.strategy: STRATEGIES[args.strategy]}
    else:
        print(f"[ERROR] Unknown strategy: {args.strategy}")
        print(f"  Available: {list(STRATEGIES.keys())}")
        sys.exit(1)

    # -- Fetch data --
    all_tickers = set()
    for s in to_run.values():
        all_tickers.update(s["tickers"])

    print(f"\n[DATA] Downloading {len(all_tickers)} tickers from yfinance...")
    data = fetch_yfinance(sorted(all_tickers), period="5y")

    if not data:
        print("[ERROR] No data downloaded. Check internet connection.")
        sys.exit(1)

    print(f"\n  [DATA] {len(data)}/{len(all_tickers)} tickers loaded")

    # -- Run strategies --
    all_results = {}

    for key, strat_info in to_run.items():
        name = strat_info["name"]
        run_fn = strat_info["run_fn"]

        print(f"\n{'-'*65}")
        print(f"  Running: {name}")
        print(f"{'-'*65}")

        # Extra analysis for brent_lag
        if key == "brent_lag":
            corr_info = analyze_brent_tte_correlation(data)
        else:
            corr_info = {}

        try:
            trades = run_fn(data)
            metrics = calculate_metrics(trades, INITIAL_CAPITAL)
            print_results(name, metrics)

            # Walk-forward
            wf_result = None
            if not args.no_wf and metrics["n_trades"] >= 30:
                print(f"\n  [WF] Running walk-forward for {name}...")
                wf_result = walk_forward(run_fn, data)
                if wf_result:
                    print(f"  [WF] Hit rate: {wf_result['hit_rate']:.0f}% "
                          f"({wf_result['profitable_windows']}/{wf_result['n_windows']}) "
                          f"-> {wf_result['verdict']}")

            result_entry = {
                "strategy": name,
                "key": key,
                "category": strat_info["category"],
                **metrics,
            }
            if wf_result:
                result_entry["walk_forward"] = wf_result
            if corr_info:
                result_entry["correlation_analysis"] = corr_info

            all_results[name] = result_entry

            # Save trades CSV
            if trades:
                trades_path = OUTPUT_DIR / f"trades_eu_{key}.csv"
                pd.DataFrame(trades).to_csv(trades_path, index=False)
                print(f"  [CSV] {trades_path}")

        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            import traceback
            traceback.print_exc()
            all_results[name] = {
                "strategy": name,
                "key": key,
                "category": strat_info["category"],
                "error": str(e),
                "n_trades": 0,
            }

    # -- Save JSON --
    json_path = OUTPUT_DIR / "eu_phase2_p2p3_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  [JSON] {json_path}")

    # -- Final Summary --
    print(f"\n{'='*75}")
    print("  EU PHASE 2 -- P2 + P3 SUMMARY")
    print(f"{'='*75}")
    print(f"  {'Strategy':<35s} {'Sharpe':>7s} {'PF':>6s} {'WR%':>6s} "
          f"{'Ret%':>8s} {'Trades':>6s} {'DD%':>7s} {'WF':>10s}")
    print(f"  {'-'*35} {'-'*7} {'-'*6} {'-'*6} {'-'*8} {'-'*6} {'-'*7} {'-'*10}")

    for name, r in all_results.items():
        if "error" in r:
            print(f"  {name:<35s} ERROR: {r['error'][:30]}")
            continue

        wf_status = ""
        if "walk_forward" in r:
            wf_status = r["walk_forward"]["verdict"]
        elif r.get("n_trades", 0) < 30:
            wf_status = "< 30 tr."
        else:
            wf_status = "SKIPPED"

        verdict = ""
        if r.get("sharpe_ratio", 0) >= 1.0 and r.get("profit_factor", 0) >= 1.3:
            verdict = " << WINNER"
        elif r.get("sharpe_ratio", 0) >= 0.5 and r.get("profit_factor", 0) >= 1.1:
            verdict = " << POTENTIEL"

        print(f"  {name:<35s} {r.get('sharpe_ratio',0):>7.2f} "
              f"{r.get('profit_factor',0):>6.2f} {r.get('win_rate',0):>5.1f}% "
              f"{r.get('total_return_pct',0):>7.2f}% {r.get('n_trades',0):>6d} "
              f"{r.get('max_drawdown_pct',0):>6.2f}% {wf_status:>10s}{verdict}")

    print(f"\n  Results saved: {json_path}")
    print(f"{'='*75}")


if __name__ == "__main__":
    main()
