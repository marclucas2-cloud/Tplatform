"""
EU Backtest Runner — execute toutes les strategies EU sur les donnees IBKR.

Usage:
    python run_eu_backtest.py                    # Run all strategies
    python run_eu_backtest.py --strategy eu_gap  # Run single strategy
    python run_eu_backtest.py --fetch-first      # Fetch data then backtest
    python run_eu_backtest.py --synthetic         # Use synthetic data (no IBKR needed)

Generates:
    - output/session_20260326/eu_results.json
    - output/session_20260326/EU_REPORT.md
"""
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import sys
import os
import json
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# Add project paths
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "strategies" / "eu"))

from eu_backtest_engine import (
    EUBacktestEngine, EU_INITIAL_CAPITAL, EU_COMMISSION_PCT, EU_SLIPPAGE_PCT,
)
from strategies.eu import (
    EUGapOpenStrategy,
    EULuxuryMomentumStrategy,
    EUEnergyBrentLagStrategy,
    EUCloseUSOpenStrategy,
    EUDayOfWeekStrategy,
    EUStoxxSPYReversionStrategy,
    EU_STRATEGIES,
)

# ── Paths ──
CACHE_DIR = SCRIPT_DIR / "data_cache" / "eu"
OUTPUT_DIR = SCRIPT_DIR.parent / "output" / "session_20260326"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Strategy registry ──
STRATEGY_MAP = {
    "eu_gap": EUGapOpenStrategy,
    "eu_luxury": EULuxuryMomentumStrategy,
    "eu_energy": EUEnergyBrentLagStrategy,
    "eu_close_us": EUCloseUSOpenStrategy,
    "eu_dow": EUDayOfWeekStrategy,
    "eu_reversion": EUStoxxSPYReversionStrategy,
}


def load_eu_data(use_synthetic: bool = False) -> dict[str, pd.DataFrame]:
    """Load EU data from parquet cache or generate synthetic data."""
    if use_synthetic:
        return generate_synthetic_data()

    data = {}
    parquet_files = list(CACHE_DIR.glob("*_1D.parquet"))

    if not parquet_files:
        print("[WARN] No EU parquet data found in cache. Using synthetic data.")
        print(f"       Run 'python fetch_eu_data.py' first to fetch real data from IBKR.")
        return generate_synthetic_data()

    for path in parquet_files:
        symbol = path.stem.replace("_1D", "")
        try:
            df = pd.read_parquet(path)
            if not df.empty:
                data[symbol] = df
                print(f"  [LOADED] {symbol}: {len(df)} daily bars")
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")

    # Also try to load intraday data
    intraday_files = list(CACHE_DIR.glob("*_15M.parquet"))
    for path in intraday_files:
        symbol = path.stem.replace("_15M", "")
        if symbol not in data:  # Don't overwrite daily with intraday
            try:
                df = pd.read_parquet(path)
                if not df.empty:
                    data[f"{symbol}_15M"] = df
            except Exception:
                pass

    if not data:
        print("[WARN] No data loaded from cache. Generating synthetic data.")
        return generate_synthetic_data()

    print(f"\n  [DATA] {len(data)} tickers loaded from EU cache")
    return data


