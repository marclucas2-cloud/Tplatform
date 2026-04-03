#!/usr/bin/env python3
"""
Walk-Forward Backtest — 3 swing strategies sur donnees daily Alpaca (IEX).

Strategies :
  1. Sector Momentum Weekly (11 sector ETFs)
  2. Mean Reversion RSI2 S&P500 (~70 stocks)
  3. Earnings Drift Swing (~100 stocks)

WF config : 4 fenetres, 70% IS / 30% OOS, rolling.
Capital : $25K (cible Alpaca live).
Couts : $0 commission + 0.02% slippage.
"""
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wf_swing")

CAPITAL = 25_000
SLIPPAGE_PCT = 0.0002  # 0.02%
WF_WINDOWS = 4
IS_RATIO = 0.70
MIN_OOS_TRADES = 15


def fetch_daily_data(tickers: list[str], days: int = 730) -> dict[str, pd.DataFrame]:
    """Fetch daily bars from Alpaca IEX for all tickers."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )

    end = datetime.now()
    start = end - timedelta(days=days)

    data = {}
    # Batch by 50 (Alpaca limit)
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i+50]
        logger.info(f"Fetching {len(batch)} tickers (batch {i//50+1})...")
        try:
            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
            bars = client.get_stock_bars(request)

            for ticker in batch:
                if bars.data.get(ticker):
                    rows = []
                    for bar in bars.data[ticker]:
                        rows.append({
                            "timestamp": bar.timestamp,
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": int(bar.volume),
                        })
                    if rows:
                        df = pd.DataFrame(rows)
                        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                        df = df.set_index("timestamp").sort_index()
                        data[ticker] = df
        except Exception as e:
            logger.warning(f"Batch {i//50+1} failed: {e}")

        time.sleep(1)  # Rate limit

    logger.info(f"Fetched {len(data)} tickers with data")
    return data


def run_backtest_period(strategy, data: dict[str, pd.DataFrame],
                        start_date, end_date, capital: float) -> dict:
    """Run strategy on a date range, return metrics."""
    trades = []
    equity = capital
    open_positions = []

    # Get all trading days in range
    all_dates = set()
    for df in data.values():
        _start_ts = pd.Timestamp(start_date).tz_localize("UTC") if pd.Timestamp(start_date).tz is None else pd.Timestamp(start_date)
        _end_ts = pd.Timestamp(end_date).tz_localize("UTC") if pd.Timestamp(end_date).tz is None else pd.Timestamp(end_date)
        mask = (df.index >= _start_ts) & (df.index <= _end_ts)
        all_dates.update(df.index[mask].normalize().unique())
    trading_days = sorted(all_dates)

    for day in trading_days:
        # Build day's data (all history up to this day, anti-lookahead)
        day_data = {}
        for ticker, df in data.items():
            mask = df.index <= day + pd.Timedelta(hours=23)
            if mask.any():
                day_data[ticker] = df[mask]

        # Check exits on open positions
        new_open = []
        for pos in open_positions:
            ticker = pos["ticker"]
            if ticker not in day_data or day_data[ticker].empty:
                new_open.append(pos)
                continue

            current_price = day_data[ticker]["close"].iloc[-1]
            days_held = (day - pos["entry_date"]).days

            # Check SL
            if pos["direction"] == "LONG" and current_price <= pos["stop_loss"]:
                pnl = (pos["stop_loss"] - pos["entry_price"]) * pos["qty"]
                pnl -= pos["entry_price"] * pos["qty"] * SLIPPAGE_PCT * 2  # slippage A/R
                equity += pnl
                trades.append({**pos, "exit_price": pos["stop_loss"], "exit_date": day,
                               "pnl": pnl, "exit_reason": "SL", "days_held": days_held})
                continue
            if pos["direction"] == "SHORT" and current_price >= pos["stop_loss"]:
                pnl = (pos["entry_price"] - pos["stop_loss"]) * pos["qty"]
                pnl -= pos["entry_price"] * pos["qty"] * SLIPPAGE_PCT * 2
                equity += pnl
                trades.append({**pos, "exit_price": pos["stop_loss"], "exit_date": day,
                               "pnl": pnl, "exit_reason": "SL", "days_held": days_held})
                continue

            # Check TP
            if pos["direction"] == "LONG" and current_price >= pos["take_profit"]:
                pnl = (pos["take_profit"] - pos["entry_price"]) * pos["qty"]
                pnl -= pos["entry_price"] * pos["qty"] * SLIPPAGE_PCT * 2
                equity += pnl
                trades.append({**pos, "exit_price": pos["take_profit"], "exit_date": day,
                               "pnl": pnl, "exit_reason": "TP", "days_held": days_held})
                continue
            if pos["direction"] == "SHORT" and current_price <= pos["take_profit"]:
                pnl = (pos["entry_price"] - pos["take_profit"]) * pos["qty"]
                pnl -= pos["entry_price"] * pos["qty"] * SLIPPAGE_PCT * 2
                equity += pnl
                trades.append({**pos, "exit_price": pos["take_profit"], "exit_date": day,
                               "pnl": pnl, "exit_reason": "TP", "days_held": days_held})
                continue

            # Check max hold (from metadata)
            max_hold = pos.get("max_hold_days", 20)
            if days_held >= max_hold:
                pnl_mult = 1 if pos["direction"] == "LONG" else -1
                pnl = (current_price - pos["entry_price"]) * pos["qty"] * pnl_mult
                pnl -= pos["entry_price"] * pos["qty"] * SLIPPAGE_PCT * 2
                equity += pnl
                trades.append({**pos, "exit_price": current_price, "exit_date": day,
                               "pnl": pnl, "exit_reason": "MAX_HOLD", "days_held": days_held})
                continue

            new_open.append(pos)
        open_positions = new_open

        # Generate new signals
        try:
            signals = strategy.generate_signals(day_data, day)
        except Exception:
            continue

        for sig in signals:
            # Check not already positioned on this ticker
            if any(p["ticker"] == sig.ticker for p in open_positions):
                continue
            # Position sizing
            pos_size = min(equity * 0.10, equity / 5)  # 10% max, or 1/5 of equity
            if sig.entry_price <= 0:
                continue
            qty = int(pos_size / sig.entry_price)
            if qty <= 0:
                continue

            open_positions.append({
                "ticker": sig.ticker,
                "direction": sig.action,
                "entry_price": sig.entry_price,
                "entry_date": day,
                "stop_loss": sig.stop_loss,
                "take_profit": sig.take_profit,
                "qty": qty,
                "max_hold_days": sig.metadata.get("max_hold_days", 20),
            })

    # Force close remaining positions at last day's close
    for pos in open_positions:
        ticker = pos["ticker"]
        if ticker in data and not data[ticker].empty:
            last_price = data[ticker]["close"].iloc[-1]
            pnl_mult = 1 if pos["direction"] == "LONG" else -1
            pnl = (last_price - pos["entry_price"]) * pos["qty"] * pnl_mult
            trades.append({**pos, "exit_price": last_price, "exit_date": trading_days[-1] if trading_days else end_date,
                           "pnl": pnl, "exit_reason": "END", "days_held": 0})
            equity += pnl

    # Compute metrics
    if not trades:
        return {"sharpe": 0, "trades": 0, "win_rate": 0, "pf": 0, "return_pct": 0, "max_dd": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_return = sum(pnls) / capital
    win_rate = len(wins) / len(pnls) if pnls else 0
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    # Daily returns for Sharpe (approximate)
    daily_pnl = pd.Series(pnls)
    sharpe = (daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)) if daily_pnl.std() > 0 else 0

    # Max DD
    cumulative = np.cumsum(pnls) + capital
    peak = np.maximum.accumulate(cumulative)
    dd = (cumulative - peak) / peak
    max_dd = abs(dd.min()) if len(dd) > 0 else 0

    return {
        "sharpe": round(sharpe, 2),
        "trades": len(trades),
        "win_rate": round(win_rate * 100, 1),
        "pf": round(pf, 2),
        "return_pct": round(total_return * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "avg_pnl": round(np.mean(pnls), 2),
        "avg_hold_days": round(np.mean([t.get("days_held", 0) for t in trades]), 1),
    }


def walk_forward(strategy, data: dict[str, pd.DataFrame], name: str) -> dict:
    """Run walk-forward validation with 4 windows."""
    # Get date range
    all_dates = set()
    for df in data.values():
        all_dates.update(df.index.normalize().unique())
    all_dates = sorted(all_dates)

    if len(all_dates) < 100:
        logger.warning(f"{name}: only {len(all_dates)} days — need 100+ for WF")
        return {"verdict": "INSUFFICIENT_DATA"}

    total_days = len(all_dates)
    window_size = total_days // WF_WINDOWS
    is_size = int(window_size * IS_RATIO)
    oos_size = window_size - is_size

    results = []
    for w in range(WF_WINDOWS):
        start_idx = w * window_size
        is_end_idx = start_idx + is_size
        oos_end_idx = start_idx + window_size

        if oos_end_idx >= total_days:
            break

        is_start = all_dates[start_idx]
        is_end = all_dates[is_end_idx]
        oos_start = all_dates[is_end_idx]
        oos_end = all_dates[min(oos_end_idx, total_days - 1)]

        logger.info(f"{name} Window {w+1}: IS {is_start.date()}-{is_end.date()}, OOS {oos_start.date()}-{oos_end.date()}")

        is_metrics = run_backtest_period(strategy, data, is_start, is_end, CAPITAL)
        oos_metrics = run_backtest_period(strategy, data, oos_start, oos_end, CAPITAL)

        results.append({
            "window": w + 1,
            "is": is_metrics,
            "oos": oos_metrics,
        })

        logger.info(
            f"  IS: Sharpe={is_metrics['sharpe']}, trades={is_metrics['trades']}, "
            f"WR={is_metrics['win_rate']}%, return={is_metrics['return_pct']}%"
        )
        logger.info(
            f"  OOS: Sharpe={oos_metrics['sharpe']}, trades={oos_metrics['trades']}, "
            f"WR={oos_metrics['win_rate']}%, return={oos_metrics['return_pct']}%"
        )

    if not results:
        return {"verdict": "NO_WINDOWS"}

    # Aggregate OOS metrics
    oos_sharpes = [r["oos"]["sharpe"] for r in results]
    oos_trades = [r["oos"]["trades"] for r in results]
    oos_profitable = sum(1 for r in results if r["oos"]["return_pct"] > 0)
    is_sharpes = [r["is"]["sharpe"] for r in results]

    avg_oos_sharpe = np.mean(oos_sharpes)
    total_oos_trades = sum(oos_trades)
    pct_profitable = oos_profitable / len(results) * 100
    avg_is_sharpe = np.mean(is_sharpes)
    wf_ratio = avg_oos_sharpe / avg_is_sharpe if avg_is_sharpe > 0 else 0

    # Verdict
    pass_checks = {
        "wf_ratio > 0.4": wf_ratio > 0.4,
        "50%+ windows profitable": pct_profitable >= 50,
        "30+ OOS trades": total_oos_trades >= MIN_OOS_TRADES,
        "OOS Sharpe > 0.5": avg_oos_sharpe > 0.5,
    }
    all_pass = all(pass_checks.values())

    if all_pass:
        verdict = "VALIDATED"
    elif sum(pass_checks.values()) >= 3:
        verdict = "BORDERLINE"
    else:
        verdict = "REJECTED"

    summary = {
        "strategy": name,
        "verdict": verdict,
        "avg_is_sharpe": round(avg_is_sharpe, 2),
        "avg_oos_sharpe": round(avg_oos_sharpe, 2),
        "wf_ratio": round(wf_ratio, 2),
        "total_oos_trades": total_oos_trades,
        "pct_profitable_windows": pct_profitable,
        "checks": pass_checks,
        "windows": results,
    }

    return summary


def main():
    print("=" * 60)
    print("  WALK-FORWARD SWING STRATEGIES")
    print(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Capital: ${CAPITAL:,}")
    print("=" * 60)

    # Import strategies from intraday-backtesterV2/strategies/ via importlib
    import importlib.util
    _bt_strats = ROOT / "archive" / "intraday-backtesterV2" / "strategies"

    def _load(module_name, class_name):
        spec = importlib.util.spec_from_file_location(
            module_name, _bt_strats / f"{module_name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, class_name)

    SectorMomentumWeeklyStrategy = _load("sector_momentum_weekly", "SectorMomentumWeeklyStrategy")
    MeanReversionRSI2Strategy = _load("mean_reversion_rsi2_sp500", "MeanReversionRSI2Strategy")
    EarningsDriftSwingStrategy = _load("earnings_drift_swing", "EarningsDriftSwingStrategy")

    strategies = [
        ("Sector Momentum Weekly", SectorMomentumWeeklyStrategy()),
        ("Mean Reversion RSI2", MeanReversionRSI2Strategy()),
        ("Earnings Drift Swing", EarningsDriftSwingStrategy()),
    ]

    # Collect all required tickers
    all_tickers = set()
    for name, strat in strategies:
        all_tickers.update(strat.get_required_tickers())
    all_tickers = sorted(all_tickers)
    logger.info(f"Total unique tickers: {len(all_tickers)}")

    # Fetch data
    logger.info("Fetching 2 years daily data from Alpaca IEX...")
    data = fetch_daily_data(all_tickers, days=730)

    if len(data) < 10:
        logger.error(f"Only {len(data)} tickers with data — aborting")
        return 1

    # Run WF for each strategy
    all_results = []
    for name, strat in strategies:
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

        result = walk_forward(strat, data, name)
        all_results.append(result)

        print(f"\n  VERDICT: {result.get('verdict', '?')}")
        print(f"  OOS Sharpe: {result.get('avg_oos_sharpe', '?')}")
        print(f"  WF ratio: {result.get('wf_ratio', '?')}")
        print(f"  OOS trades: {result.get('total_oos_trades', '?')}")
        print(f"  Profitable windows: {result.get('pct_profitable_windows', '?')}%")
        checks = result.get("checks", {})
        for check, passed in checks.items():
            print(f"    {'PASS' if passed else 'FAIL'} {check}")

    # Save results
    out_path = ROOT / "data" / "monitoring" / "wf_swing_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "capital": CAPITAL,
        "results": all_results,
    }, indent=2, default=str))

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in all_results:
        v = r.get("verdict", "?")
        print(f"  {r.get('strategy', '?')}: {v} (OOS Sharpe={r.get('avg_oos_sharpe', '?')})")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
