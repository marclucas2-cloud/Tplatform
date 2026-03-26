"""
EU Phase 2 — P1+P2 Strategies Backtest Runner
7 strategies EU event-driven, cross-asset, forex.

Strategies :
  P1-1 : ASML Earnings Chain (ASML.AS -> IFX.DE sympathy)
  P1-2 : Luxury Momentum China Signal (^HSI -> MC.PA)
  P1-3 : EUR/USD Trend Following (EMA crossover + ADX filter)
  P1-4 : EU Close -> US Afternoon Signal (^GDAXI -> SPY)
  P2-1 : Auto Sector German Sympathy (BMW/MBG/VOW3 catch-up)
  P2-2 : EUR/GBP Mean Reversion (z-score SMA60)
  P2-3 : EUR/JPY Carry + Momentum (carry + EMA trend)

Usage :
    python run_eu_phase2_p1p2.py
    python run_eu_phase2_p1p2.py --strategy p1_1
    python run_eu_phase2_p1p2.py --strategy p2_2

Outputs :
    output/session_20260326/eu_phase2_p1p2_results.json
    output/session_20260326/trades_eu_p1_1_asml_chain.csv
    ...
"""

import sys
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Paths ──
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

OUTPUT_DIR = SCRIPT_DIR.parent / "output" / "session_20260326"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Costs ──
EU_EQUITY_COST_RT = 0.0026      # 0.13% per leg -> 0.26% round-trip
FX_COST_RT = 0.00005            # 0.005% round-trip
US_COST_PER_SHARE = 0.005       # Alpaca US

# ── Validation ──
VALIDATION = {"sharpe_min": 0.5, "pf_min": 1.2, "trades_min": 15, "dd_max": 10.0}

INITIAL_CAPITAL = 100_000


# ═══════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════

