"""Test futures strategies across multiple timeframes.

Compare Daily vs 1H vs 5min for the 3 BORDERLINE strategies:
  - MES Trend (EMA crossover)
  - MES/MNQ Pairs (Z-score stat-arb)
  - MGC VIX Hedge (gold flight-to-quality)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load(name, tf):
    path = ROOT / "data" / "futures" / f"{name}_{tf}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    if "datetime" in df.columns:
        df.index = pd.to_datetime(df["datetime"])
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    return df


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def adx(high, low, close, period=14):
    """Simplified ADX proxy using directional movement."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = up.where((up > down) & (up > 0), 0).rolling(period).mean()
    minus_dm = down.where((down > up) & (down > 0), 0).rolling(period).mean()
    plus_di = 100 * plus_dm / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def bollinger(series, period=20, n_std=2):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    return sma, sma + n_std * std, sma - n_std * std


def wf_test(close, signals, name, n_windows=5, cost_bps=5, ann_factor=252):
    """Walk-forward test on a signal series."""
    returns = close.pct_change() * signals.shift(1)
    returns = returns.dropna()
    n = len(returns)
    if n < 100:
        return None

    window_size = n // n_windows
    results = []

    for w in range(n_windows):
        start = w * (window_size // 2)
        end = min(start + window_size, n)
        if end - start < 30:
            continue
        split = start + int((end - start) * 0.70)

        train = returns.iloc[start:split]
        test = returns.iloc[split:end]

        is_sharpe = (
            train.mean() / train.std() * np.sqrt(ann_factor)
            if train.std() > 0 else 0
        )

        # Cost per trade
        trade_mask = signals.iloc[split:end].diff().abs()
        n_trades = trade_mask.sum() / 2
        cost_total = cost_bps / 10000 * n_trades
        test_net = test - cost_total / len(test) if len(test) > 0 else test

        oos_sharpe = (
            test_net.mean() / test_net.std() * np.sqrt(ann_factor)
            if test_net.std() > 0 else 0
        )
        oos_ret = float((1 + test_net).prod() - 1)
        oos_dd = float(
            ((1 + test_net).cumprod() / (1 + test_net).cumprod().cummax() - 1).min()
        )

        results.append({
            "is_sharpe": round(float(is_sharpe), 2),
            "oos_sharpe": round(float(oos_sharpe), 2),
            "oos_return": round(oos_ret, 4),
            "oos_dd": round(oos_dd, 4),
            "trades": int(n_trades),
            "profitable": oos_ret > 0,
        })

    if not results:
        return None

    avg_oos = np.mean([r["oos_sharpe"] for r in results])
    win_pct = np.mean([r["profitable"] for r in results])
    total_trades = sum(r["trades"] for r in results)
    avg_dd = np.mean([abs(r["oos_dd"]) for r in results])

    if avg_oos >= 0.5 and win_pct >= 0.5 and total_trades >= 30:
        verdict = "VALIDATED"
    elif avg_oos >= 0.3 and win_pct >= 0.4:
        verdict = "BORDERLINE"
    else:
        verdict = "REJECTED"

    return {
        "name": name,
        "verdict": verdict,
        "sharpe": round(float(avg_oos), 2),
        "win_pct": round(float(win_pct), 2),
        "trades": total_trades,
        "dd": round(float(avg_dd), 4),
        "windows": results,
    }


# =====================================================================
# STRATEGY 1: MES TREND — EMA crossover + ADX filter
# =====================================================================

def mes_trend_signals(close, high=None, low=None, fast=10, slow=30, adx_thresh=20):
    """EMA crossover with optional ADX filter."""
    ema_f = ema(close, fast)
    ema_s = ema(close, slow)
    sig = pd.Series(0.0, index=close.index)
    sig[ema_f > ema_s] = 1.0
    sig[ema_f < ema_s] = -1.0

    # ADX filter if high/low available
    if high is not None and low is not None:
        adx_val = adx(high, low, close)
        sig[adx_val < adx_thresh] = 0.0

    return sig


# =====================================================================
# STRATEGY 2: MES/MNQ PAIRS — Z-score mean reversion
# =====================================================================

def pairs_signals(close_a, close_b, lookback=20, entry_z=2.0, exit_z=0.5):
    """Z-score stat-arb between two correlated instruments."""
    ratio = np.log(close_a) - np.log(close_b)
    mean = ratio.rolling(lookback).mean()
    std = ratio.rolling(lookback).std()
    z = (ratio - mean) / std.replace(0, np.nan)

    sig = pd.Series(0.0, index=close_a.index)
    sig[z > entry_z] = -1.0   # Short A, Long B
    sig[z < -entry_z] = 1.0   # Long A, Short B
    # Exit zone
    exit_mask = (z > -exit_z) & (z < exit_z)
    sig[exit_mask] = 0.0
    return sig.ffill().fillna(0)


# =====================================================================
# STRATEGY 3: MGC VIX HEDGE — gold + VIX confirmation
# =====================================================================

def mgc_vix_signals(gold_close, vix_close, vix_rsi_high=60, vix_rsi_low=35):
    """Gold breakout confirmed by VIX regime."""
    vix_rsi_val = rsi(vix_close, 14)
    _, bb_upper, bb_lower = bollinger(gold_close, 20, 2)
    gold_adx = gold_close.diff().abs().rolling(14).mean()  # Simplified

    sig = pd.Series(0.0, index=gold_close.index)
    # Long: VIX fear + gold breakout
    sig[(vix_rsi_val > vix_rsi_high) & (gold_close > bb_upper)] = 1.0
    # Short: VIX calm + gold breakdown
    sig[(vix_rsi_val < vix_rsi_low) & (gold_close < bb_lower)] = -1.0

    return sig.ffill().fillna(0)


# =====================================================================
# MAIN — Run all timeframes
# =====================================================================

def main():
    timeframes = {
        "1D": {"ann": 252, "cost_bps": 5},
        "1H": {"ann": 252 * 7, "cost_bps": 5},   # ~7 trading hours/day
        "5M": {"ann": 252 * 7 * 12, "cost_bps": 8},  # 12 bars/hour
    }

    # Parameter grids per timeframe
    ema_params = {
        "1D": [(20, 50), (10, 30), (5, 20)],
        "1H": [(10, 30), (8, 21), (5, 13)],
        "5M": [(12, 26), (8, 21), (5, 13), (20, 50)],
    }
    pairs_params = {
        "1D": [(20, 2.0), (10, 1.5), (30, 2.5)],
        "1H": [(20, 2.0), (10, 1.5), (50, 2.5)],
        "5M": [(50, 2.0), (20, 1.5), (100, 2.5)],
    }
    vix_params = {
        "1D": [(60, 35)],
        "1H": [(60, 35), (65, 30)],
        "5M": [(60, 35), (55, 40)],
    }

    print("=" * 115)
    print("  FUTURES TIMEFRAME TEST — 3 Strategies x 3 Timeframes (Daily / 1H / 5min)")
    print("=" * 115)
    print()

    # Load all data
    data = {}
    for name in ["MES", "MNQ", "MGC", "VIX"]:
        for tf in ["1D", "1H", "5M"]:
            df = load(name, tf)
            if df is not None:
                data[f"{name}_{tf}"] = df

    all_results = []

    # ─── MES TREND ──────────────────────────────────────────────────
    print("--- MES TREND (EMA crossover + ADX) ---")
    header = f"  {'TF':<5s} {'Params':<15s} {'Verdict':<12s} {'Sharpe':>7s} {'Win%':>6s} {'Trades':>7s} {'DD':>7s}"
    print(header)
    print("  " + "-" * 65)

    best_mes = None
    for tf in ["1D", "1H", "5M"]:
        key = f"MES_{tf}"
        if key not in data:
            continue
        df = data[key]
        ann = timeframes[tf]["ann"]
        cost = timeframes[tf]["cost_bps"]

        for fast, slow in ema_params[tf]:
            sig = mes_trend_signals(
                df["close"],
                df.get("high"), df.get("low"),
                fast=fast, slow=slow,
            )
            r = wf_test(df["close"], sig, f"MES_Trend_{tf}", cost_bps=cost, ann_factor=ann)
            if r is None:
                continue

            tag = " ***" if r["verdict"] == "VALIDATED" else (" *" if r["verdict"] == "BORDERLINE" else "")
            print(f"  {tf:<5s} EMA({fast},{slow}){'':<5s} {r['verdict']:<12s} {r['sharpe']:>7.2f} {r['win_pct']:>5.0%} {r['trades']:>7d} {r['dd']:>6.1%}{tag}")

            if best_mes is None or r["sharpe"] > best_mes["sharpe"]:
                best_mes = {**r, "tf": tf, "params": f"EMA({fast},{slow})"}
            all_results.append({**r, "strategy": "MES Trend", "tf": tf, "params": f"EMA({fast},{slow})"})

    if best_mes:
        print(f"  BEST: {best_mes['tf']} {best_mes['params']} -> {best_mes['verdict']} Sharpe {best_mes['sharpe']}, {best_mes['trades']} trades")
    print()

    # ─── MES/MNQ PAIRS ─────────────────────────────────────────────
    print("--- MES/MNQ PAIRS (Z-score stat-arb) ---")
    print(header)
    print("  " + "-" * 65)

    best_pairs = None
    for tf in ["1D", "1H", "5M"]:
        key_a = f"MES_{tf}"
        key_b = f"MNQ_{tf}"
        if key_a not in data or key_b not in data:
            continue
        df_a = data[key_a]
        df_b = data[key_b]
        common = df_a.index.intersection(df_b.index)
        if len(common) < 100:
            continue
        close_a = df_a.loc[common, "close"]
        close_b = df_b.loc[common, "close"]
        ann = timeframes[tf]["ann"]
        cost = timeframes[tf]["cost_bps"] * 2  # 2 legs

        for lookback, threshold in pairs_params[tf]:
            sig = pairs_signals(close_a, close_b, lookback, threshold)
            r = wf_test(close_a, sig, f"Pairs_{tf}", cost_bps=cost, ann_factor=ann)
            if r is None:
                continue

            tag = " ***" if r["verdict"] == "VALIDATED" else (" *" if r["verdict"] == "BORDERLINE" else "")
            print(f"  {tf:<5s} Z({lookback},{threshold}){'':<5s} {r['verdict']:<12s} {r['sharpe']:>7.2f} {r['win_pct']:>5.0%} {r['trades']:>7d} {r['dd']:>6.1%}{tag}")

            if best_pairs is None or r["sharpe"] > best_pairs["sharpe"]:
                best_pairs = {**r, "tf": tf, "params": f"Z({lookback},{threshold})"}
            all_results.append({**r, "strategy": "MES/MNQ Pairs", "tf": tf, "params": f"Z({lookback},{threshold})"})

    if best_pairs:
        print(f"  BEST: {best_pairs['tf']} {best_pairs['params']} -> {best_pairs['verdict']} Sharpe {best_pairs['sharpe']}, {best_pairs['trades']} trades")
    print()

    # ─── MGC VIX HEDGE ──────────────────────────────────────────────
    print("--- MGC VIX HEDGE (gold + VIX confirmation) ---")
    print(header)
    print("  " + "-" * 65)

    best_mgc = None
    for tf in ["1D", "1H", "5M"]:
        key_gold = f"MGC_{tf}"
        key_vix = f"VIX_{tf}"
        if key_gold not in data or key_vix not in data:
            continue
        df_gold = data[key_gold]
        df_vix = data[key_vix]
        common = df_gold.index.intersection(df_vix.index)
        if len(common) < 100:
            continue
        gold_c = df_gold.loc[common, "close"]
        vix_c = df_vix.loc[common, "close"]
        ann = timeframes[tf]["ann"]
        cost = timeframes[tf]["cost_bps"]

        for rsi_high, rsi_low in vix_params[tf]:
            sig = mgc_vix_signals(gold_c, vix_c, rsi_high, rsi_low)
            r = wf_test(gold_c, sig, f"MGC_VIX_{tf}", cost_bps=cost, ann_factor=ann)
            if r is None:
                continue

            tag = " ***" if r["verdict"] == "VALIDATED" else (" *" if r["verdict"] == "BORDERLINE" else "")
            print(f"  {tf:<5s} RSI({rsi_high},{rsi_low}){'':<3s} {r['verdict']:<12s} {r['sharpe']:>7.2f} {r['win_pct']:>5.0%} {r['trades']:>7d} {r['dd']:>6.1%}{tag}")

            if best_mgc is None or r["sharpe"] > best_mgc["sharpe"]:
                best_mgc = {**r, "tf": tf, "params": f"RSI({rsi_high},{rsi_low})"}
            all_results.append({**r, "strategy": "MGC VIX", "tf": tf, "params": f"RSI({rsi_high},{rsi_low})"})

    if best_mgc:
        print(f"  BEST: {best_mgc['tf']} {best_mgc['params']} -> {best_mgc['verdict']} Sharpe {best_mgc['sharpe']}, {best_mgc['trades']} trades")
    print()

    # ─── SUMMARY ────────────────────────────────────────────────────
    print("=" * 115)
    print("  RESUME — BEST PER STRATEGY")
    print("=" * 115)
    print()
    print(f"  {'Strategy':<20s} {'Best TF':<6s} {'Params':<15s} {'Verdict':<12s} {'Sharpe':>7s} {'Trades':>7s} {'DD':>7s}")
    print("  " + "-" * 80)
    for label, best in [("MES Trend", best_mes), ("MES/MNQ Pairs", best_pairs), ("MGC VIX Hedge", best_mgc)]:
        if best:
            print(f"  {label:<20s} {best['tf']:<6s} {best['params']:<15s} {best['verdict']:<12s} {best['sharpe']:>7.2f} {best['trades']:>7d} {best['dd']:>6.1%}")
        else:
            print(f"  {label:<20s} NO DATA")

    # Count VALIDATED
    validated = [r for r in all_results if r["verdict"] == "VALIDATED"]
    print(f"\n  VALIDATED combinations: {len(validated)}/{len(all_results)}")
    for v in validated:
        print(f"    {v['strategy']} {v['tf']} {v['params']} -> Sharpe {v['sharpe']}, {v['trades']} trades")


if __name__ == "__main__":
    main()
