"""
Phase 1 — Optimisations analytiques sur les strategies actives.

1. Monte Carlo (1000 shuffles) : IC 5%-95% du Sharpe et du PnL
2. Cost sensitivity : break-even commission/slippage
3. Stationnarite : rolling Sharpe 30j pour detecter decay
"""
import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from utils.metrics import calculate_metrics

OUTPUT_DIR = Path(__file__).parent / "output"
INITIAL_CAPITAL = config.INITIAL_CAPITAL

# Strategies intraday actives et leurs fichiers CSV
ACTIVE_STRATEGIES = {
    "OpEx Gamma Pin":              "trades_opex_gamma_pin.csv",
    "Overnight Gap Continuation":  "trades_overnight_gap_continuation.csv",
    "Crypto-Proxy Regime V2":      "trades_crypto-proxy_regime_switch.csv",
    "Day-of-Week Seasonal":        "trades_day-of-week_seasonal.csv",
    "Late Day Mean Reversion":     "trades_late_day_mean_reversion.csv",
    "ORB 5-Min V2":                "trades_orb_5-min_breakout.csv",
    "Mean Reversion V2":           "trades_mean_reversion_bb_rsi.csv",
}


def load_trades(csv_name: str) -> pd.DataFrame:
    path = OUTPUT_DIR / csv_name
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ============================================================
# 1. MONTE CARLO SIMULATION
# ============================================================
def monte_carlo(trades: pd.DataFrame, n_sims: int = 1000) -> dict:
    """Shuffle trade order N times, compute Sharpe/PnL distributions."""
    if trades.empty or "net_pnl" not in trades.columns:
        return {"sharpe_5": 0, "sharpe_50": 0, "sharpe_95": 0,
                "pnl_5": 0, "pnl_50": 0, "pnl_95": 0, "prob_positive": 0}

    net_pnls = trades["net_pnl"].values
    n = len(net_pnls)
    rng = np.random.default_rng(42)

    sharpes = []
    final_pnls = []

    for _ in range(n_sims):
        shuffled = rng.permutation(net_pnls)
        equity = INITIAL_CAPITAL + np.cumsum(shuffled)
        returns = np.diff(equity) / equity[:-1]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
        else:
            sharpe = 0
        sharpes.append(sharpe)
        final_pnls.append(equity[-1] - INITIAL_CAPITAL)

    sharpes = np.array(sharpes)
    final_pnls = np.array(final_pnls)

    return {
        "sharpe_5": round(np.percentile(sharpes, 5), 2),
        "sharpe_50": round(np.percentile(sharpes, 50), 2),
        "sharpe_95": round(np.percentile(sharpes, 95), 2),
        "pnl_5": round(np.percentile(final_pnls, 5), 0),
        "pnl_50": round(np.percentile(final_pnls, 50), 0),
        "pnl_95": round(np.percentile(final_pnls, 95), 0),
        "prob_positive": round(np.mean(final_pnls > 0) * 100, 1),
    }


# ============================================================
# 2. COST SENSITIVITY
# ============================================================
def cost_sensitivity(trades: pd.DataFrame) -> list[dict]:
    """Re-compute PnL under different cost assumptions."""
    if trades.empty:
        return []

    results = []
    commission_levels = [0, 0.003, 0.005, 0.010, 0.020]
    slippage_levels = [0, 0.0001, 0.0002, 0.0005, 0.001]

    for comm in commission_levels:
        for slip in slippage_levels:
            total_pnl = 0
            for _, t in trades.iterrows():
                shares = t.get("shares", 1)
                entry = t.get("entry_price", 0)
                exit_p = t.get("exit_price", 0)
                direction = t.get("direction", "LONG")

                # Recalculate with new costs
                if direction == "LONG":
                    adj_entry = entry * (1 + slip)
                    adj_exit = exit_p * (1 - slip)
                    pnl = (adj_exit - adj_entry) * shares
                else:
                    adj_entry = entry * (1 - slip)
                    adj_exit = exit_p * (1 + slip)
                    pnl = (adj_entry - adj_exit) * shares

                pnl -= shares * comm * 2  # entry + exit

                total_pnl += pnl

            results.append({
                "commission": comm,
                "slippage_pct": slip * 100,
                "net_pnl": round(total_pnl, 2),
                "return_pct": round(total_pnl / INITIAL_CAPITAL * 100, 3),
                "profitable": total_pnl > 0,
            })

    return results