def fetch_yf(tickers: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
    """Download daily data via yfinance (in-memory, no cache)."""
    import yfinance as yf

    data = {}
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if df.empty:
                print(f"  [WARN] {ticker}: no data")
                continue
            df.columns = [c.lower() for c in df.columns]
            # Ensure tz-naive index for alignment
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            data[ticker] = df
            print(f"  [YF] {ticker}: {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()})")
        except Exception as e:
            print(f"  [WARN] {ticker}: {e}")
    return data


# ═══════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════

def calc_metrics(trades_df: pd.DataFrame, capital: float = INITIAL_CAPITAL) -> dict:
    """Calculate Sharpe, WR, PF, max DD, trade count from a trades DataFrame."""
    if trades_df.empty or len(trades_df) == 0:
        return {
            "total_return_pct": 0, "net_pnl": 0, "n_trades": 0,
            "win_rate": 0, "profit_factor": 0, "sharpe_ratio": 0,
            "max_drawdown_pct": 0, "avg_winner": 0, "avg_loser": 0,
            "best_trade": 0, "worst_trade": 0, "avg_rr_ratio": 0,
            "total_commission": 0,
        }

    pnl_col = "net_pnl"
    if pnl_col not in trades_df.columns:
        trades_df = trades_df.copy()
        comm = trades_df["commission"] if "commission" in trades_df.columns else 0
        trades_df["net_pnl"] = trades_df["pnl"] - comm

    n = len(trades_df)
    net_pnl = trades_df[pnl_col].sum()
    total_comm = trades_df["commission"].sum() if "commission" in trades_df.columns else 0

    winners = trades_df[trades_df[pnl_col] > 0]
    losers = trades_df[trades_df[pnl_col] <= 0]

    wr = len(winners) / n * 100 if n > 0 else 0
    avg_w = winners[pnl_col].mean() if len(winners) > 0 else 0
    avg_l = losers[pnl_col].mean() if len(losers) > 0 else 0

    gross_profit = winners[pnl_col].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers[pnl_col].sum()) if len(losers) > 0 else 1
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve for Sharpe & DD
    date_col = "date" if "date" in trades_df.columns else "entry_date"
    daily_pnl = trades_df.groupby(date_col)[pnl_col].sum().sort_index()
    equity = capital + daily_pnl.cumsum()

    peak = equity.expanding().max()
    dd = (equity - peak) / peak * 100
    max_dd = abs(dd.min()) if len(dd) > 0 else 0

    daily_ret = equity.pct_change().dropna()
    sharpe = (
        daily_ret.mean() / daily_ret.std() * np.sqrt(252)
        if len(daily_ret) > 1 and daily_ret.std() > 0
        else 0
    )

    return {
        "total_return_pct": round(float(net_pnl / capital * 100), 2),
        "net_pnl": round(float(net_pnl), 2),
        "n_trades": n,
        "win_rate": round(float(wr), 1),
        "profit_factor": round(float(pf), 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown_pct": round(float(max_dd), 2),
        "avg_winner": round(float(avg_w), 2),
        "avg_loser": round(float(avg_l), 2),
        "best_trade": round(float(trades_df[pnl_col].max()), 2),
        "worst_trade": round(float(trades_df[pnl_col].min()), 2),
        "avg_rr_ratio": round(abs(float(avg_w) / float(avg_l)), 2) if avg_l != 0 else 0,
        "total_commission": round(float(total_comm), 2),
    }


def evaluate_verdict(m: dict) -> str:
    s, pf, n, dd = m["sharpe_ratio"], m["profit_factor"], m["n_trades"], m["max_drawdown_pct"]
    if s >= VALIDATION["sharpe_min"] and pf >= VALIDATION["pf_min"] and n >= VALIDATION["trades_min"] and dd < VALIDATION["dd_max"] and m["net_pnl"] > 0:
        return "WINNER"
    elif s > 0 and m["net_pnl"] > 0:
        return "POTENTIEL"
    return "REJETE"


def print_result(name: str, m: dict):
    v = evaluate_verdict(m)
    tag = {"WINNER": "[***]", "POTENTIEL": "[ * ]", "REJETE": "[ X ]"}[v]
    print(f"\n{'='*65}")
    print(f"  {tag} {name}  ->  {v}")
    print(f"{'='*65}")
    print(f"  Trades:           {m['n_trades']:>8d}")
    print(f"  Total Return:     {m['total_return_pct']:>8.2f}%")
    print(f"  Net P&L:          ${m['net_pnl']:>10,.2f}")
    print(f"  Win Rate:         {m['win_rate']:>8.1f}%")
    print(f"  Profit Factor:    {m['profit_factor']:>8.2f}")
    print(f"  Sharpe Ratio:     {m['sharpe_ratio']:>8.2f}")
    print(f"  Max Drawdown:     {m['max_drawdown_pct']:>8.2f}%")
    print(f"  Avg Winner:       ${m['avg_winner']:>10,.2f}")
    print(f"  Avg Loser:        ${m['avg_loser']:>10,.2f}")
    print(f"  R:R Ratio:        {m['avg_rr_ratio']:>8.2f}")
    print(f"  Commission:       ${m['total_commission']:>10,.2f}")
    print(f"{'='*65}")


# ═══════════════════════════════════════════════════════════════════
# P1-1 : ASML Earnings Chain
# ═══════════════════════════════════════════════════════════════════

def run_p1_1_asml_chain() -> tuple[dict, pd.DataFrame]:
    """
    ASML earnings-like events (|return| > 3% + volume > 3x avg)
    trigger sympathy trade on IFX.DE same day + J+1.
    """
    print("\n" + "="*65)
    print("[P1-1] ASML Earnings Chain (ASML.AS -> IFX.DE)")
    print("="*65)

    data = fetch_yf(["ASML.AS", "IFX.DE"], period="5y")
    if "ASML.AS" not in data or "IFX.DE" not in data:
        print("  [ERROR] Missing data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    asml = data["ASML.AS"].copy()
    ifx = data["IFX.DE"].copy()

    # Daily returns
    asml["ret"] = asml["close"].pct_change()
    asml["vol_avg"] = asml["volume"].rolling(60).mean()

    ifx["ret"] = ifx["close"].pct_change()
    ifx["ret_oc"] = (ifx["close"] - ifx["open"]) / ifx["open"]  # open-to-close

    # Align dates
    common_dates = asml.index.intersection(ifx.index)
    asml = asml.loc[common_dates]
    ifx = ifx.loc[common_dates]

    trades = []
    position_size = INITIAL_CAPITAL * 0.10  # 10% per trade

    for i in range(61, len(asml)):
        date = asml.index[i]
        asml_ret = asml["ret"].iloc[i]
        asml_vol = asml["volume"].iloc[i]
        asml_vol_avg = asml["vol_avg"].iloc[i]

        if pd.isna(asml_ret) or pd.isna(asml_vol_avg) or asml_vol_avg == 0:
            continue

        # Earnings-like event: |return| > 3% AND volume > 3x average
        if abs(asml_ret) < 0.03 or asml_vol < 3 * asml_vol_avg:
            continue

        direction = "LONG" if asml_ret > 0.03 else "SHORT"
        ifx_ret_d0 = ifx["ret_oc"].iloc[i]

        if pd.isna(ifx_ret_d0):
            continue

        # Same-day sympathy trade
        trade_ret = ifx_ret_d0 if direction == "LONG" else -ifx_ret_d0
        gross_pnl = position_size * trade_ret
        commission = position_size * EU_EQUITY_COST_RT
        net = gross_pnl - commission

        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "symbol": "IFX.DE",
            "direction": direction,
            "trigger": f"ASML {asml_ret:+.1%}",
            "asml_return": round(asml_ret * 100, 2),
            "ifx_return_d0": round(ifx_ret_d0 * 100, 2),
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(net, 2),
        })

        # J+1 continuation trade (if available)
        if i + 1 < len(ifx):
            ifx_ret_d1 = ifx["ret_oc"].iloc[i + 1]
            if not pd.isna(ifx_ret_d1):
                trade_ret_d1 = ifx_ret_d1 if direction == "LONG" else -ifx_ret_d1
                gross_d1 = position_size * trade_ret_d1
                comm_d1 = position_size * EU_EQUITY_COST_RT
                net_d1 = gross_d1 - comm_d1

                trades.append({
                    "date": ifx.index[i + 1].strftime("%Y-%m-%d"),
                    "symbol": "IFX.DE",
                    "direction": direction,
                    "trigger": f"ASML J+1 ({asml_ret:+.1%})",
                    "asml_return": round(asml_ret * 100, 2),
                    "ifx_return_d0": round(ifx_ret_d1 * 100, 2),
                    "pnl": round(gross_d1, 2),
                    "commission": round(comm_d1, 2),
                    "net_pnl": round(net_d1, 2),
                })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P1-1 ASML Earnings Chain", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P1-2 : Luxury Momentum China Signal
