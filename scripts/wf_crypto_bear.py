"""WF validation for 3 BEAR crypto strategies on 4H BTC data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

df = pd.read_parquet("data/crypto/candles/BTCUSDT_4h.parquet")
df.columns = [c.lower() for c in df.columns]
if "timestamp" in df.columns:
    df.index = pd.to_datetime(df["timestamp"])
df = df.sort_index()
print(f"BTC 4H: {len(df)} bars, {df.index[0]} to {df.index[-1]}")

COST = 0.001  # 0.10% Binance


def wf_backtest(full_df, trade_fn, name, n_windows=5):
    n = len(full_df)
    ws = n // n_windows
    all_results = []
    for w in range(n_windows):
        start = w * (ws // 2)
        end = min(start + ws, n)
        split = start + int((end - start) * 0.7)
        test_df = full_df.iloc[split:end].copy().reset_index(drop=True)
        trades = trade_fn(test_df)
        if len(trades) == 0:
            all_results.append({"w": w, "sharpe": 0, "wr": 0, "n": 0, "ok": False})
            continue
        t = np.array(trades)
        sh = t.mean() / t.std() * np.sqrt(len(t) * 2) if t.std() > 0 else 0
        wr = (t > 0).mean()
        all_results.append({"w": w, "sharpe": round(sh, 2), "wr": round(wr, 3), "n": len(t), "ok": sh > 0})

    n_pass = sum(r["ok"] for r in all_results)
    avg_sh = np.mean([r["sharpe"] for r in all_results])
    total_n = sum(r["n"] for r in all_results)
    avg_wr = np.mean([r["wr"] for r in all_results if r["n"] > 0]) if total_n > 0 else 0
    verdict = "VALIDATED" if n_pass >= 3 and avg_sh > 0.3 else "BORDERLINE" if n_pass >= 2 else "REJECTED"
    print(f"\n  {name:30s} Sharpe={avg_sh:+.2f} WR={avg_wr:.0%} Trades={total_n:3d} WF={n_pass}/5 -> {verdict}")
    for r in all_results:
        print(f"    W{r['w']}: Sharpe={r['sharpe']:+.2f} WR={r['wr']:.0%} n={r['n']} {'PASS' if r['ok'] else 'FAIL'}")
    return verdict, avg_sh


# === 1. Vol Expansion Bear ===
def ve_trades(tdf):
    tdf = tdf.copy()
    tdf["sma"] = tdf["close"].rolling(20).mean()
    tdf["std"] = tdf["close"].rolling(20).std()
    tdf["bb_upper"] = tdf["sma"] + 2 * tdf["std"]
    tdf["bb_lower"] = tdf["sma"] - 2 * tdf["std"]
    tdf["bb_width"] = (tdf["bb_upper"] - tdf["bb_lower"]) / tdf["sma"]
    tdf["vol_avg"] = tdf["volume"].rolling(42).mean()
    tr = pd.DataFrame({"hl": tdf["high"] - tdf["low"], "hc": (tdf["high"] - tdf["close"].shift(1)).abs(), "lc": (tdf["low"] - tdf["close"].shift(1)).abs()})
    tdf["atr"] = tr.max(axis=1).rolling(14).mean()
    trades = []
    i = 60
    while i < len(tdf) - 5:
        r = tdf.iloc[i]
        if pd.isna(r["bb_width"]) or pd.isna(r["atr"]) or r["atr"] <= 0 or pd.isna(r["vol_avg"]) or r["vol_avg"] <= 0:
            i += 1; continue
        widths = tdf["bb_width"].iloc[max(0, i - 50):i].dropna()
        if len(widths) < 20:
            i += 1; continue
        thresh = np.percentile(widths, 20)
        prev_w = float(tdf.iloc[i - 1]["bb_width"]) if i > 0 else float(r["bb_width"])
        if prev_w <= thresh and r["close"] < r["bb_lower"] and r["volume"] > r["vol_avg"] * 1.5:
            entry = float(r["close"]); atr = float(r["atr"])
            sl = entry + atr * 1.5; tp = entry - atr * 3
            for j in range(i + 1, min(i + 42, len(tdf))):
                h, l = float(tdf.iloc[j]["high"]), float(tdf.iloc[j]["low"])
                if h >= sl:
                    trades.append(-abs(sl - entry) / entry - COST); break
                if l <= tp:
                    trades.append(abs(entry - tp) / entry - COST); break
            else:
                exit_p = float(tdf.iloc[min(i + 41, len(tdf) - 1)]["close"])
                trades.append((entry - exit_p) / entry - COST)
            i += 6
        else:
            i += 1
    return trades


# === 2. Dead Cat Bounce ===
def dcb_trades(tdf):
    tdf = tdf.copy()
    tdf["ema100"] = tdf["close"].ewm(span=100, adjust=False).mean()
    delta = tdf["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    tdf["rsi"] = 100 - (100 / (1 + rs))
    tr = pd.DataFrame({"hl": tdf["high"] - tdf["low"], "hc": (tdf["high"] - tdf["close"].shift(1)).abs(), "lc": (tdf["low"] - tdf["close"].shift(1)).abs()})
    tdf["atr"] = tr.max(axis=1).rolling(14).mean()
    trades = []
    i = 120
    while i < len(tdf) - 5:
        r = tdf.iloc[i]
        if pd.isna(r["ema100"]) or pd.isna(r["rsi"]) or pd.isna(r["atr"]) or r["atr"] <= 0:
            i += 1; continue
        if r["close"] >= r["ema100"] or r["rsi"] < 55:
            i += 1; continue
        was_os = (tdf["rsi"].iloc[max(0, i - 20):i] < 30).any()
        if not was_os:
            i += 1; continue
        if i >= 6:
            rv = tdf["volume"].iloc[i - 3:i].mean()
            pv = tdf["volume"].iloc[i - 6:i - 3].mean()
            if pv > 0 and rv >= pv:
                i += 1; continue
        entry = float(r["close"]); atr = float(r["atr"])
        sl = entry + atr * 1.5; tp = entry - atr * 2
        for j in range(i + 1, min(i + 30, len(tdf))):
            h, l = float(tdf.iloc[j]["high"]), float(tdf.iloc[j]["low"])
            if h >= sl:
                trades.append(-abs(sl - entry) / entry - COST); break
            if l <= tp:
                trades.append(abs(entry - tp) / entry - COST); break
        else:
            exit_p = float(tdf.iloc[min(i + 29, len(tdf) - 1)]["close"])
            trades.append((entry - exit_p) / entry - COST)
        i += 6
    return trades


# === 3. Range BB Harvest ===
def rbb_trades(tdf):
    tdf = tdf.copy()
    tdf["sma"] = tdf["close"].rolling(20).mean()
    tdf["std_col"] = tdf["close"].rolling(20).std()
    tdf["bb_upper"] = tdf["sma"] + 2 * tdf["std_col"]
    tdf["bb_lower"] = tdf["sma"] - 2 * tdf["std_col"]
    # ADX
    plus_dm = tdf["high"].diff().clip(lower=0)
    minus_dm = (-tdf["low"].diff()).clip(lower=0)
    tr = pd.DataFrame({"hl": tdf["high"] - tdf["low"], "hc": (tdf["high"] - tdf["close"].shift(1)).abs(), "lc": (tdf["low"] - tdf["close"].shift(1)).abs()})
    atr_s = tr.max(axis=1).rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr_s)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr_s)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    tdf["adx"] = dx.rolling(14).mean()
    trades = []
    i = 50
    while i < len(tdf) - 5:
        r = tdf.iloc[i]
        if pd.isna(r["adx"]) or pd.isna(r["bb_upper"]) or pd.isna(r["sma"]):
            i += 1; continue
        if r["adx"] >= 20:
            i += 1; continue
        if r["close"] < r["bb_lower"]:
            entry = float(r["close"]); tp = float(r["sma"]); sl_dist = tp - entry; sl = entry - sl_dist * 1.5
            for j in range(i + 1, min(i + 18, len(tdf))):
                h, l = float(tdf.iloc[j]["high"]), float(tdf.iloc[j]["low"])
                if l <= sl:
                    trades.append(-abs(entry - sl) / entry - COST); break
                if h >= tp:
                    trades.append(abs(tp - entry) / entry - COST); break
            else:
                exit_p = float(tdf.iloc[min(i + 17, len(tdf) - 1)]["close"])
                trades.append((exit_p - entry) / entry - COST)
            i += 4
        elif r["close"] > r["bb_upper"]:
            entry = float(r["close"]); tp = float(r["sma"]); sl_dist = entry - tp; sl = entry + sl_dist * 1.5
            for j in range(i + 1, min(i + 18, len(tdf))):
                h, l = float(tdf.iloc[j]["high"]), float(tdf.iloc[j]["low"])
                if h >= sl:
                    trades.append(-abs(sl - entry) / entry - COST); break
                if l <= tp:
                    trades.append(abs(entry - tp) / entry - COST); break
            else:
                exit_p = float(tdf.iloc[min(i + 17, len(tdf) - 1)]["close"])
                trades.append((entry - exit_p) / entry - COST)
            i += 4
        else:
            i += 1
    return trades


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("  CRYPTO BEAR STRATEGIES -- WALK-FORWARD VALIDATION (4H BTC)")
    print("=" * 80)

    v1, s1 = wf_backtest(df, ve_trades, "Vol Expansion Bear")
    v2, s2 = wf_backtest(df, dcb_trades, "Dead Cat Bounce Fade")
    v3, s3 = wf_backtest(df, rbb_trades, "Range BB Harvest")

    print("\n" + "=" * 80)
    results = {"Vol Expansion": v1, "Dead Cat Bounce": v2, "Range BB": v3}
    validated = [k for k, v in results.items() if v == "VALIDATED"]
    borderline = [k for k, v in results.items() if v == "BORDERLINE"]
    rejected = [k for k, v in results.items() if v == "REJECTED"]
    print(f"  VALIDATED: {len(validated)} -- {validated}")
    print(f"  BORDERLINE: {len(borderline)} -- {borderline}")
    print(f"  REJECTED: {len(rejected)} -- {rejected}")
    print("=" * 80)
