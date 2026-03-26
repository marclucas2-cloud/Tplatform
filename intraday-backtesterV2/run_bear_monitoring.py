"""
Phase 1 — Bear Market Monitoring.
Analyse la performance de chaque strategie en regime BEAR vs BULL.
Bear = jours ou SPY close < SMA200.
"""
import sys, os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
import config
from utils.metrics import calculate_metrics

OUTPUT_DIR = Path(__file__).parent / "output"

# Load SPY daily data to determine regime per day
def get_regime_days():
    """Retourne 2 sets : bear_days, bull_days (dates) via yfinance SPY 2Y."""
    try:
        ROOT = Path(__file__).parent.parent
        sys.path.insert(0, str(ROOT))
        from core.data.loader import OHLCVLoader
        data = OHLCVLoader.from_yfinance("SPY", "1D", period="2y")
        close = data.df["close"]
        sma200 = close.rolling(200).mean()
        daily = pd.DataFrame({"close": close, "sma200": sma200})
        daily["bear"] = daily["close"] < daily["sma200"]
        # Filter to last 6 months
        cutoff = datetime.now() - timedelta(days=200)
        daily = daily[daily.index >= cutoff]
        bear_days = set(daily[daily["bear"]].index.date)
        bull_days = set(daily[~daily["bear"]].index.date)
        print(f"Regime (yfinance): {len(bear_days)} bear days, {len(bull_days)} bull days (last 6 months)")
        return bear_days, bull_days
    except Exception as e:
        print(f"ERROR loading SPY: {e}")
        # Fallback: use SMA50 from cache
        cache_dir = Path(__file__).parent / "data_cache"
        spy_files = sorted(cache_dir.glob("SPY_5Min_*.parquet"))
        if not spy_files:
            return set(), set()
        spy = pd.read_parquet(spy_files[-1])
        daily = spy.groupby(spy.index.date).agg(close=("close", "last"))
        daily["sma50"] = daily["close"].rolling(50, min_periods=20).mean()
        daily["bear"] = daily["close"] < daily["sma50"]
        bear_days = set(daily[daily["bear"]].index)
        bull_days = set(daily[~daily["bear"]].index)
        print(f"Regime (SMA50 fallback): {len(bear_days)} bear days, {len(bull_days)} bull days")
        return bear_days, bull_days


# Strategy trade CSVs
STRATEGY_CSVS = {
    "OpEx Gamma Pin": "trades_opex_gamma_pin.csv",
    "Overnight Gap": "trades_overnight_gap_continuation.csv",
    "Crypto-Proxy V2": "trades_crypto-proxy_regime_switch.csv",
    "Day-of-Week": "trades_day-of-week_seasonal.csv",
    "Late Day MR": "trades_late_day_mean_reversion.csv",
    "VWAP Micro": "trades_vwap_micro_deviation.csv",
    "Triple EMA": "trades_triple_ema_pullback.csv",
    "Midday Reversal": "trades_midday_reversal.csv",
    "Gold Fear Gauge": "trades_gold_fear_gauge.csv",
    "Corr Regime Hedge": "trades_correlation_regime_hedge.csv",
}


def analyze_strategy_by_regime(name, csv_file, bear_days, bull_days):
    path = OUTPUT_DIR / csv_file
    if not path.exists():
        return None

    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns:
        return None

    df["date"] = pd.to_datetime(df["date"]).dt.date

    bear_trades = df[df["date"].isin(bear_days)]
    bull_trades = df[df["date"].isin(bull_days)]

    def metrics(trades):
        if trades.empty or "net_pnl" not in trades.columns:
            return {"sharpe": 0, "pnl": 0, "trades": 0, "wr": 0, "pf": 0}
        m = calculate_metrics(trades, config.INITIAL_CAPITAL)
        return {
            "sharpe": m.get("sharpe_ratio", 0),
            "pnl": m.get("net_pnl", 0),
            "trades": m.get("n_trades", 0),
            "wr": m.get("win_rate", 0),
            "pf": m.get("profit_factor", 0),
        }

    bear_m = metrics(bear_trades)
    bull_m = metrics(bull_trades)

    # Classify
    if bear_m["sharpe"] > bull_m["sharpe"] * 1.0 and bear_m["sharpe"] > 0:
        category = "BEAR WINNER"
    elif bear_m["sharpe"] >= bull_m["sharpe"] * 0.7:
        category = "BEAR NEUTRAL"
    elif bear_m["sharpe"] >= 0:
        category = "BEAR LOSER"
    else:
        category = "BEAR KILLER"

    return {
        "name": name,
        "bull": bull_m,
        "bear": bear_m,
        "category": category,
    }


def main():
    bear_days, bull_days = get_regime_days()
    if not bear_days:
        print("No bear days detected — skipping analysis")
        return

    print(f"\n{'='*90}")
    print(f"  BEAR MARKET MONITORING — Performance par regime")
    print(f"{'='*90}")

    results = []
    for name, csv in STRATEGY_CSVS.items():
        r = analyze_strategy_by_regime(name, csv, bear_days, bull_days)
        if r:
            results.append(r)

    # Table
    print(f"\n  {'Strategie':<22} {'Sharpe Bull':>11} {'Sharpe Bear':>11} {'Delta':>7} {'Bear Trades':>11} {'Bear PnL':>10} {'Category':>14}")
    print(f"  {'-'*22} {'-'*11} {'-'*11} {'-'*7} {'-'*11} {'-'*10} {'-'*14}")

    for r in sorted(results, key=lambda x: -x["bear"]["sharpe"]):
        b = r["bull"]
        br = r["bear"]
        delta = br["sharpe"] - b["sharpe"]
        print(f"  {r['name']:<22} {b['sharpe']:>11.2f} {br['sharpe']:>11.2f} {delta:>+7.2f} {br['trades']:>11} ${br['pnl']:>9.0f} {r['category']:>14}")

    # Summary
    bear_winners = [r for r in results if r["category"] == "BEAR WINNER"]
    bear_killers = [r for r in results if r["category"] == "BEAR KILLER"]
    print(f"\n  BEAR WINNERS ({len(bear_winners)}): {', '.join(r['name'] for r in bear_winners) or 'None'}")
    print(f"  BEAR KILLERS ({len(bear_killers)}): {', '.join(r['name'] for r in bear_killers) or 'None'}")

    # Recommendations
    print(f"\n  RECOMMANDATIONS:")
    for r in results:
        if r["category"] == "BEAR KILLER":
            print(f"    [!] {r['name']}: AUTO-PAUSE en bear (Sharpe bear {r['bear']['sharpe']:.2f})")
        elif r["category"] == "BEAR WINNER":
            print(f"    [+] {r['name']}: AMPLIFIER en bear +30% allocation")

    # Save
    import json
    output_path = Path(__file__).parent.parent / "output" / "session_20260326"
    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "bear_monitoring.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved to {output_path / 'bear_monitoring.json'}")


if __name__ == "__main__":
    main()
