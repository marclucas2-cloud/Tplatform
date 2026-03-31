"""WF-FUTURES — Walk-forward + Monte Carlo for 6 micro futures strategies.

Usage: python scripts/wf_futures_all.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_futures(name, tf="1D"):
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


def wf_backtest(close, signal_fn, name, cost_bps=5, n_windows=5):
    n = len(close)
    window_size = n // n_windows
    train_pct = 0.70
    results = []

    for w in range(n_windows):
        start = w * (window_size // 2)
        end = min(start + window_size, n)
        if end - start < 50:
            continue
        split = start + int((end - start) * train_pct)

        train_close = close.iloc[start:split]
        train_signals = signal_fn(train_close)
        train_ret = train_close.pct_change() * train_signals.shift(1)
        train_ret = train_ret.dropna()
        is_sharpe = (
            train_ret.mean() / train_ret.std() * np.sqrt(252)
            if train_ret.std() > 0
            else 0
        )

        test_close = close.iloc[split:end]
        test_signals = signal_fn(test_close)
        test_ret = test_close.pct_change() * test_signals.shift(1)
        trades = test_signals.diff().abs().sum() / 2
        cost = cost_bps / 10000 * trades / len(test_ret) if len(test_ret) > 0 else 0
        test_ret_net = test_ret - cost
        test_ret_net = test_ret_net.dropna()
        oos_sharpe = (
            test_ret_net.mean() / test_ret_net.std() * np.sqrt(252)
            if test_ret_net.std() > 0
            else 0
        )
        oos_ret = float((1 + test_ret_net).prod() - 1)
        oos_dd = float(
            ((1 + test_ret_net).cumprod() / (1 + test_ret_net).cumprod().cummax() - 1).min()
        )

        results.append({
            "window": w,
            "is_sharpe": round(float(is_sharpe), 2),
            "oos_sharpe": round(float(oos_sharpe), 2),
            "oos_return": round(oos_ret, 4),
            "oos_dd": round(oos_dd, 4),
            "oos_trades": int(trades),
            "profitable": oos_ret > 0,
        })

    if not results:
        return None

    avg_oos = np.mean([r["oos_sharpe"] for r in results])
    avg_is = np.mean([r["is_sharpe"] for r in results])
    win_pct = np.mean([r["profitable"] for r in results])
    avg_dd = np.mean([abs(r["oos_dd"]) for r in results])
    total_trades = sum(r["oos_trades"] for r in results)

    # Monte Carlo bootstrap
    all_oos_rets = [r["oos_return"] for r in results]
    mc_sharpes = []
    rng = np.random.RandomState(42)
    for _ in range(1000):
        sample = rng.choice(all_oos_rets, size=len(all_oos_rets), replace=True)
        mc_sharpes.append(
            np.mean(sample) / (np.std(sample) + 1e-10) * np.sqrt(len(sample))
        )

    if avg_oos >= 0.5 and win_pct >= 0.5 and total_trades >= 30:
        verdict = "VALIDATED"
    elif avg_oos >= 0.3 and win_pct >= 0.4:
        verdict = "BORDERLINE"
    else:
        verdict = "REJECTED"

    return {
        "name": name,
        "verdict": verdict,
        "avg_oos_sharpe": round(float(avg_oos), 2),
        "avg_is_sharpe": round(float(avg_is), 2),
        "win_pct": round(float(win_pct), 2),
        "avg_dd": round(float(avg_dd), 4),
        "total_trades": total_trades,
        "mc_p5_sharpe": round(float(np.percentile(mc_sharpes, 5)), 2),
        "mc_p50_sharpe": round(float(np.percentile(mc_sharpes, 50)), 2),
        "mc_p95_sharpe": round(float(np.percentile(mc_sharpes, 95)), 2),
        "windows": results,
    }


# --- Signal functions ---

def signal_ema_crossover(close, fast=20, slow=50):
    ema_f = close.ewm(span=fast).mean()
    ema_s = close.ewm(span=slow).mean()
    return (ema_f > ema_s).astype(float) * 2 - 1


def signal_overnight_momentum(close):
    ret = close.pct_change()
    sig = pd.Series(0.0, index=close.index)
    sig[ret > 0.003] = 1
    sig[ret < -0.003] = -1
    return sig


def signal_orb(close):
    ret = close.pct_change()
    median_range = ret.abs().rolling(20).median()
    sig = pd.Series(0.0, index=close.index)
    sig[ret > median_range] = 1
    sig[ret < -median_range] = -1
    return sig


def signal_pairs_zscore(close_a, close_b, lookback=20, threshold=2.0):
    ratio = np.log(close_a / close_a.iloc[0]) - np.log(close_b / close_b.iloc[0])
    mean = ratio.rolling(lookback).mean()
    std = ratio.rolling(lookback).std()
    z = (ratio - mean) / std.replace(0, np.nan)
    sig = pd.Series(0.0, index=close_a.index)
    sig[z > threshold] = -1
    sig[z < -threshold] = 1
    sig[(z > -0.5) & (z < 0.5)] = 0
    return sig.ffill().fillna(0)


def signal_gold_vix_hedge(gold_close, vix_close):
    delta = vix_close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    vix_rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    sma = gold_close.rolling(20).mean()
    std = gold_close.rolling(20).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    sig = pd.Series(0.0, index=gold_close.index)
    sig[(vix_rsi > 60) & (gold_close > upper)] = 1
    sig[(vix_rsi < 35) & (gold_close < lower)] = -1
    return sig.ffill().fillna(0)


def signal_brent_lag(close):
    ret2d = close.pct_change(2)
    sig = pd.Series(0.0, index=close.index)
    sig[ret2d > 0.01] = 1
    sig[ret2d < -0.01] = -1
    return sig


def main():
    print("=" * 95)
    print("  FUTURES WALK-FORWARD + MONTE CARLO -- 6 Micro Futures Strategies")
    print("=" * 95)
    print()

    results = {}

    # 1. MES Trend (EMA 20/50)
    mes = load_futures("MES")
    if mes is not None:
        results["mes_trend"] = wf_backtest(
            mes["close"], signal_ema_crossover, "MES Trend", cost_bps=5
        )

    # 2. MES Overnight Momentum
    if mes is not None:
        results["mes_overnight"] = wf_backtest(
            mes["close"], signal_overnight_momentum, "MES Overnight", cost_bps=5
        )

    # 3. M2K ORB
    m2k = load_futures("M2K")
    if m2k is not None:
        results["m2k_orb"] = wf_backtest(
            m2k["close"], signal_orb, "M2K ORB", cost_bps=8
        )

    # 4. MES/MNQ Pairs
    mnq = load_futures("MNQ")
    if mes is not None and mnq is not None:
        common = mes.index.intersection(mnq.index)
        mes_c = mes.loc[common, "close"]
        mnq_c = mnq.loc[common, "close"]
        pairs_sig = signal_pairs_zscore(mes_c, mnq_c)
        results["mes_mnq_pairs"] = wf_backtest(
            mes_c,
            lambda c: pairs_sig.reindex(c.index).fillna(0),
            "MES/MNQ Pairs",
            cost_bps=10,
        )

    # 5. MGC VIX Hedge
    mgc = load_futures("MGC")
    vix = load_futures("VIX")
    if mgc is not None and vix is not None:
        common = mgc.index.intersection(vix.index)
        gold_c = mgc.loc[common, "close"]
        vix_c = vix.loc[common, "close"]
        gold_sig = signal_gold_vix_hedge(gold_c, vix_c)
        results["mgc_vix_hedge"] = wf_backtest(
            gold_c,
            lambda c: gold_sig.reindex(c.index).fillna(0),
            "MGC VIX Hedge",
            cost_bps=5,
        )

    # 6. MCL Brent Lag
    mcl = load_futures("MCL")
    if mcl is not None:
        results["mcl_brent_lag"] = wf_backtest(
            mcl["close"], signal_brent_lag, "MCL Brent Lag", cost_bps=8
        )

    # Print table
    header = (
        f"{'Strategy':<25s} {'Verdict':<12s} {'OOS Sharpe':>10s} "
        f"{'Win%':>6s} {'Trades':>7s} {'DD':>8s} "
        f"{'MC p5':>7s} {'MC p50':>7s} {'MC p95':>7s}"
    )
    print(header)
    print("-" * 95)
    for key, r in results.items():
        if r:
            print(
                f"{r['name']:<25s} {r['verdict']:<12s} "
                f"{r['avg_oos_sharpe']:>10.2f} {r['win_pct']:>5.0%} "
                f"{r['total_trades']:>7d} {r['avg_dd']:>7.1%} "
                f"{r['mc_p5_sharpe']:>7.2f} {r['mc_p50_sharpe']:>7.2f} "
                f"{r['mc_p95_sharpe']:>7.2f}"
            )
    print("-" * 95)

    validated = [k for k, r in results.items() if r and r["verdict"] == "VALIDATED"]
    borderline = [k for k, r in results.items() if r and r["verdict"] == "BORDERLINE"]
    rejected = [k for k, r in results.items() if r and r["verdict"] == "REJECTED"]
    print(f"\nVALIDATED: {len(validated)} -- {validated}")
    print(f"BORDERLINE: {len(borderline)} -- {borderline}")
    print(f"REJECTED: {len(rejected)} -- {rejected}")

    # Save
    output_dir = ROOT / "output" / "wf_futures_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, r in results.items():
        if r:
            with open(output_dir / f"{key}.json", "w") as f:
                json.dump(r, f, indent=2, default=str)

    summary = {
        "validated": validated,
        "borderline": borderline,
        "rejected": rejected,
        "total": len(results),
    }
    with open(output_dir / "wf_futures_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults saved to {output_dir}/")


if __name__ == "__main__":
    main()
