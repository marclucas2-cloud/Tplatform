"""
Phase 6 : Portfolio optimizations.
- Kelly criterion sizing
- Correlation matrix entre strategies
- Regime detection (vol regime impacts)
"""
import sys, os
import pandas as pd
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
OUTPUT_DIR = Path(__file__).parent / "output"

# All active + new validated strategies
STRATEGY_FILES = {
    "OpEx Gamma Pin": "trades_opex_gamma_pin.csv",
    "Overnight Gap": "trades_overnight_gap_continuation.csv",
    "Crypto-Proxy V2": "trades_crypto-proxy_regime_switch.csv",
    "Day-of-Week": "trades_day-of-week_seasonal.csv",
    "Late Day MR": "trades_late_day_mean_reversion.csv",
    # New winners
    "VWAP Micro": "trades_vwap_micro_deviation.csv",
    "Triple EMA": "trades_triple_ema_pullback.csv",
}

def load_daily_pnl(csv_name: str) -> pd.Series:
    path = OUTPUT_DIR / csv_name
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "net_pnl" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    daily = df.groupby("date")["net_pnl"].sum()
    return daily

def kelly_criterion(trades_csv: str) -> dict:
    """Calcule le Kelly criterion optimal."""
    path = OUTPUT_DIR / trades_csv
    if not path.exists():
        return {"kelly_pct": 0, "half_kelly": 0}
    df = pd.read_csv(path)
    if df.empty or "net_pnl" not in df.columns:
        return {"kelly_pct": 0, "half_kelly": 0}

    wins = df[df["net_pnl"] > 0]["net_pnl"]
    losses = df[df["net_pnl"] <= 0]["net_pnl"]

    if len(wins) == 0 or len(losses) == 0:
        return {"kelly_pct": 0, "half_kelly": 0}

    win_rate = len(wins) / len(df)
    avg_win = wins.mean()
    avg_loss = abs(losses.mean())

    if avg_loss == 0:
        return {"kelly_pct": 100, "half_kelly": 50}

    win_loss_ratio = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / win_loss_ratio

    return {
        "kelly_pct": round(kelly * 100, 2),
        "half_kelly": round(kelly * 50, 2),
        "win_rate": round(win_rate * 100, 1),
        "win_loss_ratio": round(win_loss_ratio, 2),
    }