def generate_synthetic_data() -> dict[str, pd.DataFrame]:
    """
    Generate realistic synthetic EU market data for backtesting.
    Based on typical EU stock characteristics (ATR, price ranges, volume).
    """
    print("\n[SYNTHETIC] Generating 1 year of synthetic EU data...")
    np.random.seed(42)

    # EU stock profiles: symbol -> (initial_price, daily_vol%, avg_volume, currency)
    profiles = {
        "MC":   (850, 1.5, 200_000, "EUR"),    # LVMH
        "SAP":  (210, 1.3, 1_500_000, "EUR"),   # SAP
        "ASML": (680, 1.8, 500_000, "EUR"),      # ASML
        "TTE":  (58, 1.2, 3_000_000, "EUR"),     # TotalEnergies
        "SIE":  (175, 1.3, 1_000_000, "EUR"),    # Siemens
        "ALV":  (265, 1.1, 400_000, "EUR"),      # Allianz
        "BNP":  (62, 1.4, 2_000_000, "EUR"),     # BNP Paribas
        "BMW":  (95, 1.3, 800_000, "EUR"),        # BMW
        "SHEL": (32, 1.1, 5_000_000, "EUR"),      # Shell
        "EXS1": (185, 0.9, 3_000_000, "EUR"),     # DAX ETF
        "ISF":  (8.5, 0.8, 10_000_000, "GBP"),    # FTSE ETF
    }

    # Generate 252 trading days (1 year)
    trading_days = pd.bdate_range(
        start=datetime.now() - timedelta(days=365),
        end=datetime.now(),
        freq="B",
    )

    data = {}
    for symbol, (init_price, daily_vol, avg_vol, _currency) in profiles.items():
        vol = daily_vol / 100

        # Generate returns with realistic properties:
        # - Slight positive drift (EU equity premium ~5% annual)
        # - Fat tails (kurtosis)
        # - Slight Monday negative bias
        # - Slight Friday positive bias
        # - Autocorrelation in volatility (GARCH-like)
        n = len(trading_days)
        drift = 0.05 / 252  # ~5% annual return

        # GARCH-like volatility
        vol_series = np.full(n, vol)
        for i in range(1, n):
            vol_series[i] = 0.9 * vol_series[i-1] + 0.1 * vol * (1 + np.random.randn() * 0.3)
            vol_series[i] = max(vol * 0.5, min(vol * 2.0, vol_series[i]))

        # Returns with day-of-week effect
        returns = np.random.randn(n) * vol_series + drift
        for i in range(n):
            dow = trading_days[i].weekday()
            if dow == 0:  # Monday
                returns[i] -= 0.0005  # Monday effect
            elif dow == 4:  # Friday
                returns[i] += 0.0003  # Friday effect

        # Apply fat tails (occasionally)
        for i in range(n):
            if np.random.rand() < 0.03:  # 3% chance of large move
                returns[i] *= np.random.choice([2.0, 2.5, 3.0])

        # Build prices
        prices = np.zeros(n)
        prices[0] = init_price
        for i in range(1, n):
            prices[i] = prices[i-1] * (1 + returns[i])

        # Build OHLCV with realistic open (near prev close + gap)
        closes = prices.copy()
        opens = np.zeros(n)
        opens[0] = prices[0] * (1 + np.random.randn() * 0.001)
        for i in range(1, n):
            # Open = prev close + overnight gap
            gap = np.random.randn() * vol_series[i] * 0.3  # Gap ~ 30% of daily vol
            opens[i] = closes[i-1] * (1 + gap)

        # Intraday range based on volatility
        highs = np.maximum(opens, closes) * (1 + abs(np.random.randn(n)) * vol_series * 0.5)
        lows = np.minimum(opens, closes) * (1 - abs(np.random.randn(n)) * vol_series * 0.5)

        # Volume with variability
        volumes = np.random.lognormal(np.log(avg_vol), 0.3, n).astype(int)
        # Monday and Friday volume slightly different
        for i in range(n):
            dow = trading_days[i].weekday()
            if dow == 0:
                volumes[i] = int(volumes[i] * 0.9)  # Lower Monday volume
            elif dow == 4:
                volumes[i] = int(volumes[i] * 1.1)  # Higher Friday volume

        # Ensure OHLC consistency
        highs = np.maximum(highs, np.maximum(opens, closes))
        lows = np.minimum(lows, np.minimum(opens, closes))

        df = pd.DataFrame({
            "open": opens.round(2),
            "high": highs.round(2),
            "low": lows.round(2),
            "close": closes.round(2),
            "volume": volumes,
        }, index=trading_days)

        data[symbol] = df
        print(f"  [SYNTH] {symbol}: {len(df)} bars, "
              f"{df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}")

    print(f"\n  [SYNTHETIC] {len(data)} tickers generated")
    return data