def find_breakeven(cost_results: list[dict]) -> dict:
    """Find the commission/slippage break-even point."""
    if not cost_results:
        return {"breakeven_comm": 0, "breakeven_slip": 0}

    # Find max commission where still profitable (at base slippage 0.02%)
    base_slip = 0.02
    max_comm = 0
    for r in cost_results:
        if abs(r["slippage_pct"] - base_slip) < 0.001 and r["profitable"]:
            max_comm = max(max_comm, r["commission"])

    # Find max slippage where still profitable (at base commission 0.005)
    base_comm = 0.005
    max_slip = 0
    for r in cost_results:
        if r["commission"] == base_comm and r["profitable"]:
            max_slip = max(max_slip, r["slippage_pct"])

    return {
        "breakeven_comm": max_comm,
        "breakeven_slip_pct": max_slip,
    }


# ============================================================
# 3. STATIONARITY (Rolling Sharpe)
# ============================================================
def stationarity_check(trades: pd.DataFrame, window_days: int = 30) -> dict:
    """Rolling Sharpe ratio to detect edge decay."""
    if trades.empty or len(trades) < 10:
        return {"rolling_sharpes": [], "trend": "insufficient_data",
                "latest_30d_sharpe": 0, "first_30d_sharpe": 0}

    trades_sorted = trades.sort_values("date")
    daily_pnl = trades_sorted.groupby("date")["net_pnl"].sum()
    daily_pnl.index = pd.to_datetime(daily_pnl.index)

    if len(daily_pnl) < window_days:
        window_days = max(len(daily_pnl) // 3, 5)

    rolling_sharpe = []
    dates_list = []

    for i in range(window_days, len(daily_pnl)):
        window = daily_pnl.iloc[i - window_days:i]
        if window.std() > 0:
            s = window.mean() / window.std() * np.sqrt(252)
        else:
            s = 0
        rolling_sharpe.append(round(s, 2))
        dates_list.append(str(daily_pnl.index[i].date()))

    if len(rolling_sharpe) < 2:
        return {"rolling_sharpes": rolling_sharpe, "trend": "insufficient_data",
                "latest_30d_sharpe": 0, "first_30d_sharpe": 0}

    # Trend : linear regression on rolling Sharpe
    x = np.arange(len(rolling_sharpe))
    if np.std(rolling_sharpe) > 0:
        slope = np.polyfit(x, rolling_sharpe, 1)[0]
    else:
        slope = 0

    if slope > 0.05:
        trend = "IMPROVING"
    elif slope < -0.05:
        trend = "DECAYING"
    else:
        trend = "STABLE"

    first_third = np.mean(rolling_sharpe[:len(rolling_sharpe) // 3])
    last_third = np.mean(rolling_sharpe[-len(rolling_sharpe) // 3:])

    return {
        "n_windows": len(rolling_sharpe),
        "trend": trend,
        "slope": round(slope, 4),
        "first_third_sharpe": round(first_third, 2),
        "last_third_sharpe": round(last_third, 2),
        "min_sharpe": round(min(rolling_sharpe), 2),
        "max_sharpe": round(max(rolling_sharpe), 2),
    }


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("  PHASE 1 : OPTIMISATIONS ANALYTIQUES")
    print("  Monte Carlo | Cost Sensitivity | Stationnarite")
    print("=" * 70)

    all_results = {}

    for name, csv_file in ACTIVE_STRATEGIES.items():
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

        trades = load_trades(csv_file)
        if trades.empty:
            print(f"  [SKIP] No trades found in {csv_file}")
            continue

        n_trades = len(trades)
        net_pnl = trades["net_pnl"].sum() if "net_pnl" in trades.columns else 0
        print(f"  Trades: {n_trades} | Net PnL: ${net_pnl:,.2f}")

        # 1. Monte Carlo
        mc = monte_carlo(trades)
        print(f"\n  --- Monte Carlo (1000 sims) ---")
        print(f"  Sharpe IC 90%: [{mc['sharpe_5']}, {mc['sharpe_50']}, {mc['sharpe_95']}]")
        print(f"  PnL IC 90%:    [${mc['pnl_5']:,.0f}, ${mc['pnl_50']:,.0f}, ${mc['pnl_95']:,.0f}]")
        print(f"  P(profitable): {mc['prob_positive']}%")

        # 2. Cost sensitivity
        cs = cost_sensitivity(trades)
        be = find_breakeven(cs)
        print(f"\n  --- Cost Sensitivity ---")
        print(f"  Break-even commission: ${be['breakeven_comm']:.3f}/share")
        print(f"  Break-even slippage:   {be['breakeven_slip_pct']:.3f}%")

        # Key cost scenarios
        for r in cs:
            if r["commission"] == 0.005 and abs(r["slippage_pct"] - 0.02) < 0.001:
                print(f"  Current costs ($0.005 + 0.02%): PnL ${r['net_pnl']:,.2f} ({r['return_pct']:+.3f}%)")
            if r["commission"] == 0.010 and abs(r["slippage_pct"] - 0.05) < 0.001:
                print(f"  Stress costs ($0.010 + 0.05%):  PnL ${r['net_pnl']:,.2f} ({r['return_pct']:+.3f}%)")
            if r["commission"] == 0 and r["slippage_pct"] == 0:
                print(f"  Zero costs:                     PnL ${r['net_pnl']:,.2f} ({r['return_pct']:+.3f}%)")

        # 3. Stationarity
        st = stationarity_check(trades)
        print(f"\n  --- Stationnarite (rolling {30}j) ---")
        print(f"  Trend:      {st['trend']} (slope={st.get('slope', 0):.4f})")
        print(f"  1er tiers:  Sharpe {st.get('first_third_sharpe', 0):.2f}")
        print(f"  3e tiers:   Sharpe {st.get('last_third_sharpe', 0):.2f}")

        all_results[name] = {
            "n_trades": n_trades,
            "net_pnl": net_pnl,
            "monte_carlo": mc,
            "breakeven": be,
            "stationarity": st,
        }

    # Summary table
    print(f"\n{'='*90}")
    print(f"  RESUME PHASE 1")
    print(f"{'='*90}")
    print(f"  {'Strategie':<30} {'Trades':>6} {'MC P(+)':>7} {'MC Sharpe 50%':>13} {'Trend':>10} {'BE Comm':>8}")
    print(f"  {'-'*30} {'-'*6} {'-'*7} {'-'*13} {'-'*10} {'-'*8}")
    for name, r in all_results.items():
        mc = r["monte_carlo"]
        st = r["stationarity"]
        be = r["breakeven"]
        print(f"  {name:<30} {r['n_trades']:>6} {mc['prob_positive']:>6.1f}% "
              f"{mc['sharpe_50']:>13.2f} {st['trend']:>10} ${be['breakeven_comm']:>7.3f}")

    print(f"\n{'='*90}")

    # Recommendations
    print("\n  RECOMMANDATIONS :")
    for name, r in all_results.items():
        mc = r["monte_carlo"]
        st = r["stationarity"]
        be = r["breakeven"]

        issues = []
        if mc["prob_positive"] < 60:
            issues.append(f"MC: seulement {mc['prob_positive']}% profitable")
        if mc["sharpe_5"] < 0:
            issues.append(f"MC: Sharpe 5e percentile negatif ({mc['sharpe_5']})")
        if st["trend"] == "DECAYING":
            issues.append("Edge en decay")
        if be["breakeven_comm"] <= 0.005:
            issues.append(f"Marge de cout fragile (BE comm=${be['breakeven_comm']:.3f})")

        if issues:
            print(f"  [!] {name}: {', '.join(issues)}")
        else:
            print(f"  [OK] {name}: robuste (MC {mc['prob_positive']}% +, Sharpe IC [{mc['sharpe_5']}, {mc['sharpe_95']}], {st['trend']})")


if __name__ == "__main__":
    main()
