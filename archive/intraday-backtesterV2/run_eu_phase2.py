#!/usr/bin/env python3
"""
EU Phase 2 -- Backtest 3 stratégies P0 EU.

Strategies:
  1. EU-CROSS-2 : Asia Close -> EU Open Catch-Up (Nikkei->DAX)
  2. EU-ACT-3   : BCE Rate Decision Momentum Drift (nouvelle approche)
  3. EU-FUT-1   : Eurostoxx 50 Trend Following (EMA cross + ADX)

Outputs:
  output/session_20260326/eu_phase2_p0_results.json
  output/session_20260326/trades_eu_p0_asia_catchup.csv
  output/session_20260326/trades_eu_p0_bce_momentum.csv
  output/session_20260326/trades_eu_p0_stoxx_trend.csv

Usage:
    python intraday-backtesterV2/run_eu_phase2.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# PATHS
# =============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
EU_CACHE = PROJECT_ROOT / "data_cache" / "eu"
OUTPUT_DIR = PROJECT_ROOT / "output" / "session_20260326"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# CONFIG
# =============================================================================
INITIAL_CAPITAL = 200_000  # Capital EU dédié
POSITION_SIZE = 0.10       # 10% du capital par trade


# =============================================================================
# HELPERS
# =============================================================================
def load_parquet(ticker: str) -> pd.DataFrame | None:
    """Charge un ticker depuis le cache EU parquet."""
    # Convert ticker to filename
    clean = ticker.replace(".", "_").replace("^", "").replace("=", "_")
    filename = f"{clean}_daily_5y.parquet"
    filepath = EU_CACHE / filename
    if not filepath.exists():
        print(f"  [WARN] Fichier manquant: {filepath}")
        return None
    df = pd.read_parquet(filepath)
    df.index = pd.to_datetime(df.index)
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def compute_metrics(trades_df: pd.DataFrame, capital: float = INITIAL_CAPITAL) -> dict:
    """Calcule Sharpe, WR, PF, max DD, etc. sur un DataFrame de trades."""
    if trades_df.empty:
        return {
            "n_trades": 0, "sharpe": 0, "win_rate": 0, "profit_factor": 0,
            "total_return_pct": 0, "net_pnl": 0, "max_dd_pct": 0,
            "avg_winner": 0, "avg_loser": 0, "best_trade": 0, "worst_trade": 0,
        }

    net_pnls = trades_df["net_pnl"]
    n_trades = len(trades_df)
    winners = net_pnls[net_pnls > 0]
    losers = net_pnls[net_pnls <= 0]

    win_rate = len(winners) / n_trades * 100 if n_trades > 0 else 0
    gross_profit = winners.sum() if len(winners) > 0 else 0
    gross_loss = abs(losers.sum()) if len(losers) > 0 else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_net = net_pnls.sum()
    total_return_pct = total_net / capital * 100

    # Equity curve from daily aggregation
    daily = trades_df.groupby("date")["net_pnl"].sum().sort_index()
    equity = capital + daily.cumsum()
    peak = equity.expanding().max()
    dd = (equity - peak) / peak * 100
    max_dd = abs(dd.min()) if len(dd) > 0 else 0

    # Sharpe (annualisé)
    daily_returns = daily / capital
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0

    return {
        "n_trades": n_trades,
        "sharpe": round(float(sharpe), 2),
        "win_rate": round(float(win_rate), 1),
        "profit_factor": round(float(profit_factor), 2),
        "total_return_pct": round(float(total_return_pct), 2),
        "net_pnl": round(float(total_net), 2),
        "max_dd_pct": round(float(max_dd), 2),
        "avg_winner": round(float(winners.mean()), 2) if len(winners) > 0 else 0,
        "avg_loser": round(float(losers.mean()), 2) if len(losers) > 0 else 0,
        "best_trade": round(float(net_pnls.max()), 2),
        "worst_trade": round(float(net_pnls.min()), 2),
    }


def walk_forward(trades_df: pd.DataFrame, is_days: int = 60, oos_days: int = 30,
                 capital: float = INITIAL_CAPITAL) -> dict | None:
    """Walk-forward validation. 60j IS / 30j OOS. Retourne None si < 30 trades."""
    if len(trades_df) < 30:
        return None

    trades_df = trades_df.copy()
    trades_df["date"] = pd.to_datetime(trades_df["date"])
    all_dates = sorted(trades_df["date"].unique())

    if len(all_dates) < is_days + oos_days:
        return None

    windows = []
    i = 0
    step = oos_days
    while i + is_days + oos_days <= len(all_dates):
        oos_start = all_dates[i + is_days]
        oos_end_idx = min(i + is_days + oos_days - 1, len(all_dates) - 1)
        oos_end = all_dates[oos_end_idx]

        oos_trades = trades_df[
            (trades_df["date"] >= oos_start) & (trades_df["date"] <= oos_end)
        ]

        if len(oos_trades) > 0:
            oos_net = oos_trades["net_pnl"].sum()
            oos_return = oos_net / capital * 100
            daily_pnl = oos_trades.groupby("date")["net_pnl"].sum()
            daily_ret = daily_pnl / capital
            if len(daily_ret) > 1 and daily_ret.std() > 0:
                oos_sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
            else:
                oos_sharpe = 0

            oos_win = oos_trades["net_pnl"]
            oos_w = oos_win[oos_win > 0]
            oos_l = oos_win[oos_win <= 0]
            oos_pf = (oos_w.sum() / abs(oos_l.sum())) if len(oos_l) > 0 and oos_l.sum() != 0 else float("inf")

            windows.append({
                "oos_start": str(oos_start.date()) if hasattr(oos_start, 'date') else str(oos_start)[:10],
                "oos_end": str(oos_end.date()) if hasattr(oos_end, 'date') else str(oos_end)[:10],
                "return_pct": round(float(oos_return), 2),
                "sharpe": round(float(oos_sharpe), 2),
                "pf": round(float(oos_pf), 2),
                "trades": len(oos_trades),
                "win_rate": round(len(oos_trades[oos_trades["net_pnl"] > 0]) / len(oos_trades) * 100, 1),
            })
        i += step

    if not windows:
        return None

    profitable = sum(1 for w in windows if w["return_pct"] > 0)
    hit_rate = profitable / len(windows) * 100
    avg_return = np.mean([w["return_pct"] for w in windows])
    avg_sharpe = np.mean([w["sharpe"] for w in windows])

    # Verdict: >= 60% windows profitable pour les P0
    verdict = "VALIDATED" if hit_rate >= 60 and avg_return > 0 else "REJECTED"

    return {
        "hit_rate": round(float(hit_rate), 1),
        "avg_return": round(float(avg_return), 2),
        "avg_sharpe": round(float(avg_sharpe), 2),
        "n_windows": len(windows),
        "profitable_windows": profitable,
        "verdict": verdict,
        "windows": windows,
    }


# =============================================================================
# STRATEGY 1: EU-CROSS-2 -- Asia Close -> EU Open Catch-Up
# =============================================================================
def backtest_asia_catchup() -> tuple[pd.DataFrame, dict]:
    """
    Nikkei clôture en hausse > 1% -> le lendemain, si DAX gap > 0.3%,
    on LONG DAX open-to-close (continuation).

    Filtres: gap EU > 1.5% = skip. SL 0.8%, TP 1.5%.
    Coûts: 0.10% + 0.03% slippage = 0.26% round-trip.
    """
    print("\n" + "=" * 70)
    print("  STRATEGY 1: EU-CROSS-2 -- Asia Close -> EU Open Catch-Up")
    print("=" * 70)

    nikkei = load_parquet("^N225")
    dax = load_parquet("^GDAXI")

    if nikkei is None or dax is None:
        print("  [ERROR] Donnees manquantes")
        return pd.DataFrame(), {}

    # Nikkei daily return
    nikkei["return"] = nikkei["Close"].pct_change()
    # DAX gap = open / prev close - 1
    dax["prev_close"] = dax["Close"].shift(1)
    dax["gap_pct"] = (dax["Open"] / dax["prev_close"] - 1) * 100
    # DAX intraday return = close / open - 1
    dax["intraday_return"] = (dax["Close"] / dax["Open"] - 1)

    # Cost params
    COST_RT = 0.0026  # 0.26% round-trip
    SL_PCT = 0.008    # 0.8%
    TP_PCT = 0.015    # 1.5%

    position_value = INITIAL_CAPITAL * POSITION_SIZE

    trades = []

    # Align dates: for each DAX trading day, check previous Nikkei close
    for i in range(1, len(dax)):
        dax_date = dax.index[i]
        dax_row = dax.iloc[i]

        # Find the most recent Nikkei trading day before this DAX date
        nikkei_before = nikkei[nikkei.index < dax_date]
        if nikkei_before.empty:
            continue

        nikkei_prev = nikkei_before.iloc[-1]
        nikkei_ret = nikkei_prev["return"]

        # Signal: Nikkei > +1%
        if pd.isna(nikkei_ret) or nikkei_ret <= 0.01:
            continue

        # Filter: DAX gap must be > 0.3% (continuation signal)
        gap = dax_row["gap_pct"]
        if pd.isna(gap) or gap <= 0.3:
            continue

        # Filter: skip if gap > 1.5% (too extended)
        if gap > 1.5:
            continue

        # Intraday return (open -> close)
        intra_ret = dax_row["intraday_return"]
        if pd.isna(intra_ret):
            continue

        # Apply SL/TP to intraday return
        # With daily data, we approximate: if low would have hit SL first
        entry_price = dax_row["Open"]
        low_pct = (dax_row["Low"] / entry_price - 1) if entry_price > 0 else 0
        high_pct = (dax_row["High"] / entry_price - 1) if entry_price > 0 else 0

        # Determine exit return
        if low_pct <= -SL_PCT:
            # SL hit (assume SL hit before TP -- conservative)
            exit_return = -SL_PCT
        elif high_pct >= TP_PCT:
            exit_return = TP_PCT
        else:
            exit_return = intra_ret  # EOD exit

        # P&L
        gross_pnl = position_value * exit_return
        cost = position_value * COST_RT
        net = gross_pnl - cost

        trades.append({
            "date": str(dax_date.date()) if hasattr(dax_date, 'date') else str(dax_date)[:10],
            "ticker": "^GDAXI",
            "direction": "LONG",
            "nikkei_return_pct": round(float(nikkei_ret * 100), 2),
            "dax_gap_pct": round(float(gap), 2),
            "entry_price": round(float(entry_price), 2),
            "exit_return_pct": round(float(exit_return * 100), 3),
            "gross_pnl": round(float(gross_pnl), 2),
            "commission": round(float(cost), 2),
            "net_pnl": round(float(net), 2),
        })

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["date"] = pd.to_datetime(trades_df["date"])

    metrics = compute_metrics(trades_df)
    wf = walk_forward(trades_df)

    print(f"  Trades: {metrics['n_trades']}")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Win Rate: {metrics['win_rate']:.1f}%")
    print(f"  Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"  Net P&L: ${metrics['net_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Total Return: {metrics['total_return_pct']:.2f}%")
    if wf:
        print(f"  Walk-Forward: {wf['verdict']} ({wf['hit_rate']:.0f}% hit rate, {wf['n_windows']} windows)")
    else:
        print(f"  Walk-Forward: N/A (< 30 trades)")

    return trades_df, {**metrics, "walk_forward": wf}


# =============================================================================
# STRATEGY 2: EU-ACT-3 -- BCE Rate Decision Momentum Drift (v2)
# =============================================================================
def backtest_bce_momentum() -> tuple[pd.DataFrame, dict]:
    """
    Nouvelle approche momentum : pas de classification hawk/dove.
    Jours BCE: si banques EU > +0.5% open-to-13:30 -> LONG rest of day.
              si banques EU < -0.5% -> SHORT rest of day.

    Proxy daily: bank return (open-to-close) les jours BCE.
    Si return > +0.5% open -> LONG, si < -0.5% -> SHORT.

    SL 1.5%, TP 3.0%.
    Coûts: 0.26% RT.
    """
    print("\n" + "=" * 70)
    print("  STRATEGY 2: EU-ACT-3 -- BCE Rate Decision Momentum Drift (v2)")
    print("=" * 70)

    bnp = load_parquet("BNP.PA")
    gle = load_parquet("GLE.PA")
    dbk = load_parquet("DBK.DE")

    if bnp is None or gle is None or dbk is None:
        print("  [ERROR] Donnees manquantes")
        return pd.DataFrame(), {}

    # BCE meeting dates (8 per year, 2021-2026)
    # Source: ECB official calendar
    bce_dates = [
        # 2021
        "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
        "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
        # 2022
        "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
        "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
        # 2023
        "2023-02-02", "2023-03-16", "2023-04-27" , "2023-06-15",
        "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
        # 2024
        "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
        "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
        # 2025
        "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
        "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
        # 2026
        "2026-01-22", "2026-03-05", "2026-03-26",
    ]
    bce_dates = pd.to_datetime(bce_dates)

    # For each bank, compute open-to-close return
    for df in [bnp, gle, dbk]:
        df["otc_return"] = (df["Close"] / df["Open"] - 1)

    COST_RT = 0.0026
    SL_PCT = 0.015
    TP_PCT = 0.030
    position_value = INITIAL_CAPITAL * POSITION_SIZE

    trades = []

    for bce_date in bce_dates:
        # Compute average bank return on this day
        returns = []
        prices = {}  # store for individual trade entries

        for ticker, df in [("BNP.PA", bnp), ("GLE.PA", gle), ("DBK.DE", dbk)]:
            if bce_date in df.index:
                ret = df.loc[bce_date, "otc_return"]
                if not pd.isna(ret):
                    returns.append(ret)
                    prices[ticker] = {
                        "open": float(df.loc[bce_date, "Open"]),
                        "close": float(df.loc[bce_date, "Close"]),
                        "high": float(df.loc[bce_date, "High"]),
                        "low": float(df.loc[bce_date, "Low"]),
                        "otc_return": float(ret),
                    }

        if not returns:
            continue

        avg_bank_return = np.mean(returns)

        # Momentum signal: need > 0.5% or < -0.5% to trade
        if abs(avg_bank_return) < 0.005:
            continue  # No clear direction

        direction = "LONG" if avg_bank_return > 0 else "SHORT"

        # Trade each bank individually for more granularity
        for ticker, info in prices.items():
            raw_return = info["otc_return"]

            # Align direction: if LONG, raw_return is our P&L direction
            # If SHORT, we invert
            if direction == "SHORT":
                effective_return = -raw_return
            else:
                effective_return = raw_return

            # Apply SL/TP with daily OHLC
            entry = info["open"]
            if entry <= 0:
                continue

            if direction == "LONG":
                low_move = (info["low"] / entry - 1)
                high_move = (info["high"] / entry - 1)
            else:  # SHORT
                low_move = -(info["high"] / entry - 1)  # worst for shorts
                high_move = -(info["low"] / entry - 1)   # best for shorts

            # Check SL/TP
            if low_move <= -SL_PCT:
                exit_return = -SL_PCT
            elif high_move >= TP_PCT:
                exit_return = TP_PCT
            else:
                exit_return = effective_return

            gross_pnl = position_value * exit_return
            cost = position_value * COST_RT
            net = gross_pnl - cost

            trades.append({
                "date": str(bce_date.date()),
                "ticker": ticker,
                "direction": direction,
                "bank_avg_return_pct": round(float(avg_bank_return * 100), 2),
                "individual_otc_pct": round(float(raw_return * 100), 2),
                "entry_price": round(float(entry), 2),
                "exit_return_pct": round(float(exit_return * 100), 3),
                "gross_pnl": round(float(gross_pnl), 2),
                "commission": round(float(cost), 2),
                "net_pnl": round(float(net), 2),
            })

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["date"] = pd.to_datetime(trades_df["date"])

    metrics = compute_metrics(trades_df)
    wf = walk_forward(trades_df)

    print(f"  BCE dates matched: {len(trades_df['date'].unique()) if not trades_df.empty else 0}")
    print(f"  Trades: {metrics['n_trades']}")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Win Rate: {metrics['win_rate']:.1f}%")
    print(f"  Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"  Net P&L: ${metrics['net_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Total Return: {metrics['total_return_pct']:.2f}%")
    if wf:
        print(f"  Walk-Forward: {wf['verdict']} ({wf['hit_rate']:.0f}% hit rate, {wf['n_windows']} windows)")
    else:
        print(f"  Walk-Forward: N/A (< 30 trades)")

    return trades_df, {**metrics, "walk_forward": wf}


# =============================================================================
# STRATEGY 3: EU-FUT-1 -- Eurostoxx 50 Trend Following
# =============================================================================
def backtest_stoxx_trend() -> tuple[pd.DataFrame, dict]:
    """
    Eurostoxx 50 trend following avec EMA(20)/EMA(50).

    Signal: prix > EMA(20) > EMA(50) -> LONG. Inverse -> SHORT.
    Filtre: ADX(14) < 15 -> skip (pas de trend).
    SL = 2.0 × ATR(14). TP = 3.0 × ATR(14) ou trailing stop.
    Holding: 1-10 jours (swing).
    Coûts FUTURES: 0.003% RT (très bas).
    """
    print("\n" + "=" * 70)
    print("  STRATEGY 3: EU-FUT-1 -- Eurostoxx 50 Trend Following")
    print("=" * 70)

    stoxx = load_parquet("^STOXX50E")
    if stoxx is None:
        print("  [ERROR] Donnees manquantes")
        return pd.DataFrame(), {}

    df = stoxx.copy()

    # Indicators
    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # ATR(14)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # ADX(14)
    plus_dm = df["High"].diff()
    minus_dm = -df["Low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    atr_smooth = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr_smooth)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    dx = dx.replace([np.inf, -np.inf], 0).fillna(0)
    df["adx14"] = dx.rolling(14).mean()

    COST_RT = 0.00003  # 0.003% for futures
    NOTIONAL = 50_000  # ~1 FESX contract
    SL_MULT = 2.0
    TP_MULT = 3.0
    MAX_HOLD = 10

    trades = []
    position = None  # {"direction", "entry_price", "entry_date", "sl", "tp", "trailing_high/low"}

    # Start after EMA50 warmup
    start_idx = 60

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        date = df.index[i]
        close = row["Close"]
        ema20 = row["ema20"]
        ema50 = row["ema50"]
        adx = row["adx14"]
        atr = row["atr14"]

        if pd.isna(ema20) or pd.isna(ema50) or pd.isna(adx) or pd.isna(atr) or atr == 0:
            continue

        # Check for exit if in position
        if position is not None:
            days_held = (date - position["entry_date"]).days
            entry = position["entry_price"]

            if position["direction"] == "LONG":
                # Update trailing stop (highest high since entry)
                position["trailing_high"] = max(position["trailing_high"], row["High"])
                trailing_sl = position["trailing_high"] - SL_MULT * position["entry_atr"]

                # Check SL (use Low of day)
                if row["Low"] <= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "SL"
                elif row["Low"] <= trailing_sl:
                    exit_price = trailing_sl
                    exit_reason = "TRAILING_SL"
                elif row["High"] >= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "TP"
                elif days_held >= MAX_HOLD:
                    exit_price = close
                    exit_reason = "MAX_HOLD"
                # Reverse signal: close position
                elif close < ema20 < ema50:
                    exit_price = close
                    exit_reason = "REVERSE"
                else:
                    continue  # Hold

                ret = (exit_price - entry) / entry
                gross = NOTIONAL * ret
                cost = NOTIONAL * COST_RT
                net = gross - cost

                trades.append({
                    "date": str(position["entry_date"].date()) if hasattr(position["entry_date"], 'date') else str(position["entry_date"])[:10],
                    "exit_date": str(date.date()) if hasattr(date, 'date') else str(date)[:10],
                    "ticker": "^STOXX50E",
                    "direction": "LONG",
                    "entry_price": round(float(entry), 2),
                    "exit_price": round(float(exit_price), 2),
                    "exit_reason": exit_reason,
                    "days_held": days_held,
                    "exit_return_pct": round(float(ret * 100), 3),
                    "gross_pnl": round(float(gross), 2),
                    "commission": round(float(cost), 2),
                    "net_pnl": round(float(net), 2),
                })
                position = None

            elif position["direction"] == "SHORT":
                position["trailing_low"] = min(position["trailing_low"], row["Low"])
                trailing_sl = position["trailing_low"] + SL_MULT * position["entry_atr"]

                if row["High"] >= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "SL"
                elif row["High"] >= trailing_sl:
                    exit_price = trailing_sl
                    exit_reason = "TRAILING_SL"
                elif row["Low"] <= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "TP"
                elif days_held >= MAX_HOLD:
                    exit_price = close
                    exit_reason = "MAX_HOLD"
                elif close > ema20 > ema50:
                    exit_price = close
                    exit_reason = "REVERSE"
                else:
                    continue

                ret = (entry - exit_price) / entry
                gross = NOTIONAL * ret
                cost = NOTIONAL * COST_RT
                net = gross - cost

                trades.append({
                    "date": str(position["entry_date"].date()) if hasattr(position["entry_date"], 'date') else str(position["entry_date"])[:10],
                    "exit_date": str(date.date()) if hasattr(date, 'date') else str(date)[:10],
                    "ticker": "^STOXX50E",
                    "direction": "SHORT",
                    "entry_price": round(float(entry), 2),
                    "exit_price": round(float(exit_price), 2),
                    "exit_reason": exit_reason,
                    "days_held": days_held,
                    "exit_return_pct": round(float(ret * 100), 3),
                    "gross_pnl": round(float(gross), 2),
                    "commission": round(float(cost), 2),
                    "net_pnl": round(float(net), 2),
                })
                position = None

        # Check for new entry (only if flat)
        if position is None:
            # ADX filter
            if adx < 15:
                continue

            if close > ema20 > ema50:
                # LONG signal
                sl = close - SL_MULT * atr
                tp = close + TP_MULT * atr
                position = {
                    "direction": "LONG",
                    "entry_price": close,
                    "entry_date": date,
                    "entry_atr": atr,
                    "sl": sl,
                    "tp": tp,
                    "trailing_high": row["High"],
                }

            elif close < ema20 < ema50:
                # SHORT signal
                sl = close + SL_MULT * atr
                tp = close - TP_MULT * atr
                position = {
                    "direction": "SHORT",
                    "entry_price": close,
                    "entry_date": date,
                    "entry_atr": atr,
                    "sl": sl,
                    "tp": tp,
                    "trailing_low": row["Low"],
                }

    # Close any open position at end
    if position is not None:
        last = df.iloc[-1]
        date = df.index[-1]
        entry = position["entry_price"]

        if position["direction"] == "LONG":
            ret = (last["Close"] - entry) / entry
        else:
            ret = (entry - last["Close"]) / entry

        gross = NOTIONAL * ret
        cost = NOTIONAL * COST_RT
        net = gross - cost

        trades.append({
            "date": str(position["entry_date"].date()) if hasattr(position["entry_date"], 'date') else str(position["entry_date"])[:10],
            "exit_date": str(date.date()) if hasattr(date, 'date') else str(date)[:10],
            "ticker": "^STOXX50E",
            "direction": position["direction"],
            "entry_price": round(float(entry), 2),
            "exit_price": round(float(last["Close"]), 2),
            "exit_reason": "EOD_FINAL",
            "days_held": (date - position["entry_date"]).days,
            "exit_return_pct": round(float(ret * 100), 3),
            "gross_pnl": round(float(gross), 2),
            "commission": round(float(cost), 2),
            "net_pnl": round(float(net), 2),
        })

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["date"] = pd.to_datetime(trades_df["date"])

    metrics = compute_metrics(trades_df)
    wf = walk_forward(trades_df)

    # Exit reason distribution
    if not trades_df.empty and "exit_reason" in trades_df.columns:
        print(f"\n  Exit reasons:")
        for reason, count in trades_df["exit_reason"].value_counts().items():
            sub = trades_df[trades_df["exit_reason"] == reason]
            print(f"    {reason:15s}: {count:3d} trades, avg net={sub['net_pnl'].mean():>8.2f}")

    print(f"\n  Trades: {metrics['n_trades']}")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Win Rate: {metrics['win_rate']:.1f}%")
    print(f"  Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"  Net P&L: ${metrics['net_pnl']:,.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")
    print(f"  Total Return: {metrics['total_return_pct']:.2f}%")
    if wf:
        print(f"  Walk-Forward: {wf['verdict']} ({wf['hit_rate']:.0f}% hit rate, {wf['n_windows']} windows)")
    else:
        print(f"  Walk-Forward: N/A (< 30 trades)")

    return trades_df, {**metrics, "walk_forward": wf}


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("=" * 70)
    print("  EU PHASE 2 -- BACKTEST 3 STRATEGIES P0")
    print(f"  Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"  Data source: {EU_CACHE}")
    print(f"  Output: {OUTPUT_DIR}")
    print("=" * 70)

    results = {}

    # 1. Asia Catch-Up
    trades_1, metrics_1 = backtest_asia_catchup()
    results["EU-CROSS-2 Asia Catch-Up"] = {
        "strategy": "EU-CROSS-2 Asia Catch-Up",
        "description": "Nikkei > 1% close -> DAX continuation next open. SL 0.8%, TP 1.5%.",
        **metrics_1,
    }
    if not trades_1.empty:
        csv_path = OUTPUT_DIR / "trades_eu_p0_asia_catchup.csv"
        trades_1.to_csv(csv_path, index=False)
        print(f"  [CSV] {csv_path}")

    # 2. BCE Momentum
    trades_2, metrics_2 = backtest_bce_momentum()

    # Yearly walk-forward for event-driven BCE strategy
    bce_wf = None
    if not trades_2.empty:
        trades_2_copy = trades_2.copy()
        trades_2_copy["date"] = pd.to_datetime(trades_2_copy["date"])
        trades_2_copy["year"] = trades_2_copy["date"].dt.year
        years = sorted(trades_2_copy["year"].unique())

        if len(years) >= 3:
            # Leave-one-year-out validation
            bce_windows = []
            for yr in years:
                oos = trades_2_copy[trades_2_copy["year"] == yr]
                if oos.empty:
                    continue
                oos_net = oos["net_pnl"].sum()
                oos_ret = oos_net / INITIAL_CAPITAL * 100
                n = len(oos)
                wr = len(oos[oos["net_pnl"] > 0]) / n * 100 if n > 0 else 0
                bce_windows.append({
                    "oos_start": f"{yr}-01-01",
                    "oos_end": f"{yr}-12-31",
                    "return_pct": round(float(oos_ret), 2),
                    "sharpe": 0,  # not meaningful for single-year events
                    "pf": round(float(oos[oos["net_pnl"] > 0]["net_pnl"].sum() /
                                      max(abs(oos[oos["net_pnl"] <= 0]["net_pnl"].sum()), 1e-9)), 2),
                    "trades": n,
                    "win_rate": round(float(wr), 1),
                })

            profitable_yrs = sum(1 for w in bce_windows if w["return_pct"] > 0)
            hit_rate = profitable_yrs / len(bce_windows) * 100 if bce_windows else 0
            avg_ret = np.mean([w["return_pct"] for w in bce_windows]) if bce_windows else 0

            bce_wf = {
                "method": "yearly_leave_one_out",
                "hit_rate": round(float(hit_rate), 1),
                "avg_return": round(float(avg_ret), 2),
                "avg_sharpe": 0,
                "n_windows": len(bce_windows),
                "profitable_windows": profitable_yrs,
                "verdict": "VALIDATED" if hit_rate >= 60 and avg_ret > 0 else "REJECTED",
                "windows": bce_windows,
            }
            print(f"  BCE Yearly WF: {bce_wf['verdict']} ({hit_rate:.0f}% hit, {len(bce_windows)} years)")

    results["EU-ACT-3 BCE Momentum Drift v2"] = {
        "strategy": "EU-ACT-3 BCE Momentum Drift v2",
        "description": "BCE day: if banks > +0.5% -> LONG, < -0.5% -> SHORT. Momentum approach. SL 1.5%, TP 3.0%.",
        **metrics_2,
    }
    if bce_wf:
        results["EU-ACT-3 BCE Momentum Drift v2"]["walk_forward"] = bce_wf

    if not trades_2.empty:
        csv_path = OUTPUT_DIR / "trades_eu_p0_bce_momentum.csv"
        trades_2.to_csv(csv_path, index=False)
        print(f"  [CSV] {csv_path}")

    # 3. Eurostoxx Trend
    trades_3, metrics_3 = backtest_stoxx_trend()
    results["EU-FUT-1 Eurostoxx Trend Following"] = {
        "strategy": "EU-FUT-1 Eurostoxx Trend Following",
        "description": "EMA(20)/EMA(50) cross + ADX > 15 filter. SL/TP 2x/3x ATR(14). Futures costs 0.003%.",
        **metrics_3,
    }
    if not trades_3.empty:
        csv_path = OUTPUT_DIR / "trades_eu_p0_stoxx_trend.csv"
        trades_3.to_csv(csv_path, index=False)
        print(f"  [CSV] {csv_path}")

    # Save consolidated JSON
    # Remove non-serializable objects
    for key in results:
        if "walk_forward" in results[key] and results[key]["walk_forward"] is None:
            results[key]["walk_forward"] = "N/A (< 30 trades)"

    json_path = OUTPUT_DIR / "eu_phase2_p0_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  [JSON] {json_path}")

    # Summary table
    print("\n\n" + "=" * 70)
    print("  EU PHASE 2 -- SUMMARY")
    print("=" * 70)
    print(f"  {'Strategy':<40s} {'Sharpe':>7s} {'WR':>6s} {'PF':>6s} {'Trades':>7s} {'Return':>8s} {'MaxDD':>7s} {'WF':>10s}")
    print("  " + "-" * 95)

    for name, m in results.items():
        wf_str = "N/A"
        if isinstance(m.get("walk_forward"), dict):
            wf_str = m["walk_forward"]["verdict"]
        print(f"  {name:<40s} {m['sharpe']:>7.2f} {m['win_rate']:>5.1f}% "
              f"{m['profit_factor']:>6.2f} {m['n_trades']:>7d} "
              f"{m['total_return_pct']:>7.2f}% {m['max_dd_pct']:>6.2f}% "
              f"{wf_str:>10s}")

    # Verdict
    print("\n  VERDICTS:")
    for name, m in results.items():
        sharpe = m["sharpe"]
        wf = m.get("walk_forward")
        wf_ok = isinstance(wf, dict) and wf["verdict"] == "VALIDATED"

        if sharpe > 1.0 and wf_ok:
            verdict = "WINNER + WF VALIDATED -> Deploy candidate"
        elif sharpe > 1.0:
            verdict = "WINNER (no WF yet) -> Monitor"
        elif sharpe > 0:
            verdict = "MARGINAL -> Needs optimization"
        else:
            verdict = "REJECTED -> Negative edge"

        print(f"    {name}: {verdict}")


if __name__ == "__main__":
    main()