def main():
    print("=" * 70)
    print("  PHASE 6 : PORTFOLIO OPTIMIZATIONS")
    print("=" * 70)

    # 1. Kelly Criterion
    print("\n--- KELLY CRITERION ---")
    print(f"  {'Strategy':<25} {'Kelly%':>8} {'Half-K%':>8} {'WR%':>6} {'W/L':>6}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")

    for name, csv in STRATEGY_FILES.items():
        k = kelly_criterion(csv)
        print(f"  {name:<25} {k['kelly_pct']:>7.1f}% {k['half_kelly']:>7.1f}% "
              f"{k.get('win_rate', 0):>5.1f}% {k.get('win_loss_ratio', 0):>5.2f}")

    # 2. Correlation Matrix
    print("\n--- CORRELATION MATRIX ---")
    daily_pnls = {}
    for name, csv in STRATEGY_FILES.items():
        pnl = load_daily_pnl(csv)
        if not pnl.empty:
            daily_pnls[name] = pnl

    if len(daily_pnls) >= 2:
        pnl_df = pd.DataFrame(daily_pnls).fillna(0)
        corr = pnl_df.corr()

        print(f"\n  {'':>20}", end="")
        short_names = {n: n[:8] for n in corr.columns}
        for col in corr.columns:
            print(f" {short_names[col]:>8}", end="")
        print()

        for row in corr.index:
            print(f"  {short_names[row]:>20}", end="")
            for col in corr.columns:
                val = corr.loc[row, col]
                print(f" {val:>8.2f}", end="")
            print()

        # High correlations
        print("\n  High correlations (>0.3):")
        seen = set()
        for i, row in enumerate(corr.index):
            for j, col in enumerate(corr.columns):
                if i < j and abs(corr.iloc[i, j]) > 0.3:
                    pair = (row, col)
                    if pair not in seen:
                        seen.add(pair)
                        print(f"    {row} <-> {col}: {corr.iloc[i, j]:.2f}")
        if not seen:
            print("    None — good diversification!")

    # 3. Regime Analysis (high vol vs low vol)
    print("\n--- REGIME ANALYSIS ---")
    # Use SPY as regime indicator
    try:
        spy_path = OUTPUT_DIR.parent / "data_cache"
        spy_files = list(spy_path.glob("SPY_5Min_*.parquet"))
        if spy_files:
            spy_df = pd.read_parquet(spy_files[-1])
            spy_daily = spy_df.groupby(spy_df.index.date).agg(
                daily_range=("high", "max"),
                daily_low=("low", "min"),
                close=("close", "last"),
            )
            spy_daily["atr_pct"] = (spy_daily["daily_range"] - spy_daily["daily_low"]) / spy_daily["close"] * 100
            median_atr = spy_daily["atr_pct"].median()

            high_vol_days = set(spy_daily[spy_daily["atr_pct"] > median_atr].index)
            low_vol_days = set(spy_daily[spy_daily["atr_pct"] <= median_atr].index)

            print(f"  SPY median daily ATR: {median_atr:.2f}%")
            print(f"  High vol days: {len(high_vol_days)}, Low vol days: {len(low_vol_days)}")

            print(f"\n  {'Strategy':<25} {'HiVol PnL':>12} {'LoVol PnL':>12} {'Better':>8}")
            print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*8}")

            for name, csv in STRATEGY_FILES.items():
                pnl = load_daily_pnl(csv)
                if pnl.empty:
                    continue
                hi_pnl = pnl[pnl.index.isin(high_vol_days)].sum()
                lo_pnl = pnl[pnl.index.isin(low_vol_days)].sum()
                better = "HiVol" if hi_pnl > lo_pnl else "LoVol"
                print(f"  {name:<25} ${hi_pnl:>10,.2f} ${lo_pnl:>10,.2f} {better:>8}")
    except Exception as e:
        print(f"  Regime analysis skipped: {e}")

    # 4. Recommended allocation
    print("\n--- RECOMMENDED ALLOCATION (Sharpe-weighted + Kelly-capped) ---")
    sharpes = {
        "OpEx Gamma Pin": 10.41,
        "Overnight Gap": 5.22,
        "Crypto-Proxy V2": 3.49,
        "Day-of-Week": 3.42,
        "Late Day MR": 0.60,
        "VWAP Micro": 3.08,
        "Triple EMA": 1.06,
    }

    total_sharpe = sum(max(s, 0) for s in sharpes.values())
    if total_sharpe > 0:
        raw_alloc = {k: max(s, 0) / total_sharpe for k, s in sharpes.items()}
    else:
        raw_alloc = {k: 1/len(sharpes) for k in sharpes}

    # Cap at 20%
    MAX_ALLOC = 0.20
    for _ in range(10):
        excess = sum(max(0, v - MAX_ALLOC) for v in raw_alloc.values())
        if excess == 0:
            break
        below_cap = {k: v for k, v in raw_alloc.items() if v < MAX_ALLOC}
        if not below_cap:
            break
        redistribute = excess / len(below_cap)
        for k in raw_alloc:
            if raw_alloc[k] > MAX_ALLOC:
                raw_alloc[k] = MAX_ALLOC
            elif k in below_cap:
                raw_alloc[k] += redistribute

    print(f"\n  {'Strategy':<25} {'Sharpe':>8} {'Alloc%':>8} {'Capital':>12}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*12}")
    for name, alloc in sorted(raw_alloc.items(), key=lambda x: -x[1]):
        cap = alloc * 100_000
        print(f"  {name:<25} {sharpes[name]:>8.2f} {alloc*100:>7.1f}% ${cap:>10,.0f}")

    print(f"\n  Total: {sum(raw_alloc.values())*100:.1f}%")
    print(f"  Strategies: {len(raw_alloc)} (was 10, now 12 with new winners)")

if __name__ == "__main__":
    main()
