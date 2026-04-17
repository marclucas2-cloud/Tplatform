"""
Walk-Forward validation — FX Structural Strategies.

4 strategies based on STRUCTURAL edges (not technical):
1. Risk-Managed Carry (Barroso & Santa-Clara 2015)
2. FX Time-Series Momentum with Vol Scaling (Moskowitz et al 2012)
3. Month-End Rebalancing Flow
4. FX Value + Carry Combo (Asness, Moskowitz, Pedersen 2013)

Uses daily data (5 years, sufficient for 10+ WF windows).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "fx"

# ── WF Config ────────────────────────────────────────────────────────────────
TRAIN_DAYS = 252  # 1 year
TEST_DAYS = 63    # 3 months
MIN_WINDOWS = 4
COMMISSION_RT_PCT = 0.0005  # $2 on $20K ≈ 0.01% + spread 0.02% ≈ 0.05% round trip

# Carry pairs (high yield - low yield)
CARRY_PAIRS = {
    "AUDJPY": {"long": "AUD", "short": "JPY", "swap_daily_bps": 3.5},  # ~3.5 bps/day carry
    "NZDUSD": {"long": "NZD", "short": "USD", "swap_daily_bps": 1.5},
    "EURJPY": {"long": "EUR", "short": "JPY", "swap_daily_bps": 2.0},
    "USDJPY": {"long": "USD", "short": "JPY", "swap_daily_bps": 4.0},
}

# All pairs for momentum/value
ALL_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURGBP", "EURJPY", "AUDJPY", "NZDUSD"]


def load_daily(pair: str) -> pd.DataFrame:
    """Load daily FX data."""
    path = DATA_DIR / f"{pair}_1D.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    return df


def sharpe(returns: pd.Series) -> float:
    if len(returns) < 10 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


def max_drawdown(returns: pd.Series) -> float:
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def run_wf(strategy_fn, name: str, data: dict, extra=None) -> dict:
    """Run walk-forward on a strategy."""
    # Use EURUSD as the reference timeline
    ref = data.get("EURUSD")
    if ref is None or len(ref) < TRAIN_DAYS + TEST_DAYS:
        return {"verdict": "NO_DATA", "name": name}

    total_days = len(ref)
    ref_dates = ref.index
    windows = []
    start = 0
    # Warm-up for indicator lookback (252d for Value+Momentum, 63d for TS Momentum)
    WARMUP = 260

    while start + TRAIN_DAYS + TEST_DAYS <= total_days:
        train_end = start + TRAIN_DAYS
        test_end = train_end + TEST_DAYS

        # Slice data for training WITH warm-up for indicator lookback
        train_warmup_start = max(0, start - WARMUP)
        train_data = {p: df.iloc[train_warmup_start:train_end] for p, df in data.items() if len(df) > train_end}

        # Slice data for test WITH warm-up for indicator calculation
        test_warmup_start = max(0, train_end - WARMUP)
        test_data = {p: df.iloc[test_warmup_start:test_end] for p, df in data.items() if len(df) >= test_end}

        # Run strategy on train and test (with warm-up included)
        train_returns_full = strategy_fn(train_data, is_train=True, extra=extra)
        test_returns_full = strategy_fn(test_data, is_train=False, extra=extra)

        # Filter returns to only the actual period (exclude warm-up)
        train_period_start = ref_dates[start]
        train_returns = train_returns_full[train_returns_full.index >= train_period_start]
        test_period_start = ref_dates[train_end]
        test_returns = test_returns_full[test_returns_full.index >= test_period_start]

        if len(train_returns) > 0 and len(test_returns) > 0:
            is_sharpe = sharpe(train_returns)
            oos_sharpe = sharpe(test_returns)
            oos_trades = len(test_returns[test_returns != 0])
            oos_profitable = oos_sharpe > 0

            windows.append({
                "window": len(windows),
                "is_sharpe": round(is_sharpe, 2),
                "oos_sharpe": round(oos_sharpe, 2),
                "oos_return": round(float(test_returns.sum()), 4),
                "oos_trades": oos_trades,
                "oos_profitable": oos_profitable,
                "oos_max_dd": round(max_drawdown(test_returns), 4),
            })

        start += TEST_DAYS  # Roll forward

    if len(windows) < MIN_WINDOWS:
        return {"verdict": "INSUFFICIENT_WINDOWS", "name": name, "windows": len(windows)}

    avg_oos_sharpe = np.mean([w["oos_sharpe"] for w in windows])
    avg_is_sharpe = np.mean([w["is_sharpe"] for w in windows])
    pct_profitable = np.mean([w["oos_profitable"] for w in windows])
    total_trades = sum(w["oos_trades"] for w in windows)
    oos_is_ratio = avg_oos_sharpe / avg_is_sharpe if avg_is_sharpe != 0 else 0
    avg_dd = np.mean([w["oos_max_dd"] for w in windows])

    # Verdict
    if avg_oos_sharpe >= 0.5 and pct_profitable >= 0.50 and total_trades >= 30:
        verdict = "VALIDATED"
    elif avg_oos_sharpe >= 0.2 and pct_profitable >= 0.40:
        verdict = "BORDERLINE"
    else:
        verdict = "REJECTED"

    return {
        "name": name,
        "verdict": verdict,
        "avg_oos_sharpe": round(avg_oos_sharpe, 2),
        "avg_is_sharpe": round(avg_is_sharpe, 2),
        "oos_is_ratio": round(oos_is_ratio, 2),
        "pct_profitable": round(pct_profitable * 100, 0),
        "total_oos_trades": total_trades,
        "avg_max_dd": round(avg_dd, 4),
        "n_windows": len(windows),
        "windows": windows,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1 — Risk-Managed Carry (Barroso & Santa-Clara 2015)
#
# Edge: Buy high-yield currencies, sell low-yield. Scale position by
# inverse realized volatility to avoid carry crashes.
# ══════════════════════════════════════════════════════════════════════════════

def strat_carry_vol_scaled(data: dict, is_train=False, extra=None) -> pd.Series:
    """Carry trade with volatility scaling."""
    all_returns = []

    for pair, info in CARRY_PAIRS.items():
        df = data.get(pair)
        if df is None or len(df) < 60:
            continue

        returns = df["close"].pct_change().dropna()
        # Realized vol (20-day rolling)
        vol = returns.rolling(20).std()
        # Target vol = 5% annualized
        target_vol = 0.05 / np.sqrt(252)
        # Position size = target_vol / realized_vol (capped 0.1 to 3.0)
        sizing = (target_vol / vol.replace(0, np.nan)).clip(0.1, 3.0)

        # Carry return = price return + swap
        swap_daily = info["swap_daily_bps"] / 10000
        carry_return = returns * sizing + swap_daily

        # Costs
        carry_return -= COMMISSION_RT_PCT / 252  # Amortized daily

        all_returns.append(carry_return)

    if not all_returns:
        return pd.Series(dtype=float)

    # Equal-weight across carry pairs
    combined = pd.concat(all_returns, axis=1).mean(axis=1).dropna()
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2 — FX Time-Series Momentum (Moskowitz, Ooi, Pedersen 2012)
#
# Edge: Currencies that went up keep going up (1-12 month lookback).
# Vol-scaled position. NOT EMA crossover — pure return momentum.
# ══════════════════════════════════════════════════════════════════════════════

def strat_ts_momentum(data: dict, is_train=False, extra=None) -> pd.Series:
    """Time-series momentum: go long if past return positive, short if negative."""
    all_returns = []

    for pair in ALL_PAIRS:
        df = data.get(pair)
        if df is None or len(df) < 65:
            continue

        returns = df["close"].pct_change().dropna()
        # Lookback: 63 days (3 months)
        past_return = returns.rolling(63).sum()
        # Signal: +1 if positive momentum, -1 if negative
        signal = np.sign(past_return).shift(1)  # Avoid lookahead

        # Vol scaling
        vol = returns.rolling(20).std()
        target_vol = 0.05 / np.sqrt(252)
        sizing = (target_vol / vol.replace(0, np.nan)).clip(0.1, 3.0)

        strat_return = signal * returns * sizing

        # Costs: assume 1 trade per month (signal rarely flips daily)
        strat_return -= COMMISSION_RT_PCT / 21  # ~1 RT per month amortized

        all_returns.append(strat_return)

    if not all_returns:
        return pd.Series(dtype=float)

    combined = pd.concat(all_returns, axis=1).mean(axis=1).dropna()
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3 — Month-End Rebalancing Flow
#
# Edge: Institutional hedging flows at month-end create predictable
# price pressure. EUR/USD tends to move against USD in last 3 days.
# ══════════════════════════════════════════════════════════════════════════════

def strat_month_end(data: dict, is_train=False, extra=None) -> pd.Series:
    """Month-end rebalancing: go long EUR/USD in last 3 trading days of month."""
    df = data.get("EURUSD")
    if df is None or len(df) < 30:
        return pd.Series(dtype=float)

    returns = df["close"].pct_change().dropna()

    # Identify last 3 trading days of each month
    dates = returns.index.to_series()
    month = dates.dt.month
    month_shift = month.shift(-1)
    # Month-end = when next day's month is different (or last data point)
    is_month_end = (month != month_shift).fillna(True)
    # Last 3 trading days: month_end + 2 days before
    me_mask = is_month_end.copy()
    for lag in [1, 2]:
        me_mask = me_mask | is_month_end.shift(-lag).fillna(False)

    # Signal: long in last 3 days, flat otherwise
    signal = me_mask.astype(float)

    strat_return = signal * returns
    # Costs: ~2 trades per month (entry + exit)
    strat_return[signal > 0] -= COMMISSION_RT_PCT / 3  # 3 days per trade

    return strat_return


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 4 — FX Value + Momentum Combo (AMP 2013)
#
# Edge: Combine PPP deviation (value) with momentum. When both agree,
# the signal is stronger. Cross-sectional across 8 pairs.
# ══════════════════════════════════════════════════════════════════════════════

def strat_value_momentum(data: dict, is_train=False, extra=None) -> pd.Series:
    """Value (5Y mean reversion) + Momentum (3M) combined signal."""
    all_returns = []

    for pair in ALL_PAIRS:
        df = data.get(pair)
        if df is None or len(df) < 252:
            continue

        returns = df["close"].pct_change().dropna()

        # Value signal: z-score of current price vs 252-day (1Y) mean
        ma_252 = df["close"].rolling(252).mean()
        std_252 = df["close"].rolling(252).std()
        z_value = -((df["close"] - ma_252) / std_252.replace(0, np.nan))  # Negative = overvalued = short
        z_value = z_value.clip(-2, 2) / 2  # Normalize to [-1, 1]

        # Momentum signal: 63-day return sign
        mom_63 = returns.rolling(63).sum()
        z_mom = np.sign(mom_63)

        # Combined signal: average of value + momentum
        combined_signal = ((z_value + z_mom) / 2).shift(1)

        # Vol scaling
        vol = returns.rolling(20).std()
        target_vol = 0.04 / np.sqrt(252)
        sizing = (target_vol / vol.replace(0, np.nan)).clip(0.1, 2.0)

        strat_return = combined_signal * returns * sizing
        strat_return -= COMMISSION_RT_PCT / 42  # Low turnover

        all_returns.append(strat_return)

    if not all_returns:
        return pd.Series(dtype=float)

    combined = pd.concat(all_returns, axis=1).mean(axis=1).dropna()
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 5 — Carry + Momentum Filter
#
# Edge: Only enter carry when 3M momentum confirms direction.
# Cuts carry crashes because momentum turns negative before crash.
# ══════════════════════════════════════════════════════════════════════════════

def strat_carry_momentum_filter(data: dict, is_train=False, extra=None) -> pd.Series:
    """Carry trade filtered by momentum — only hold carry when momentum agrees."""
    all_returns = []

    for pair, info in CARRY_PAIRS.items():
        df = data.get(pair)
        if df is None or len(df) < 65:
            continue

        returns = df["close"].pct_change().dropna()

        # Momentum filter: 63-day return must be positive to hold carry
        mom_63 = returns.rolling(63).sum().shift(1)
        carry_on = (mom_63 > 0).astype(float)  # Only hold when momentum positive

        # Vol scaling
        vol = returns.rolling(20).std()
        target_vol = 0.05 / np.sqrt(252)
        sizing = (target_vol / vol.replace(0, np.nan)).clip(0.1, 3.0)

        # Carry return with momentum filter
        swap_daily = info["swap_daily_bps"] / 10000
        strat_return = carry_on * (returns * sizing + swap_daily)
        strat_return -= carry_on * COMMISSION_RT_PCT / 63  # Trade when filter flips

        all_returns.append(strat_return)

    if not all_returns:
        return pd.Series(dtype=float)

    combined = pd.concat(all_returns, axis=1).mean(axis=1).dropna()
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — Run all strategies through WF
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 80)
    print("  WALK-FORWARD — FX Structural Strategies")
    print("  Data: 5 years daily (2021-2026), 8 pairs")
    print("  Windows: 252d train / 63d test, rolling")
    print("=" * 80)

    # Load all data
    data = {}
    for pair in ALL_PAIRS:
        df = load_daily(pair)
        if not df.empty:
            data[pair] = df
            print(f"  Loaded {pair}: {len(df)} candles")
    print()

    strategies = [
        ("FX Carry Vol-Scaled", strat_carry_vol_scaled),
        ("FX TS Momentum", strat_ts_momentum),
        ("FX Month-End Flow", strat_month_end),
        ("FX Value+Momentum", strat_value_momentum),
        ("FX Carry+Momentum Filter", strat_carry_momentum_filter),
    ]

    results = []
    for name, fn in strategies:
        print(f"\n--- {name} ---")
        result = run_wf(fn, name, data)
        results.append(result)

        windows_list = result.get("windows", [])
        if isinstance(windows_list, list):
            for w in windows_list:
                status = "PASS" if w.get("oos_profitable") else "FAIL"
                print(f"  Window {w['window']}: IS={w['is_sharpe']:.2f}  "
                      f"OOS={w['oos_sharpe']:.2f}  DD={w['oos_max_dd']:.2%}  [{status}]")

        print(f"  [{result['verdict']}] OOS Sharpe={result.get('avg_oos_sharpe', 0):.2f}  "
              f"Win%={result.get('pct_profitable', 0):.0f}%  "
              f"Trades={result.get('total_oos_trades', 0)}  "
              f"OOS/IS={result.get('oos_is_ratio', 0):.2f}")

    # Summary table
    print("\n" + "-" * 80)
    print(f"{'Strategy':<30} {'Verdict':<12} {'OOS Sharpe':>10} {'Win%':>6} {'Trades':>7} {'OOS/IS':>7}")
    print("-" * 80)
    for r in results:
        print(f"{r['name']:<30} {r['verdict']:<12} {r.get('avg_oos_sharpe', 0):>10.2f} "
              f"{r.get('pct_profitable', 0):>5.0f}% {r.get('total_oos_trades', 0):>7} "
              f"{r.get('oos_is_ratio', 0):>7.2f}")

    # Save results
    output_path = ROOT / "data" / "fx" / "wf_structural_results.json"
    with open(output_path, "w") as f:
        json.dump({"results": results, "timestamp": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    print(f"\nResults saved to {output_path}")

    validated = sum(1 for r in results if r["verdict"] == "VALIDATED")
    print(f"\n{'=' * 80}")
    print(f"  VALIDATED: {validated}/{len(results)}")
    print(f"{'=' * 80}")