# ═══════════════════════════════════════════════════════════════════

def run_p1_2_luxury_china() -> tuple[dict, pd.DataFrame]:
    """
    Hang Seng up > 1% yesterday -> LONG LVMH (MC.PA) next day open-to-close.
    """
    print("\n" + "="*65)
    print("[P1-2] Luxury Momentum China Signal (^HSI -> MC.PA)")
    print("="*65)

    data = fetch_yf(["MC.PA", "^HSI"], period="5y")
    if "MC.PA" not in data or "^HSI" not in data:
        print("  [ERROR] Missing data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    lvmh = data["MC.PA"].copy()
    hsi = data["^HSI"].copy()

    hsi["ret"] = hsi["close"].pct_change()
    lvmh["ret_oc"] = (lvmh["close"] - lvmh["open"]) / lvmh["open"]

    trades = []
    position_size = INITIAL_CAPITAL * 0.10

    # Build a mapping: for each LVMH trading day, find the most recent HSI return
    hsi_dates = hsi.index
    lvmh_dates = lvmh.index

    for i in range(1, len(lvmh_dates)):
        date = lvmh_dates[i]
        prev_date = lvmh_dates[i - 1]

        # Find HSI data for date <= prev_date (HSI trades in Asian hours before EU open)
        # We look at the most recent HSI day on or before prev_date
        hsi_mask = hsi_dates <= prev_date
        if hsi_mask.sum() == 0:
            continue

        hsi_latest = hsi_dates[hsi_mask][-1]
        hsi_ret = hsi.loc[hsi_latest, "ret"]

        if pd.isna(hsi_ret):
            continue

        # Signal: HSI up > 1%
        if hsi_ret <= 0.01:
            continue

        lvmh_ret = lvmh["ret_oc"].iloc[i]
        if pd.isna(lvmh_ret):
            continue

        gross_pnl = position_size * lvmh_ret
        commission = position_size * EU_EQUITY_COST_RT
        net = gross_pnl - commission

        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "symbol": "MC.PA",
            "direction": "LONG",
            "trigger": f"HSI {hsi_ret:+.1%}",
            "hsi_return": round(hsi_ret * 100, 2),
            "lvmh_return_oc": round(lvmh_ret * 100, 2),
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(net, 2),
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P1-2 Luxury Momentum China Signal", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P1-3 : EUR/USD Trend Following
# ═══════════════════════════════════════════════════════════════════

def run_p1_3_eurusd_trend() -> tuple[dict, pd.DataFrame]:
    """
    EMA(20) > EMA(50) = LONG, inverse = SHORT.
    SL = 2x ATR(14), trailing TP = 1.5x ATR.
    ADX < 15 = skip (no trend).
    """
    print("\n" + "="*65)
    print("[P1-3] EUR/USD Trend Following")
    print("="*65)

    data = fetch_yf(["EURUSD=X"], period="5y")
    if "EURUSD=X" not in data:
        print("  [ERROR] Missing EURUSD=X data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    df = data["EURUSD=X"].copy()

    # Indicators
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

    # ADX(14)
    df["dm_plus"] = np.where(
        (df["high"] - df["high"].shift(1)) > (df["low"].shift(1) - df["low"]),
        np.maximum(df["high"] - df["high"].shift(1), 0), 0
    )
    df["dm_minus"] = np.where(
        (df["low"].shift(1) - df["low"]) > (df["high"] - df["high"].shift(1)),
        np.maximum(df["low"].shift(1) - df["low"], 0), 0
    )
    df["di_plus"] = 100 * (pd.Series(df["dm_plus"]).ewm(span=14, adjust=False).mean() / df["atr14"])
    df["di_minus"] = 100 * (pd.Series(df["dm_minus"]).ewm(span=14, adjust=False).mean() / df["atr14"])
    dx = 100 * abs(df["di_plus"] - df["di_minus"]) / (df["di_plus"] + df["di_minus"])
    df["adx"] = dx.ewm(span=14, adjust=False).mean()

    trades = []
    position = None  # {"direction", "entry", "sl", "trailing_high/low", "atr", "date"}
    position_size = INITIAL_CAPITAL * 0.05  # 5% per FX trade (leveraged market)

    for i in range(60, len(df)):
        date = df.index[i]
        price = df["close"].iloc[i]
        atr = df["atr14"].iloc[i]
        adx = df["adx"].iloc[i]
        ema20 = df["ema20"].iloc[i]
        ema50 = df["ema50"].iloc[i]

        if pd.isna(atr) or pd.isna(adx) or atr == 0:
            continue

        # Check exit first
        if position is not None:
            if position["direction"] == "LONG":
                # Update trailing stop
                position["trailing_high"] = max(position["trailing_high"], price)
                trailing_stop = position["trailing_high"] - 1.5 * position["atr"]
                sl_hit = price <= position["sl"]
                tp_hit = price <= trailing_stop and price > position["entry"]

                if sl_hit or tp_hit:
                    exit_price = position["sl"] if sl_hit else trailing_stop
                    ret = (exit_price - position["entry"]) / position["entry"]
                    gross_pnl = position_size * ret
                    commission = position_size * FX_COST_RT
                    trades.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "entry_date": position["date"],
                        "symbol": "EURUSD",
                        "direction": "LONG",
                        "entry_price": round(position["entry"], 5),
                        "exit_price": round(exit_price, 5),
                        "exit_reason": "SL" if sl_hit else "TRAILING",
                        "pnl": round(gross_pnl, 2),
                        "commission": round(commission, 2),
                        "net_pnl": round(gross_pnl - commission, 2),
                    })
                    position = None

            elif position["direction"] == "SHORT":
                position["trailing_low"] = min(position["trailing_low"], price)
                trailing_stop = position["trailing_low"] + 1.5 * position["atr"]
                sl_hit = price >= position["sl"]
                tp_hit = price >= trailing_stop and price < position["entry"]

                if sl_hit or tp_hit:
                    exit_price = position["sl"] if sl_hit else trailing_stop
                    ret = (position["entry"] - exit_price) / position["entry"]
                    gross_pnl = position_size * ret
                    commission = position_size * FX_COST_RT
                    trades.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "entry_date": position["date"],
                        "symbol": "EURUSD",
                        "direction": "SHORT",
                        "entry_price": round(position["entry"], 5),
                        "exit_price": round(exit_price, 5),
                        "exit_reason": "SL" if sl_hit else "TRAILING",
                        "pnl": round(gross_pnl, 2),
                        "commission": round(commission, 2),
                        "net_pnl": round(gross_pnl - commission, 2),
                    })
                    position = None

        # Entry signals (only if no position)
        if position is None and adx >= 15:
            if price > ema20 and ema20 > ema50:
                # LONG
                position = {
                    "direction": "LONG",
                    "entry": price,
                    "sl": price - 2 * atr,
                    "atr": atr,
                    "trailing_high": price,
                    "date": date.strftime("%Y-%m-%d"),
                }
            elif price < ema20 and ema20 < ema50:
                # SHORT
                position = {
                    "direction": "SHORT",
                    "entry": price,
                    "sl": price + 2 * atr,
                    "atr": atr,
                    "trailing_low": price,
                    "date": date.strftime("%Y-%m-%d"),
                }

    # Close any open position at end
    if position is not None:
        price = df["close"].iloc[-1]
        date = df.index[-1]
        if position["direction"] == "LONG":
            ret = (price - position["entry"]) / position["entry"]
        else:
            ret = (position["entry"] - price) / position["entry"]
        gross_pnl = position_size * ret
        commission = position_size * FX_COST_RT
        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "entry_date": position["date"],
            "symbol": "EURUSD",
            "direction": position["direction"],
            "entry_price": round(position["entry"], 5),
            "exit_price": round(price, 5),
            "exit_reason": "EOD",
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(gross_pnl - commission, 2),
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    # Use entry_date as grouping for daily PnL
    if "entry_date" in trades_df.columns:
        trades_df["date"] = trades_df["date"]  # exit date for PnL attribution

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P1-3 EUR/USD Trend Following", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P1-4 : EU Close -> US Afternoon Signal
# ═══════════════════════════════════════════════════════════════════

