"""WF + MC validation gold_trend_mgc V1 (SL 0.4% / TP 0.8%).

Gates obligatoires (cf doctrine + PO review) :
  - WF 5 windows : >= 3/5 OOS profitable
  - MC 1000 sims bootstrap : P(DD>30%) < 15%

Si pass: candidate pour re-promote live_core apres paper 30j.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

MGC_PATH = ROOT / "data" / "futures" / "MGC_LONG.parquet"

# V1 params (Option B)
SL_PCT = 0.004
TP_PCT = 0.008
EMA_PERIOD = 20
MAX_HOLD_DAYS = 10
COST_RT = 5.70
CONTRACT_PT = 10.0
INITIAL_NAV = 10_000.0


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def backtest_window(df: pd.DataFrame) -> tuple[pd.Series, list]:
    """Run V1 backtest on a window. Return daily PnL series + trades."""
    df = df.copy()
    df["ema20"] = ema(df["close"], EMA_PERIOD)
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    sl = tp = 0.0
    trades = []

    for i in range(EMA_PERIOD + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if not in_pos:
            if prev["close"] > prev["ema20"]:
                entry_price = float(row["open"])
                sl = entry_price * (1 - SL_PCT)
                tp = entry_price * (1 + TP_PCT)
                entry_idx = i
                in_pos = True
        else:
            hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
            days = i - entry_idx
            exit_p = None
            if hi >= tp:
                exit_p, reason = tp, "TP"
            elif lo <= sl:
                exit_p, reason = sl, "SL"
            elif days >= MAX_HOLD_DAYS:
                exit_p, reason = cl, "TIME"
            if exit_p is not None:
                pnl = (exit_p - entry_price) * CONTRACT_PT - COST_RT
                trades.append({"entry_dt": df.index[entry_idx], "exit_dt": df.index[i],
                               "entry": entry_price, "exit": exit_p, "pnl": pnl,
                               "reason": reason, "days": days})
                in_pos = False

    if not trades:
        return pd.Series(dtype=float), []
    t = pd.DataFrame(trades)
    daily_pnl = t.set_index("exit_dt")["pnl"].resample("1D").sum().fillna(0)
    return daily_pnl, trades


def metrics(daily_pnl: pd.Series, n_trades: int) -> dict:
    if daily_pnl.empty:
        return {"sharpe": 0, "total": 0, "max_dd_pct": 0, "win_rate": 0, "n_trades": 0}
    sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(252) if daily_pnl.std() > 0 else 0
    eq = INITIAL_NAV + daily_pnl.cumsum()
    peak = eq.cummax()
    dd = ((eq - peak) / peak).min() * 100
    return {
        "sharpe": round(float(sharpe), 2),
        "total": round(daily_pnl.sum(), 0),
        "max_dd_pct": round(float(dd), 1),
        "n_trades": n_trades,
    }


def walk_forward(df: pd.DataFrame, n_windows: int = 5):
    """Rolling 80/20 split over n windows (anchored expanding)."""
    print(f"\n--- Walk-Forward {n_windows} windows ---")
    n = len(df)
    win_size = n // n_windows
    results = []
    for i in range(n_windows):
        end_idx = (i + 1) * win_size if i < n_windows - 1 else n
        window = df.iloc[:end_idx]
        train_end = int(len(window) * 0.8)
        oos = window.iloc[train_end:]
        if len(oos) < 60:
            continue
        daily, tr = backtest_window(oos)
        m = metrics(daily, len(tr))
        m["window"] = i + 1
        m["oos_period"] = f"{oos.index[0].date()} -> {oos.index[-1].date()}"
        results.append(m)
        print(f"  W{i+1} OOS {m['oos_period']}: Sharpe={m['sharpe']:+.2f} "
              f"PnL=${m['total']:+.0f} MaxDD={m['max_dd_pct']:+.1f}% trades={m['n_trades']}")
    n_pass = sum(1 for r in results if r["total"] > 0)
    print(f"\n  WF gate: {n_pass}/{len(results)} OOS profitable (>= 3/5 required)")
    return results, n_pass >= 3


def monte_carlo(daily_pnl: pd.Series, n_sims: int = 1000):
    """Bootstrap daily PnL n_sims times. Compute distribution of max DD."""
    if daily_pnl.empty:
        return {"p10_dd": 0, "p50_dd": 0, "p90_dd": 0, "prob_dd_30": 0}
    arr = daily_pnl.values
    n = len(arr)
    dds = []
    for _ in range(n_sims):
        sample = np.random.choice(arr, n, replace=True)
        eq = INITIAL_NAV + np.cumsum(sample)
        peak = np.maximum.accumulate(eq)
        dd = ((eq - peak) / peak).min()
        dds.append(dd)
    dds = np.array(dds) * 100
    return {
        "p10_dd": round(float(np.percentile(dds, 10)), 1),
        "p50_dd": round(float(np.percentile(dds, 50)), 1),
        "p90_dd": round(float(np.percentile(dds, 90)), 1),
        "prob_dd_30": round(float((dds < -30).mean() * 100), 1),
        "prob_dd_20": round(float((dds < -20).mean() * 100), 1),
        "prob_dd_15": round(float((dds < -15).mean() * 100), 1),
    }


def main():
    print("=== WF + MC Gold Trend MGC V1 (SL 0.4% / TP 0.8%) ===\n")
    df = pd.read_parquet(MGC_PATH)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    print(f"Data: {len(df)} rows {df.index.min().date()} -> {df.index.max().date()}")

    # IS full backtest
    print("\n--- IS full 5Y ---")
    daily_full, trades_full = backtest_window(df)
    m_full = metrics(daily_full, len(trades_full))
    print(f"  Sharpe={m_full['sharpe']:+.2f} PnL=${m_full['total']:+.0f} "
          f"MaxDD={m_full['max_dd_pct']:+.1f}% trades={m_full['n_trades']}")

    # Walk-forward
    wf_results, wf_pass = walk_forward(df, n_windows=5)

    # Monte Carlo on full IS
    print("\n--- Monte Carlo 1000 sims ---")
    mc = monte_carlo(daily_full, n_sims=1000)
    print(f"  Median DD: {mc['p50_dd']}% | p10: {mc['p10_dd']}% | p90: {mc['p90_dd']}%")
    print(f"  P(DD>15%): {mc['prob_dd_15']}%")
    print(f"  P(DD>20%): {mc['prob_dd_20']}%")
    print(f"  P(DD>30%): {mc['prob_dd_30']}%  (gate: < 15%)")
    mc_pass = mc["prob_dd_30"] < 15

    # kill_criteria proba
    print("\n--- Kill criteria proba ---")
    if trades_full:
        wins = sum(1 for t in trades_full if t["pnl"] > 0)
        wr = wins / len(trades_full)
        p5 = (1 - wr) ** 5
        print(f"  WR: {wr*100:.1f}%")
        print(f"  P(5 consecutive losses): {p5*100:.2f}%")
        kill_pass = p5 < 0.10  # 10% threshold
        print(f"  Kill gate '5 consec losses' < 10%: {'PASS' if kill_pass else 'FAIL'}")
    else:
        kill_pass = False

    print("\n=== VERDICT FINAL ===")
    print(f"  WF gate (>= 3/5 OOS profit): {'PASS' if wf_pass else 'FAIL'}")
    print(f"  MC gate (P(DD>30%) < 15%):   {'PASS' if mc_pass else 'FAIL'}")
    print(f"  Kill gate (5 consec < 10%):  {'PASS' if kill_pass else 'FAIL'}")
    overall = wf_pass and mc_pass and kill_pass
    print(f"\n  OVERALL: {'VALIDATED for paper -> live promotion' if overall else 'NEEDS_WORK'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