def calculate_eu_metrics(trades_df: pd.DataFrame, initial_capital: float) -> dict:
    """Calculate metrics adapted for EU strategies (includes cost analysis)."""
    if trades_df.empty:
        return {
            "total_return_pct": 0, "net_pnl": 0, "gross_pnl": 0,
            "total_commission": 0, "n_trades": 0, "n_trading_days": 0,
            "trades_per_day": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe_ratio": 0, "max_drawdown_pct": 0,
            "avg_winner": 0, "avg_loser": 0,
            "best_trade": 0, "worst_trade": 0,
            "avg_rr_ratio": 0, "avg_edge_per_trade_pct": 0,
            "total_cost_pct": 0, "cost_drag_pct": 0,
        }

    total_pnl = trades_df["pnl"].sum()
    total_commission = trades_df["commission"].sum()
    net_pnl = total_pnl - total_commission

    winners = trades_df[trades_df["net_pnl"] > 0]
    losers = trades_df[trades_df["net_pnl"] <= 0]

    win_rate = len(winners) / len(trades_df) * 100 if len(trades_df) > 0 else 0
    avg_winner = winners["net_pnl"].mean() if len(winners) > 0 else 0
    avg_loser = losers["net_pnl"].mean() if len(losers) > 0 else 0

    gross_profit = winners["net_pnl"].sum() if len(winners) > 0 else 0
    gross_loss = abs(losers["net_pnl"].sum()) if len(losers) > 0 else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    daily_pnl = trades_df.groupby("date")["net_pnl"].sum()
    daily_pnl = daily_pnl.sort_index()
    equity = initial_capital + daily_pnl.cumsum()

    # Max drawdown
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak * 100
    max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0

    # Sharpe ratio
    daily_returns = equity.pct_change().dropna()
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if len(daily_returns) > 1 and daily_returns.std() > 0
        else 0
    )

    total_return_pct = (net_pnl / initial_capital) * 100
    n_trades = len(trades_df)
    n_days = trades_df["date"].nunique()

    # Cost analysis
    avg_edge = (net_pnl / n_trades) / initial_capital * 100 * 100 if n_trades > 0 else 0
    total_cost_pct = (total_commission / initial_capital) * 100
    cost_drag = (total_commission / max(abs(total_pnl), 1)) * 100

    return {
        "total_return_pct": round(total_return_pct, 2),
        "net_pnl": round(net_pnl, 2),
        "gross_pnl": round(total_pnl, 2),
        "total_commission": round(total_commission, 2),
        "n_trades": n_trades,
        "n_trading_days": n_days,
        "trades_per_day": round(n_trades / max(n_days, 1), 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown_pct": round(float(max_dd), 2),
        "avg_winner": round(float(avg_winner), 2),
        "avg_loser": round(float(avg_loser), 2),
        "best_trade": round(float(trades_df["net_pnl"].max()), 2),
        "worst_trade": round(float(trades_df["net_pnl"].min()), 2),
        "avg_rr_ratio": round(abs(avg_winner / avg_loser), 2) if avg_loser != 0 else 0,
        "avg_edge_per_trade_pct": round(avg_edge, 3),
        "total_cost_pct": round(total_cost_pct, 3),
        "cost_drag_pct": round(cost_drag, 1),
    }


def run_walk_forward_eu(strategy_class, data, n_windows=4, is_days=60, oos_days=30):
    """Run walk-forward validation for EU strategy."""
    # Get all dates
    all_dates = set()
    for df in data.values():
        if hasattr(df.index, 'date'):
            all_dates.update(df.index.date)
        else:
            for idx in df.index:
                d = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()
                all_dates.add(d)
    all_dates = sorted(all_dates)

    if len(all_dates) < is_days + oos_days:
        return None

    # Build windows
    step = (len(all_dates) - is_days - oos_days) // max(n_windows - 1, 1)
    step = max(step, 20)

    windows = []
    i = 0
    while i + is_days + oos_days <= len(all_dates):
        oos_start = all_dates[i + is_days]
        oos_end = all_dates[min(i + is_days + oos_days - 1, len(all_dates) - 1)]
        windows.append((oos_start, oos_end))
        i += step

    if not windows:
        return None

    oos_results = []
    for oos_start, oos_end in windows:
        # Filter data to OOS window
        oos_data = {}
        for ticker, df in data.items():
            if hasattr(df.index, 'date'):
                mask = (df.index.date >= oos_start) & (df.index.date <= oos_end)
            else:
                mask = (df.index >= pd.Timestamp(oos_start)) & (df.index <= pd.Timestamp(oos_end))
            sub = df[mask]
            if not sub.empty:
                oos_data[ticker] = sub

        if not oos_data:
            continue

        strategy = strategy_class()
        engine = EUBacktestEngine(strategy, initial_capital=EU_INITIAL_CAPITAL)

        # Suppress prints
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        trades = engine.run(oos_data)
        sys.stdout = old_stdout

        metrics = calculate_eu_metrics(trades, EU_INITIAL_CAPITAL)
        oos_results.append({
            "oos_start": str(oos_start),
            "oos_end": str(oos_end),
            "return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe_ratio"],
            "pf": metrics["profit_factor"],
            "trades": metrics["n_trades"],
            "win_rate": metrics["win_rate"],
        })

    if not oos_results:
        return None

    profitable = sum(1 for r in oos_results if r["return_pct"] > 0)
    hit_rate = profitable / len(oos_results) * 100
    avg_return = np.mean([r["return_pct"] for r in oos_results])
    avg_sharpe = np.mean([r["sharpe"] for r in oos_results])

    return {
        "hit_rate": round(hit_rate, 0),
        "avg_return": round(avg_return, 2),
        "avg_sharpe": round(avg_sharpe, 2),
        "n_windows": len(oos_results),
        "profitable_windows": profitable,
        "verdict": "VALIDATED" if hit_rate >= 50 and avg_return > 0 else "REJECTED",
        "windows": oos_results,
    }