def run_p1_4_eu_close_us() -> tuple[dict, pd.DataFrame]:
    """
    DAX return (open->close) > 1% -> SPY same day return as proxy.
    Filter: skip if DAX and SPY both > 0.5% same direction (already priced in).
    """
    print("\n" + "="*65)
    print("[P1-4] EU Close -> US Afternoon Signal (^GDAXI -> SPY)")
    print("="*65)

    data = fetch_yf(["^GDAXI", "SPY"], period="5y")
    if "^GDAXI" not in data or "SPY" not in data:
        print("  [ERROR] Missing data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    dax = data["^GDAXI"].copy()
    spy = data["SPY"].copy()

    dax["ret_oc"] = (dax["close"] - dax["open"]) / dax["open"]
    spy["ret_oc"] = (spy["close"] - spy["open"]) / spy["open"]
    # Afternoon proxy: close vs midday. Use close-to-close as daily proxy fallback.
    spy["ret_daily"] = spy["close"].pct_change()

    common = dax.index.intersection(spy.index)
    dax = dax.loc[common]
    spy = spy.loc[common]

    trades = []
    # Position size: US stock, use $ amount
    position_value = INITIAL_CAPITAL * 0.10
    spy_price_approx = 500  # approximate SPY price for share calc

    for i in range(1, len(common)):
        date = common[i]
        dax_ret = dax["ret_oc"].iloc[i]
        spy_ret = spy["ret_oc"].iloc[i]

        if pd.isna(dax_ret) or pd.isna(spy_ret):
            continue

        # Signal: DAX move > 1%
        if abs(dax_ret) < 0.01:
            continue

        # Filter: if both already moving same direction > 0.5%, skip (priced in)
        if dax_ret > 0.005 and spy_ret > 0.005:
            continue
        if dax_ret < -0.005 and spy_ret < -0.005:
            continue

        # Trade direction follows DAX
        direction = "LONG" if dax_ret > 0.01 else "SHORT"

        # SPY afternoon return proxy = remaining daily return
        # Since we can't get intraday from yfinance 5y, use daily return as proxy
        # but discount by 50% since morning is already priced
        afternoon_ret = spy["ret_daily"].iloc[i] * 0.5 if not pd.isna(spy["ret_daily"].iloc[i]) else 0

        if direction == "LONG":
            trade_ret = afternoon_ret
        else:
            trade_ret = -afternoon_ret

        # US cost: $0.005/share
        shares = int(position_value / spy_price_approx)
        commission = shares * US_COST_PER_SHARE * 2  # round-trip
        gross_pnl = position_value * trade_ret
        net = gross_pnl - commission

        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "symbol": "SPY",
            "direction": direction,
            "trigger": f"DAX {dax_ret:+.1%}",
            "dax_return": round(dax_ret * 100, 2),
            "spy_afternoon_proxy": round(afternoon_ret * 100, 2),
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(net, 2),
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P1-4 EU Close -> US Afternoon Signal", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P2-1 : Auto Sector German Sympathy
# ═══════════════════════════════════════════════════════════════════

def run_p2_1_auto_sympathy() -> tuple[dict, pd.DataFrame]:
    """
    When one of BMW/MBG/VOW3 moves > 2%, buy the laggard(s) that
    haven't caught up (return < 0.8%).
    """
    print("\n" + "="*65)
    print("[P2-1] Auto Sector German Sympathy (BMW/MBG/VOW3)")
    print("="*65)

    tickers = ["BMW.DE", "MBG.DE", "VOW3.DE"]
    data = fetch_yf(tickers, period="5y")
    if len(data) < 3:
        print(f"  [ERROR] Only got {len(data)}/3 tickers")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    # Align all to common dates
    common = data[tickers[0]].index
    for t in tickers[1:]:
        common = common.intersection(data[t].index)

    for t in tickers:
        data[t] = data[t].loc[common].copy()
        data[t]["ret_oc"] = (data[t]["close"] - data[t]["open"]) / data[t]["open"]

    trades = []
    position_size = INITIAL_CAPITAL * 0.10

    for i in range(1, len(common)):
        date = common[i]
        rets = {t: data[t]["ret_oc"].iloc[i] for t in tickers}

        # Skip if any NaN
        if any(pd.isna(v) for v in rets.values()):
            continue

        # Find leader(s) with |return| > 2%
        leaders = [t for t, r in rets.items() if abs(r) > 0.02]
        if not leaders:
            continue

        # Direction from the strongest leader
        leader = max(leaders, key=lambda t: abs(rets[t]))
        leader_ret = rets[leader]
        direction = "LONG" if leader_ret > 0 else "SHORT"

        # Find laggards: same-sign return < 0.8% (haven't caught up)
        laggards = []
        for t in tickers:
            if t == leader:
                continue
            r = rets[t]
            if direction == "LONG" and 0 <= r < 0.008:
                laggards.append(t)
            elif direction == "SHORT" and -0.008 < r <= 0:
                laggards.append(t)

        if not laggards:
            continue

        # Trade the laggards
        for lag in laggards:
            lag_ret = rets[lag]
            # Edge: laggard should catch up to ~50% of leader move (conservative estimate)
            # We measure the actual same-day return as the trade PnL
            if direction == "LONG":
                trade_ret = lag_ret  # already positive but small, we hold
            else:
                trade_ret = -lag_ret  # short the laggard

            gross_pnl = position_size * trade_ret
            commission = position_size * EU_EQUITY_COST_RT
            net = gross_pnl - commission

            trades.append({
                "date": date.strftime("%Y-%m-%d"),
                "symbol": lag,
                "direction": direction,
                "trigger": f"{leader} {leader_ret:+.1%}",
                "leader_return": round(leader_ret * 100, 2),
                "laggard_return": round(lag_ret * 100, 2),
                "pnl": round(gross_pnl, 2),
                "commission": round(commission, 2),
                "net_pnl": round(net, 2),
            })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P2-1 Auto Sector German Sympathy", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P2-2 : EUR/GBP Mean Reversion
# ═══════════════════════════════════════════════════════════════════

def run_p2_2_eurgbp_mr() -> tuple[dict, pd.DataFrame]:
    """
    Z-score (price vs SMA60):
      z < -2.0 -> LONG, z > 2.0 -> SHORT
      Close at z = 0.5 or stop at z = 3.0
    """
    print("\n" + "="*65)
    print("[P2-2] EUR/GBP Mean Reversion")
    print("="*65)

    data = fetch_yf(["EURGBP=X"], period="5y")
    if "EURGBP=X" not in data:
        print("  [ERROR] Missing EURGBP=X data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    df = data["EURGBP=X"].copy()
    df["sma60"] = df["close"].rolling(60).mean()
    df["std60"] = df["close"].rolling(60).std()
    df["zscore"] = (df["close"] - df["sma60"]) / df["std60"]

    trades = []
    position = None  # {"direction", "entry", "entry_z", "date"}
    position_size = INITIAL_CAPITAL * 0.05

    for i in range(65, len(df)):
        date = df.index[i]
        price = df["close"].iloc[i]
        z = df["zscore"].iloc[i]

        if pd.isna(z):
            continue

        # Check exit
        if position is not None:
            exit_signal = False
            exit_reason = ""

            if position["direction"] == "LONG":
                if z >= 0.5:
                    exit_signal = True
                    exit_reason = "TP_REVERT"
                elif z <= -3.0:
                    exit_signal = True
                    exit_reason = "SL_EXTEND"
            else:  # SHORT
                if z <= -0.5:
                    exit_signal = True
                    exit_reason = "TP_REVERT"
                elif z >= 3.0:
                    exit_signal = True
                    exit_reason = "SL_EXTEND"

            if exit_signal:
                if position["direction"] == "LONG":
                    ret = (price - position["entry"]) / position["entry"]
                else:
                    ret = (position["entry"] - price) / position["entry"]

                gross_pnl = position_size * ret
                commission = position_size * FX_COST_RT
                trades.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "entry_date": position["date"],
                    "symbol": "EURGBP",
                    "direction": position["direction"],
                    "entry_price": round(position["entry"], 5),
                    "exit_price": round(price, 5),
                    "entry_zscore": round(position["entry_z"], 2),
                    "exit_zscore": round(z, 2),
                    "exit_reason": exit_reason,
                    "pnl": round(gross_pnl, 2),
                    "commission": round(commission, 2),
                    "net_pnl": round(gross_pnl - commission, 2),
                })
                position = None

        # Entry
        if position is None:
            if z < -2.0:
                position = {
                    "direction": "LONG",
                    "entry": price,
                    "entry_z": z,
                    "date": date.strftime("%Y-%m-%d"),
                }
            elif z > 2.0:
                position = {
                    "direction": "SHORT",
                    "entry": price,
                    "entry_z": z,
                    "date": date.strftime("%Y-%m-%d"),
                }

    # Close any remaining position
    if position is not None:
        price = df["close"].iloc[-1]
        date = df.index[-1]
        z = df["zscore"].iloc[-1]
        if position["direction"] == "LONG":
            ret = (price - position["entry"]) / position["entry"]
        else:
            ret = (position["entry"] - price) / position["entry"]
        gross_pnl = position_size * ret
        commission = position_size * FX_COST_RT
        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "entry_date": position["date"],
            "symbol": "EURGBP",
            "direction": position["direction"],
            "entry_price": round(position["entry"], 5),
            "exit_price": round(price, 5),
            "entry_zscore": round(position["entry_z"], 2),
            "exit_zscore": round(z, 2) if not pd.isna(z) else 0,
            "exit_reason": "EOD",
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(gross_pnl - commission, 2),
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P2-2 EUR/GBP Mean Reversion", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# P2-3 : EUR/JPY Carry + Momentum
# ═══════════════════════════════════════════════════════════════════

