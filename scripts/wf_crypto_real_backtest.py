"""
RE-WALK-FORWARD CRYPTO REEL — backtester event-driven (PROOF OF CONCEPT).

Remplace le wf_crypto_all.py buggy qui ne backtestait pas les strats
(daily_returns = closes BTCUSDT, soustraction couts -> tous les Sharpe
etaient B&H BTC ajustes par cost_per_trade * trade_freq).

STATUS 2026-04-18 (P0.2 audit):
  Ce script est un proof of concept architectural correct. Mais:
  - Performance O(n^2): chaque tick recompute compute_indicators sur df.iloc[:i],
    aucune cache. Pour 7 strats x 5 windows x 7K bars x 168-period rolling,
    >5 minutes par strat = >35 min total + bufferisation stdout.
  - Vol_breakout test 1 strat: 0 trades sur 5 fenetres (signal trop strict OU
    backtester ne reconstruit pas exactement le live behaviour).
  - A optimiser: pre-compute indicators une fois, passer slice etroit aux strats.

UTILE POUR:
  Documenter l'architecture event-driven correcte. NE PAS l'utiliser pour
  decisions live tant que pas valide trade-by-trade vs production.

PROCHAINE ITERATION:
  - Pre-compute indicators globaux
  - Strats receivent une vue read-only sur indicators precalcules
  - Cache compute_indicators
  - Validation: backtest historique vs trades live IBKR/Binance executes

Usage:
    python scripts/wf_crypto_real_backtest.py [--strat <name>]
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "crypto" / "candles"
REPORT_PATH = ROOT / "data" / "crypto" / "wf_results.json"

# Cost model Binance France: 0.10%/side commission + 3bps slippage = 0.23%/RT
COMMISSION_RATE = 0.0010
SLIPPAGE_RATE = 0.0003
COST_RT = 2 * (COMMISSION_RATE + SLIPPAGE_RATE)  # 0.0026 = 0.26%

# Strategies a re-WF (live et live_probation)
STRATS_TO_TEST = [
    {"id": "btc_eth_dual_momentum", "module": "strategies.crypto.btc_eth_dual_momentum",
     "tier": "TIER_1", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "vol_breakout", "module": "strategies.crypto.vol_breakout",
     "tier": "TIER_1", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "liquidation_momentum", "module": "strategies.crypto.liquidation_momentum",
     "tier": "LOW_FREQ", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "weekend_gap", "module": "strategies.crypto.weekend_gap",
     "tier": "LOW_FREQ", "primary_symbol": "BTCUSDT", "tf": "1d"},
    {"id": "vol_expansion_bear", "module": "strategies.crypto.vol_expansion_bear",
     "tier": "TIER_2", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "range_bb_harvest", "module": "strategies.crypto.range_bb_harvest",
     "tier": "TIER_2", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "bb_mr_short", "module": "strategies.crypto.bb_mr_short",
     "tier": "TIER_2", "primary_symbol": "BTCUSDT", "tf": "4h"},
    # Reference: strats not live but in wf_results buggy (control)
    {"id": "btc_dominance", "module": "strategies.crypto.btc_dominance_flight",
     "tier": "TIER_1", "primary_symbol": "BTCUSDT", "tf": "4h"},
    {"id": "btc_mean_reversion", "module": "strategies.crypto.btc_mean_reversion",
     "tier": "TIER_1", "primary_symbol": "BTCUSDT", "tf": "4h"},
]


@dataclass
class Position:
    side: str  # "LONG" or "SHORT"
    entry_price: float
    entry_time: pd.Timestamp
    entry_idx: int
    qty_pct: float          # fraction of capital allocated
    stop_loss: float
    take_profit: float | None = None
    trailing_atr: float | None = None
    highest: float = 0.0    # for LONG trailing
    lowest: float = float("inf")  # for SHORT trailing
    max_hold_days: int = 21
    direction: int = 1      # +1 LONG, -1 SHORT (compat with strats)


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    qty_pct: float
    pnl_pct: float       # net (after costs)
    holding_days: float
    exit_reason: str


def load_candles(symbol: str, tf: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_{tf}.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_parquet(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def _update_position(pos: Position, candle: pd.Series, idx: int,
                     ts_seconds_per_bar: int = 14400) -> tuple[bool, float, str] | None:
    """Update trailing stop and check exit. Returns (exit, exit_price, reason) or None."""
    high = candle["high"]
    low = candle["low"]

    if pos.side == "LONG":
        pos.highest = max(pos.highest, high)
        if pos.trailing_atr:
            new_sl = pos.highest - pos.trailing_atr
            pos.stop_loss = max(pos.stop_loss, new_sl)
        if low <= pos.stop_loss:
            return True, pos.stop_loss, "stop_loss"
        if pos.take_profit and high >= pos.take_profit:
            return True, pos.take_profit, "take_profit"
    else:  # SHORT
        pos.lowest = min(pos.lowest, low)
        if pos.trailing_atr:
            new_sl = pos.lowest + pos.trailing_atr
            pos.stop_loss = min(pos.stop_loss, new_sl)
        if high >= pos.stop_loss:
            return True, pos.stop_loss, "stop_loss"
        if pos.take_profit and low <= pos.take_profit:
            return True, pos.take_profit, "take_profit"

    # Max holding
    holding_secs = (idx - pos.entry_idx) * ts_seconds_per_bar
    holding_days = holding_secs / 86400
    if holding_days >= pos.max_hold_days:
        return True, candle["close"], "max_hold"

    return None


def _close_position(pos: Position, exit_price: float, exit_time: pd.Timestamp,
                    reason: str, idx: int, ts_seconds_per_bar: int = 14400) -> Trade:
    """Compute trade PnL net of costs."""
    if pos.side == "LONG":
        gross_pct = (exit_price - pos.entry_price) / pos.entry_price
    else:  # SHORT
        gross_pct = (pos.entry_price - exit_price) / pos.entry_price

    pnl_pct = (gross_pct - COST_RT) * pos.qty_pct

    holding_days = (idx - pos.entry_idx) * ts_seconds_per_bar / 86400

    return Trade(
        entry_time=pos.entry_time.isoformat() if hasattr(pos.entry_time, "isoformat") else str(pos.entry_time),
        exit_time=exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time),
        side=pos.side,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        qty_pct=pos.qty_pct,
        pnl_pct=pnl_pct,
        holding_days=round(holding_days, 1),
        exit_reason=reason,
    )


def backtest_strategy(strat_info: dict, df: pd.DataFrame,
                      start_idx: int = 0, end_idx: int | None = None) -> tuple[list[Trade], pd.Series]:
    """Run event-driven backtest on slice df[start_idx:end_idx]."""
    if end_idx is None:
        end_idx = len(df)

    try:
        mod = importlib.import_module(strat_info["module"])
    except ImportError as e:
        return [], pd.Series(dtype=float, name="equity")

    if not hasattr(mod, "signal_fn"):
        return [], pd.Series(dtype=float, name="equity")

    signal_fn = mod.signal_fn

    state: dict = {"i": 0, "positions": [], "capital": 10000.0, "equity": 10000.0}
    trades: list[Trade] = []
    daily_pnl_pct: dict[pd.Timestamp, float] = {}

    bar_seconds = 14400 if strat_info["tf"] == "4h" else 86400  # 4h or 1d

    for i in range(max(start_idx, 100), end_idx):  # skip first 100 bars for indicators warmup
        candle = df.iloc[i]
        state["i"] = i
        ts = candle.get("timestamp", pd.Timestamp.now(tz="UTC"))

        # Update existing position (trailing, SL, TP)
        if state["positions"]:
            pos = state["positions"][0]
            res = _update_position(pos, candle, i, bar_seconds)
            if res is not None:
                _, exit_price, reason = res
                trade = _close_position(pos, exit_price, ts, reason, i, bar_seconds)
                trades.append(trade)
                date_key = pd.Timestamp(ts).normalize()
                daily_pnl_pct[date_key] = daily_pnl_pct.get(date_key, 0.0) + trade.pnl_pct
                state["positions"] = []

        # Get strategy signal
        try:
            sig = signal_fn(
                candle, state,
                df_full=df.iloc[:i + 1],
                borrow_rate=0.0003,
            )
        except Exception:
            sig = None

        if sig is None:
            continue

        action = sig.get("action")

        if action == "CLOSE" and state["positions"]:
            pos = state["positions"][0]
            exit_price = candle["close"]
            trade = _close_position(pos, exit_price, ts, sig.get("reason", "signal_close"), i, bar_seconds)
            trades.append(trade)
            date_key = pd.Timestamp(ts).normalize()
            daily_pnl_pct[date_key] = daily_pnl_pct.get(date_key, 0.0) + trade.pnl_pct
            state["positions"] = []

        elif action in ("BUY", "SELL") and not state["positions"]:
            entry_price = candle["close"]
            qty_pct = sig.get("pct", 0.10)
            sl = sig.get("stop_loss")
            tp = sig.get("take_profit")
            trailing_atr = sig.get("trailing_stop_atr")

            side = "LONG" if action == "BUY" else "SHORT"
            if sl is None:
                sl = entry_price * 0.95 if side == "LONG" else entry_price * 1.05

            pos = Position(
                side=side,
                entry_price=entry_price,
                entry_time=ts,
                entry_idx=i,
                qty_pct=qty_pct,
                stop_loss=sl,
                take_profit=tp,
                trailing_atr=trailing_atr,
                highest=entry_price,
                lowest=entry_price,
                direction=1 if side == "LONG" else -1,
            )
            state["positions"] = [pos]

    # Close any remaining position at last bar
    if state["positions"]:
        last_candle = df.iloc[end_idx - 1]
        pos = state["positions"][0]
        ts = last_candle.get("timestamp", pd.Timestamp.now(tz="UTC"))
        trade = _close_position(pos, last_candle["close"], ts, "end_of_period", end_idx - 1, bar_seconds)
        trades.append(trade)
        date_key = pd.Timestamp(ts).normalize()
        daily_pnl_pct[date_key] = daily_pnl_pct.get(date_key, 0.0) + trade.pnl_pct

    daily_series = pd.Series(daily_pnl_pct, name="daily_pnl_pct").sort_index()
    return trades, daily_series


def compute_metrics(trades: list[Trade], daily_pnl: pd.Series) -> dict:
    if not trades:
        return {"sharpe": 0.0, "n_trades": 0, "total_pnl_pct": 0.0,
                "win_rate": 0.0, "profitable": False, "max_dd_pct": 0.0,
                "avg_hold": 0.0}

    n = len(trades)
    pnl = sum(t.pnl_pct for t in trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    wr = wins / n
    avg_hold = sum(t.holding_days for t in trades) / n

    # Sharpe sur daily pnl
    if len(daily_pnl) > 1 and daily_pnl.std() > 0:
        sharpe = float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(365))
    else:
        sharpe = 0.0

    # Max DD
    equity = (1.0 + daily_pnl.cumsum()).reindex(
        pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="D")
    ).fillna(method="ffill").fillna(1.0)
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0.0

    return {
        "sharpe": round(sharpe, 3),
        "n_trades": n,
        "total_pnl_pct": round(pnl, 4),
        "win_rate": round(wr, 3),
        "profitable": pnl > 0,
        "max_dd_pct": round(max_dd, 4),
        "avg_hold": round(avg_hold, 1),
    }


def walk_forward(strat_info: dict, df: pd.DataFrame,
                 train_months: float = 6.0, test_months: float = 2.0,
                 max_windows: int = 5) -> dict:
    """Walk-forward 5 fenetres rolling."""
    tf_per_day = 6 if strat_info["tf"] == "4h" else 1
    train_bars = int(train_months * 30 * tf_per_day)
    test_bars = int(test_months * 30 * tf_per_day)

    n = len(df)
    n_windows = min(max_windows, (n - train_bars) // test_bars)

    if n_windows < 1:
        return {
            "verdict": "NO_DATA",
            "error": f"insufficient data: {n} bars, need {train_bars + test_bars}",
            "windows": [],
        }

    windows = []
    for w in range(n_windows):
        is_start = w * test_bars
        is_end = is_start + train_bars
        oos_start = is_end
        oos_end = min(oos_start + test_bars, n)

        if oos_end - oos_start < 30:
            continue

        # Run on full slice from start to oos_end (signals need history)
        trades, daily_pnl = backtest_strategy(strat_info, df, 0, oos_end)
        # Filter trades to OOS window
        oos_start_ts = df.iloc[oos_start].get("timestamp", pd.NaT)
        oos_end_ts = df.iloc[oos_end - 1].get("timestamp", pd.NaT)
        oos_trades = [
            t for t in trades
            if pd.Timestamp(t.entry_time) >= oos_start_ts
            and pd.Timestamp(t.entry_time) <= oos_end_ts
        ]
        oos_daily = daily_pnl.loc[oos_start_ts:oos_end_ts] if not daily_pnl.empty else daily_pnl

        m = compute_metrics(oos_trades, oos_daily)
        windows.append({
            "window_idx": w,
            "oos_start": str(oos_start_ts.date()) if oos_start_ts is not pd.NaT else "",
            "oos_end": str(oos_end_ts.date()) if oos_end_ts is not pd.NaT else "",
            **m,
        })

    if not windows:
        return {"verdict": "NO_VALID_WINDOWS", "windows": []}

    # Aggregate
    avg_sharpe = float(np.mean([w["sharpe"] for w in windows]))
    profitable_ratio = sum(1 for w in windows if w["profitable"]) / len(windows)
    total_trades = sum(w["n_trades"] for w in windows)

    # Verdict
    if total_trades < 5:
        verdict = "INSUFFICIENT_TRADES"
    elif avg_sharpe >= 1.0 and profitable_ratio >= 0.6:
        verdict = "VALIDATED"
    elif avg_sharpe >= 0.5 and profitable_ratio >= 0.5:
        verdict = "BORDERLINE"
    else:
        verdict = "REJECTED"

    return {
        "verdict": verdict,
        "avg_oos_sharpe": round(avg_sharpe, 3),
        "profitable_windows": int(profitable_ratio * len(windows)),
        "total_windows": len(windows),
        "profitable_ratio": round(profitable_ratio, 3),
        "total_oos_trades": total_trades,
        "windows": windows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strat", help="Run only one strategy by id")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    strats = STRATS_TO_TEST
    if args.strat:
        strats = [s for s in strats if s["id"] == args.strat]
        if not strats:
            print(f"Unknown strategy: {args.strat}")
            return

    print("=" * 80)
    print(f"  RE-WALK-FORWARD CRYPTO REEL — {len(strats)} stratégies")
    print(f"  Cost model: {COST_RT*100:.2f}%/RT (commission {COMMISSION_RATE*100:.2f}% + slip {SLIPPAGE_RATE*100:.2f}%)")
    print("=" * 80)

    results = {}
    for strat in strats:
        print(f"\n[{strat['id']}] {strat['module']} ({strat['tf']})")
        try:
            df = load_candles(strat["primary_symbol"], strat["tf"])
        except FileNotFoundError as e:
            print(f"  NO DATA: {e}")
            results[strat["id"]] = {
                "strategy_name": strat["id"],
                "tier": strat["tier"],
                "verdict": "NO_DATA",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "windows": [],
            }
            continue

        print(f"  Loaded {len(df)} bars ({df.iloc[0].get('timestamp')} -> {df.iloc[-1].get('timestamp')})")

        try:
            wf = walk_forward(strat, df)
        except Exception as e:
            import traceback
            traceback.print_exc()
            wf = {"verdict": "ERROR", "error": str(e), "windows": []}

        print(f"  VERDICT: {wf['verdict']} | "
              f"Sharpe {wf.get('avg_oos_sharpe', 0):.2f} | "
              f"WF {wf.get('profitable_windows', 0)}/{wf.get('total_windows', 0)} | "
              f"trades {wf.get('total_oos_trades', 0)}")
        for w in wf.get("windows", []):
            tag = "PROFIT" if w["profitable"] else "LOSS"
            print(f"    W{w['window_idx']} [{w['oos_start']} -> {w['oos_end']}]: "
                  f"{w['n_trades']:3d} trades | Sharpe {w['sharpe']:+.2f} | "
                  f"PnL {w['total_pnl_pct']*100:+.2f}% | WR {w['win_rate']:.0%} | "
                  f"DD {w['max_dd_pct']*100:.2f}% | {tag}")

        results[strat["id"]] = {
            "strategy_name": strat["id"],
            "tier": strat["tier"],
            "verdict": wf["verdict"],
            "avg_oos_sharpe": wf.get("avg_oos_sharpe", 0),
            "profitable_windows": wf.get("profitable_windows", 0),
            "total_windows": wf.get("total_windows", 0),
            "profitable_ratio": wf.get("profitable_ratio", 0),
            "total_oos_trades": wf.get("total_oos_trades", 0),
            "error": wf.get("error", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "windows": wf.get("windows", []),
            "validation_method": "event_driven_real_backtest_v2_2026-04-18",
        }

    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"{'Strategy':<35} {'Verdict':<22} {'Sharpe':>7} {'WF':>6} {'Trades':>6}")
    print(f"{'-'*80}")
    for k, v in results.items():
        wf = f"{v['profitable_windows']}/{v['total_windows']}"
        print(f"{k:<35} {v['verdict']:<22} {v.get('avg_oos_sharpe', 0):>7.2f} {wf:>6} {v['total_oos_trades']:>6}")

    # Save (preserve existing schema for compat)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