def print_metrics(name: str, metrics: dict):
    """Pretty print strategy metrics."""
    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    print(f"  Total Return:     {metrics['total_return_pct']:>8.2f}%")
    print(f"  Net P&L:          EUR {metrics['net_pnl']:>10,.2f}")
    print(f"  Trades:           {metrics['n_trades']:>8d}  ({metrics['trades_per_day']:.1f}/day)")
    print(f"  Win Rate:         {metrics['win_rate']:>8.1f}%")
    print(f"  Profit Factor:    {metrics['profit_factor']:>8.2f}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:>8.2f}")
    print(f"  Max Drawdown:     {metrics['max_drawdown_pct']:>8.2f}%")
    print(f"  Avg Winner:       EUR {metrics['avg_winner']:>10,.2f}")
    print(f"  Avg Loser:        EUR {metrics['avg_loser']:>10,.2f}")
    print(f"  Best Trade:       EUR {metrics['best_trade']:>10,.2f}")
    print(f"  Worst Trade:      EUR {metrics['worst_trade']:>10,.2f}")
    print(f"  R:R Ratio:        {metrics['avg_rr_ratio']:>8.2f}")
    print(f"  -- Cost Analysis --")
    print(f"  Total Commission: EUR {metrics['total_commission']:>10,.2f}")
    print(f"  Cost % Capital:   {metrics['total_cost_pct']:>8.3f}%")
    print(f"  Cost Drag:        {metrics['cost_drag_pct']:>8.1f}% of gross P&L")
    print(f"  Edge/Trade:       {metrics['avg_edge_per_trade_pct']:>8.3f}%")
    print(f"{'='*65}")