def run_p2_3_eurjpy_carry() -> tuple[dict, pd.DataFrame]:
    """
    Carry: +3%/year (EUR vs JPY rate differential).
    Signal: price > EMA(20) -> LONG with carry.
    Close if price < EMA(20) or VIX > 25.
    """
    print("\n" + "="*65)
    print("[P2-3] EUR/JPY Carry + Momentum")
    print("="*65)

    data = fetch_yf(["EURJPY=X", "^VIX"], period="5y")
    if "EURJPY=X" not in data:
        print("  [ERROR] Missing EURJPY=X data")
        return {"n_trades": 0, "verdict": "NO_DATA"}, pd.DataFrame()

    df = data["EURJPY=X"].copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    # VIX data (may not perfectly align)
    has_vix = "^VIX" in data
    if has_vix:
        vix = data["^VIX"]["close"]
    else:
        print("  [WARN] VIX data missing, ignoring VIX filter")

    daily_carry = 0.03 / 252  # 3% annualized carry
    position_size = INITIAL_CAPITAL * 0.05

    trades = []
    position = None  # {"entry", "date", "carry_accrued", "days_held"}

    for i in range(25, len(df)):
        date = df.index[i]
        price = df["close"].iloc[i]
        ema = df["ema20"].iloc[i]

        if pd.isna(price) or pd.isna(ema):
            continue

        # Get VIX for this date
        vix_val = None
        if has_vix:
            # Find nearest VIX date
            vix_dates = vix.index[vix.index <= date]
            if len(vix_dates) > 0:
                vix_val = vix.loc[vix_dates[-1]]

        # Check exit
        if position is not None:
            exit_signal = False
            exit_reason = ""

            if price < ema:
                exit_signal = True
                exit_reason = "BELOW_EMA20"
            elif vix_val is not None and vix_val > 25:
                exit_signal = True
                exit_reason = "VIX_HIGH"

            if not exit_signal:
                # Accrue carry
                position["carry_accrued"] += daily_carry
                position["days_held"] += 1
            else:
                ret = (price - position["entry"]) / position["entry"]
                carry_ret = position["carry_accrued"]
                total_ret = ret + carry_ret
                gross_pnl = position_size * total_ret
                commission = position_size * FX_COST_RT

                trades.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "entry_date": position["date"],
                    "symbol": "EURJPY",
                    "direction": "LONG",
                    "entry_price": round(position["entry"], 3),
                    "exit_price": round(price, 3),
                    "price_return_pct": round(ret * 100, 3),
                    "carry_return_pct": round(carry_ret * 100, 3),
                    "total_return_pct": round(total_ret * 100, 3),
                    "days_held": position["days_held"],
                    "exit_reason": exit_reason,
                    "pnl": round(gross_pnl, 2),
                    "commission": round(commission, 2),
                    "net_pnl": round(gross_pnl - commission, 2),
                })
                position = None

        # Entry: price > EMA(20) and VIX <= 25
        if position is None and price > ema:
            if vix_val is not None and vix_val > 25:
                continue  # Skip entry in high-vol regime
            position = {
                "entry": price,
                "date": date.strftime("%Y-%m-%d"),
                "carry_accrued": 0.0,
                "days_held": 0,
            }

    # Close remaining position
    if position is not None:
        price = df["close"].iloc[-1]
        date = df.index[-1]
        ret = (price - position["entry"]) / position["entry"]
        carry_ret = position["carry_accrued"]
        total_ret = ret + carry_ret
        gross_pnl = position_size * total_ret
        commission = position_size * FX_COST_RT
        trades.append({
            "date": date.strftime("%Y-%m-%d"),
            "entry_date": position["date"],
            "symbol": "EURJPY",
            "direction": "LONG",
            "entry_price": round(position["entry"], 3),
            "exit_price": round(price, 3),
            "price_return_pct": round(ret * 100, 3),
            "carry_return_pct": round(carry_ret * 100, 3),
            "total_return_pct": round(total_ret * 100, 3),
            "days_held": position["days_held"],
            "exit_reason": "EOD",
            "pnl": round(gross_pnl, 2),
            "commission": round(commission, 2),
            "net_pnl": round(gross_pnl - commission, 2),
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"n_trades": 0, "verdict": "NO_TRADES"}, trades_df

    m = calc_metrics(trades_df)
    m["verdict"] = evaluate_verdict(m)
    print_result("P2-3 EUR/JPY Carry + Momentum", m)
    return m, trades_df


# ═══════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

STRATEGY_RUNNERS = {
    "p1_1": ("P1-1 ASML Earnings Chain", run_p1_1_asml_chain),
    "p1_2": ("P1-2 Luxury Momentum China Signal", run_p1_2_luxury_china),
    "p1_3": ("P1-3 EUR/USD Trend Following", run_p1_3_eurusd_trend),
    "p1_4": ("P1-4 EU Close -> US Afternoon", run_p1_4_eu_close_us),
    "p2_1": ("P2-1 Auto Sector German Sympathy", run_p2_1_auto_sympathy),
    "p2_2": ("P2-2 EUR/GBP Mean Reversion", run_p2_2_eurgbp_mr),
    "p2_3": ("P2-3 EUR/JPY Carry + Momentum", run_p2_3_eurjpy_carry),
}


def main():
    parser = argparse.ArgumentParser(description="EU Phase 2 P1+P2 Backtest Runner")
    parser.add_argument("--strategy", type=str, default=None,
                        help="Run single strategy (p1_1, p1_2, ..., p2_3)")
    args = parser.parse_args()

    print("=" * 65)
    print("  EU Phase 2 — P1 + P2 Strategies Backtest")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"  EU Equity Cost RT: {EU_EQUITY_COST_RT*100:.2f}%")
    print(f"  FX Cost RT: {FX_COST_RT*100:.3f}%")
    print(f"  Validation: Sharpe>{VALIDATION['sharpe_min']} PF>{VALIDATION['pf_min']} "
          f"Trades>={VALIDATION['trades_min']} DD<{VALIDATION['dd_max']}%")
    print("=" * 65)

    if args.strategy:
        if args.strategy not in STRATEGY_RUNNERS:
            print(f"  [ERROR] Unknown strategy: {args.strategy}")
            print(f"  Available: {', '.join(STRATEGY_RUNNERS.keys())}")
            return
        runners = {args.strategy: STRATEGY_RUNNERS[args.strategy]}
    else:
        runners = STRATEGY_RUNNERS

    all_results = {}
    all_trades = {}

    for key, (name, runner_fn) in runners.items():
        try:
            metrics, trades_df = runner_fn()
            all_results[key] = {"name": name, **metrics}
            all_trades[key] = trades_df

            # Save trades CSV
            if not trades_df.empty:
                csv_name = f"trades_eu_{key}_{name.split(' ')[0].lower().replace('-', '_')}.csv"
                csv_path = OUTPUT_DIR / csv_name
                trades_df.to_csv(csv_path, index=False)
                print(f"  [CSV] {csv_path.name} ({len(trades_df)} trades)")

        except Exception as e:
            import traceback
            print(f"\n  [ERROR] {name}: {e}")
            traceback.print_exc()
            all_results[key] = {"name": name, "n_trades": 0, "verdict": "ERROR", "error": str(e)}

    # ── Summary ──
    print("\n\n" + "=" * 75)
    print("  RESUME EU PHASE 2 — P1 + P2")
    print("=" * 75)
    print(f"  {'Strategy':<40s} {'Trades':>6s} {'Sharpe':>7s} {'WR%':>6s} {'PF':>6s} {'DD%':>6s} {'Verdict':>10s}")
    print("-" * 75)

    winners = []
    for key in STRATEGY_RUNNERS:
        if key not in all_results:
            continue
        r = all_results[key]
        n = r.get("n_trades", 0)
        s = r.get("sharpe_ratio", 0)
        wr = r.get("win_rate", 0)
        pf = r.get("profit_factor", 0)
        dd = r.get("max_drawdown_pct", 0)
        v = r.get("verdict", "?")
        name = r.get("name", key)

        tag = {"WINNER": "***", "POTENTIEL": " * ", "REJETE": " X ", "NO_DATA": "N/A", "NO_TRADES": "---", "ERROR": "ERR"}.get(v, "?")
        print(f"  [{tag}] {name:<36s} {n:>6d} {s:>7.2f} {wr:>5.1f}% {pf:>6.2f} {dd:>5.2f}% {v:>10s}")

        if v == "WINNER":
            winners.append(name)

    print("-" * 75)
    print(f"  Winners: {len(winners)} / {len(runners)}")
    if winners:
        for w in winners:
            print(f"    -> {w}")
    print("=" * 75)

    # ── Save JSON results ──
    json_path = OUTPUT_DIR / "eu_phase2_p1p2_results.json"

    # Clean up for JSON serialization
    json_results = {}
    for key, val in all_results.items():
        clean = {}
        for k, v in val.items():
            if isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = float(v)
            elif isinstance(v, (np.bool_,)):
                clean[k] = bool(v)
            else:
                clean[k] = v
        json_results[key] = clean

    with open(json_path, "w") as f:
        json.dump({
            "run_date": datetime.now().isoformat(),
            "capital": INITIAL_CAPITAL,
            "costs": {
                "eu_equity_rt": EU_EQUITY_COST_RT,
                "fx_rt": FX_COST_RT,
                "us_per_share": US_COST_PER_SHARE,
            },
            "validation_criteria": VALIDATION,
            "strategies": json_results,
            "summary": {
                "total_strategies": len(runners),
                "winners": len(winners),
                "winner_names": winners,
            },
        }, f, indent=2)

    print(f"\n  [JSON] {json_path}")
    print(f"  Done. {len(runners)} strategies backtested.")


if __name__ == "__main__":
    main()