def generate_report(all_results: dict, output_dir: Path):
    """Generate EU_REPORT.md with all results."""
    lines = []
    lines.append("# EU Strategies Backtest Report")
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Capital**: EUR {EU_INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Costs**: {EU_COMMISSION_PCT*100:.2f}% commission + {EU_SLIPPAGE_PCT*100:.2f}% slippage = {(EU_COMMISSION_PCT+EU_SLIPPAGE_PCT)*100:.2f}% one-way")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Strategy | Sharpe | Return% | WR% | PF | Trades | DD% | Edge/Trade | WF Verdict |")
    lines.append("|----------|--------|---------|-----|----|--------|-----|------------|------------|")

    validated = []
    rejected = []

    for name, result in all_results.items():
        m = result["metrics"]
        wf = result.get("walk_forward", {})
        wf_verdict = wf.get("verdict", "N/A") if wf else "N/A"
        edge = m.get("avg_edge_per_trade_pct", 0)

        lines.append(
            f"| {name} | {m['sharpe_ratio']:.2f} | {m['total_return_pct']:.2f}% | "
            f"{m['win_rate']:.0f}% | {m['profit_factor']:.2f} | {m['n_trades']} | "
            f"{m['max_drawdown_pct']:.1f}% | {edge:.3f}% | {wf_verdict} |"
        )

        if m["sharpe_ratio"] > 0.5 and m["profit_factor"] > 1.2 and m["n_trades"] >= 15 and m["max_drawdown_pct"] < 10:
            validated.append(name)
        else:
            rejected.append(name)

    lines.append("")
    lines.append("## Validation Criteria")
    lines.append("- Sharpe > 0.5")
    lines.append("- Profit Factor > 1.2")
    lines.append("- Trades >= 15")
    lines.append("- Max Drawdown < 10%")
    lines.append("")

    # Validated strategies
    lines.append("## Validated Strategies")
    if validated:
        for name in validated:
            m = all_results[name]["metrics"]
            lines.append(f"- **{name}**: Sharpe {m['sharpe_ratio']:.2f}, PF {m['profit_factor']:.2f}, {m['n_trades']} trades")
    else:
        lines.append("*Aucune strategie ne passe tous les criteres.*")

    lines.append("")
    lines.append("## Rejected Strategies")
    for name in rejected:
        m = all_results[name]["metrics"]
        reasons = []
        if m["sharpe_ratio"] <= 0.5:
            reasons.append(f"Sharpe {m['sharpe_ratio']:.2f} <= 0.5")
        if m["profit_factor"] <= 1.2:
            reasons.append(f"PF {m['profit_factor']:.2f} <= 1.2")
        if m["n_trades"] < 15:
            reasons.append(f"Trades {m['n_trades']} < 15")
        if m["max_drawdown_pct"] >= 10:
            reasons.append(f"DD {m['max_drawdown_pct']:.1f}% >= 10%")
        lines.append(f"- **{name}**: {', '.join(reasons)}")

    # Cost analysis section
    lines.append("")
    lines.append("## Cost Impact Analysis")
    lines.append("")
    lines.append("EU commissions (0.10% + 0.03% slippage = 0.13% one-way, 0.26% round-trip) are ~20x higher than US Alpaca.")
    lines.append("Only strategies with edge > 0.3% per trade survive after costs.")
    lines.append("")
    lines.append("| Strategy | Gross P&L | Commission | Net P&L | Cost Drag % |")
    lines.append("|----------|-----------|------------|---------|-------------|")
    for name, result in all_results.items():
        m = result["metrics"]
        lines.append(
            f"| {name} | EUR {m['gross_pnl']:,.0f} | EUR {m['total_commission']:,.0f} | "
            f"EUR {m['net_pnl']:,.0f} | {m['cost_drag_pct']:.0f}% |"
        )

    # Walk-forward section
    lines.append("")
    lines.append("## Walk-Forward Validation")
    lines.append("")
    for name, result in all_results.items():
        wf = result.get("walk_forward")
        if wf:
            lines.append(f"### {name}")
            lines.append(f"- Hit rate: {wf['hit_rate']:.0f}% ({wf['profitable_windows']}/{wf['n_windows']} windows)")
            lines.append(f"- Avg OOS return: {wf['avg_return']:.2f}%")
            lines.append(f"- Avg OOS Sharpe: {wf['avg_sharpe']:.2f}")
            lines.append(f"- **Verdict: {wf['verdict']}**")
            lines.append("")
        else:
            lines.append(f"### {name}")
            lines.append("- Walk-forward: N/A (insufficient trades)")
            lines.append("")

    # Detailed per-strategy
    lines.append("## Detailed Results Per Strategy")
    lines.append("")
    for name, result in all_results.items():
        m = result["metrics"]
        lines.append(f"### {name}")
        lines.append(f"- Return: {m['total_return_pct']:.2f}%")
        lines.append(f"- Net P&L: EUR {m['net_pnl']:,.2f}")
        lines.append(f"- Trades: {m['n_trades']} ({m['trades_per_day']:.1f}/day)")
        lines.append(f"- Win Rate: {m['win_rate']:.1f}%")
        lines.append(f"- Profit Factor: {m['profit_factor']:.2f}")
        lines.append(f"- Sharpe: {m['sharpe_ratio']:.2f}")
        lines.append(f"- Max DD: {m['max_drawdown_pct']:.2f}%")
        lines.append(f"- R:R: {m['avg_rr_ratio']:.2f}")
        lines.append(f"- Commission total: EUR {m['total_commission']:,.2f}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by EU Backtest Runner on {datetime.now().isoformat()}*")

    report_path = output_dir / "EU_REPORT.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  [REPORT] {report_path}")
    return report_path


def main():
    parser = argparse.ArgumentParser(description="EU Backtest Runner")
    parser.add_argument("--strategy", type=str, default="all",
                        help="Strategy to run (eu_gap, eu_luxury, eu_energy, eu_close_us, eu_dow, eu_reversion, all)")
    parser.add_argument("--fetch-first", action="store_true",
                        help="Fetch data from IBKR before backtesting")
    parser.add_argument("--synthetic", action="store_true",
                        help="Use synthetic data (no IBKR connection needed)")
    parser.add_argument("--no-wf", action="store_true",
                        help="Skip walk-forward validation")
    args = parser.parse_args()

    print("=" * 65)
    print("  EU BACKTEST RUNNER")
    print("=" * 65)

    # ── Fetch data if requested ──
    if args.fetch_first:
        print("\n[PHASE 1] Fetching EU data from IBKR...")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "fetch_eu_data.py")],
            capture_output=True, text=True
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[WARN] Fetch failed: {result.stderr}")

    # ── Load data ──
    print("\n[DATA] Loading EU market data...")
    data = load_eu_data(use_synthetic=args.synthetic)

    if not data:
        print("[ERROR] No data available. Exiting.")
        sys.exit(1)

    # ── Select strategies ──
    if args.strategy == "all":
        strategies_to_run = STRATEGY_MAP
    elif args.strategy in STRATEGY_MAP:
        strategies_to_run = {args.strategy: STRATEGY_MAP[args.strategy]}
    else:
        print(f"[ERROR] Unknown strategy: {args.strategy}")
        print(f"  Available: {list(STRATEGY_MAP.keys())}")
        sys.exit(1)

    # ── Run backtests ──
    all_results = {}

    for key, strat_class in strategies_to_run.items():
        strategy = strat_class()

        # Filter data to required tickers
        required = strategy.get_required_tickers()
        strat_data = {}
        for ticker in required:
            if ticker in data:
                strat_data[ticker] = data[ticker]

        if not strat_data:
            # Use all available data if required tickers not found
            strat_data = data
            print(f"\n[WARN] {strategy.name}: required tickers not in cache, using all data")

        # Run backtest
        engine = EUBacktestEngine(strategy, initial_capital=EU_INITIAL_CAPITAL)
        trades_df = engine.run(strat_data)

        # Calculate metrics
        metrics = calculate_eu_metrics(trades_df, EU_INITIAL_CAPITAL)
        print_metrics(strategy.name, metrics)

        # Save trades CSV
        if not trades_df.empty:
            trades_path = OUTPUT_DIR / f"trades_eu_{key}.csv"
            trades_df.to_csv(trades_path, index=False)
            print(f"  [CSV] {trades_path}")

        # Walk-forward validation
        wf_result = None
        if not args.no_wf and metrics["n_trades"] >= 30:
            print(f"\n  [WF] Running walk-forward for {strategy.name}...")
            wf_result = run_walk_forward_eu(strat_class, strat_data)
            if wf_result:
                print(f"  [WF] Hit rate: {wf_result['hit_rate']:.0f}% "
                      f"({wf_result['profitable_windows']}/{wf_result['n_windows']}) "
                      f"-> {wf_result['verdict']}")

        all_results[strategy.name] = {
            "key": key,
            "metrics": metrics,
            "walk_forward": wf_result,
        }

    # ── Save results JSON ──
    results_json = {}
    for name, result in all_results.items():
        r = {
            "strategy": name,
            "key": result["key"],
            **result["metrics"],
        }
        if result["walk_forward"]:
            r["walk_forward"] = result["walk_forward"]
        results_json[name] = r

    json_path = OUTPUT_DIR / "eu_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results_json, f, indent=2, default=str)
    print(f"\n  [JSON] {json_path}")

    # ── Generate report ──
    report_path = generate_report(all_results, OUTPUT_DIR)

    # ── Final summary ──
    print("\n" + "=" * 65)
    print("  EU BACKTEST SUMMARY")
    print("=" * 65)

    for name, result in all_results.items():
        m = result["metrics"]
        status = "PASS" if (m["sharpe_ratio"] > 0.5 and m["profit_factor"] > 1.2
                           and m["n_trades"] >= 15 and m["max_drawdown_pct"] < 10) else "FAIL"
        wf_status = ""
        if result.get("walk_forward"):
            wf_status = f" | WF: {result['walk_forward']['verdict']}"
        print(f"  [{status}] {name:40s} Sharpe={m['sharpe_ratio']:>5.2f} | "
              f"PF={m['profit_factor']:>5.2f} | Ret={m['total_return_pct']:>6.2f}% | "
              f"Trades={m['n_trades']:>3d} | DD={m['max_drawdown_pct']:>5.1f}%{wf_status}")

    print(f"\n  Results: {json_path}")
    print(f"  Report:  {report_path}")
    print("=" * 65)


if __name__ == "__main__":
    main()
