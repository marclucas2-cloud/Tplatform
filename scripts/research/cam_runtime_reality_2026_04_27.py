#!/usr/bin/env python3
"""Revalidate futures CAM on fresh data and quantify runtime-vs-design mismatch.

This research script keeps the signal selection logic aligned with the live
strategy (`strategies_v2/futures/cross_asset_momentum.py`) and compares:

1. Intended product: 20-day rebalance, 3% SL, 8% TP.
2. Runtime product: 48h time-exit only.
3. Runtime product: 48h time-exit plus 3%/8% bracket.

The goal is not to replace the canonical live runner. It is to make explicit
that the live runtime cap changes the economic product even when the fresh-data
revalidation is otherwise clean.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.research.wf_canonical import classify_grade

OUT_PATH = ROOT / "data" / "research" / "cam_runtime_reality_2026-04-27.json"
MANIFEST_PATH = ROOT / "data" / "research" / "wf_manifests" / "cross_asset_momentum_runtime48h_2026-04-27.json"

UNIVERSE = ["MES", "MNQ", "M2K", "MGC", "MCL"]
LOOKBACK_DAYS = 20
MIN_MOMENTUM = 0.02
REBAL_DAYS = 20
SL_PCT = 0.03
TP_PCT = 0.08
COST_BPS_ROUND_TRIP = 5


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    bars_held: int
    entry_price: float
    exit_price: float
    exit_reason: str
    net_return_pct: float


def load_daily(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    if isinstance(df.index, pd.DatetimeIndex) and df.index.notna().any():
        idx = pd.to_datetime(df.index)
    elif "datetime" in df.columns:
        idx = pd.to_datetime(df["datetime"])
    else:
        idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def load_universe() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    data = {symbol: load_daily(symbol) for symbol in UNIVERSE}
    common = None
    for df in data.values():
        common = df.index if common is None else common.intersection(df.index)
    assert common is not None
    aligned = {symbol: df.loc[common].copy() for symbol, df in data.items()}
    return aligned, common


def first_runtime_exit_idx(index: pd.DatetimeIndex, entry_idx: int) -> int:
    """Approximate the live 48h cap.

    Entry occurs on the daily cycle day. The runner exits on the first weekday
    cycle after >=48h, which on business-day bars means:
      - Mon -> Wed
      - Tue -> Thu
      - Wed -> Fri
      - Thu -> Mon
      - Fri -> Mon
    """
    deadline = index[entry_idx] + pd.Timedelta(hours=48)
    for idx in range(entry_idx + 1, len(index)):
        if index[idx] >= deadline:
            return idx
    return len(index) - 1


def simulate_intended_fixed(df: pd.DataFrame, entry_idx: int, entry_price: float) -> tuple[int, float, str]:
    stop = entry_price * (1 - SL_PCT)
    target = entry_price * (1 + TP_PCT)
    last_idx = min(entry_idx + REBAL_DAYS, len(df) - 1)

    for idx in range(entry_idx + 1, last_idx + 1):
        bar = df.iloc[idx]
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])

        if bar_open <= stop:
            return idx, bar_open, "SL"
        if bar_open >= target:
            return idx, bar_open, "TP"
        if bar_low <= stop:
            return idx, stop, "SL"
        if bar_high >= target:
            return idx, target, "TP"

    return last_idx, float(df.iloc[last_idx]["close"]), "REBAL"


def simulate_runtime_48h(
    df: pd.DataFrame,
    index: pd.DatetimeIndex,
    entry_idx: int,
    entry_price: float,
    with_bracket: bool,
) -> tuple[int, float, str]:
    stop = entry_price * (1 - SL_PCT)
    target = entry_price * (1 + TP_PCT)
    exit_idx = first_runtime_exit_idx(index, entry_idx)

    for idx in range(entry_idx + 1, exit_idx + 1):
        bar = df.iloc[idx]
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])

        if with_bracket:
            if bar_open <= stop:
                return idx, bar_open, "SL"
            if bar_open >= target:
                return idx, bar_open, "TP"
            if idx < exit_idx:
                if bar_low <= stop:
                    return idx, stop, "SL"
                if bar_high >= target:
                    return idx, target, "TP"

        if idx == exit_idx:
            return idx, bar_open, "TIME_48H"

    return exit_idx, float(df.iloc[exit_idx]["open"]), "TIME_48H"


def run_variant(mode: str) -> list[Trade]:
    data, common = load_universe()
    closes = pd.DataFrame({symbol: df["close"] for symbol, df in data.items()})

    trades: list[Trade] = []
    idx = LOOKBACK_DAYS + 1
    while idx < len(common) - 1:
        returns = {}
        for symbol in UNIVERSE:
            ret = closes[symbol].iloc[idx] / closes[symbol].iloc[idx - LOOKBACK_DAYS] - 1
            if np.isfinite(ret):
                returns[symbol] = float(ret)

        if not returns:
            idx += 1
            continue

        winner = max(returns, key=returns.get)
        if returns[winner] < MIN_MOMENTUM:
            idx += 1
            continue

        entry_price = float(closes[winner].iloc[idx])
        if mode == "intended_20d":
            exit_idx, exit_price, reason = simulate_intended_fixed(data[winner], idx, entry_price)
        elif mode == "runtime_48h":
            exit_idx, exit_price, reason = simulate_runtime_48h(
                data[winner], common, idx, entry_price, with_bracket=False
            )
        elif mode == "runtime_48h_with_bracket":
            exit_idx, exit_price, reason = simulate_runtime_48h(
                data[winner], common, idx, entry_price, with_bracket=True
            )
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        net_return = (exit_price / entry_price - 1) - (COST_BPS_ROUND_TRIP / 10_000)
        trades.append(
            Trade(
                symbol=winner,
                entry_date=str(common[idx].date()),
                exit_date=str(common[exit_idx].date()),
                bars_held=exit_idx - idx,
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                exit_reason=reason,
                net_return_pct=round(net_return * 100, 4),
            )
        )
        idx = max(idx + REBAL_DAYS, exit_idx + 1)

    return trades


def summarise(trades: list[Trade], years: float) -> dict:
    if not trades:
        return {
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "sharpe": 0.0,
            "cagr_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "exit_counts": {},
            "per_symbol_exit_counts": {},
        }

    returns = np.array([t.net_return_pct / 100 for t in trades], dtype=float)
    equity = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1
    avg_spacing_factor = np.sqrt(252 / REBAL_DAYS)
    sharpe = (returns.mean() / returns.std() * avg_spacing_factor) if returns.std() > 0 else 0.0
    cagr = equity[-1] ** (1 / years) - 1

    exits = pd.Series([t.exit_reason for t in trades]).value_counts().to_dict()
    per_symbol = (
        pd.DataFrame([asdict(t) for t in trades])
        .groupby("symbol")["exit_reason"]
        .value_counts()
        .unstack(fill_value=0)
        .to_dict("index")
    )

    return {
        "num_trades": len(trades),
        "win_rate_pct": round(float((returns > 0).mean() * 100), 1),
        "sharpe": round(float(sharpe), 2),
        "cagr_pct": round(float(cagr * 100), 2),
        "max_drawdown_pct": round(float(-drawdown.min() * 100), 2),
        "exit_counts": exits,
        "per_symbol_exit_counts": per_symbol,
    }


def window_summaries(trades: list[Trade], index: pd.DatetimeIndex, n_windows: int = 5) -> list[dict]:
    """Build simple chronological windows for the assumed 48h runtime product.

    No train/test optimization is performed here: parameters are fixed and the
    aim is to measure stability across time, not parameter selection quality.
    """
    windows: list[dict] = []
    window_days = len(index) // n_windows
    if window_days <= 0:
        return windows

    for window_idx in range(n_windows):
        start = index[window_idx * window_days]
        end = index[min((window_idx + 1) * window_days - 1, len(index) - 1)]
        subset = [t for t in trades if start <= pd.Timestamp(t.exit_date) <= end]
        returns = np.array([t.net_return_pct / 100 for t in subset], dtype=float)
        if len(returns) > 1 and returns.std() > 0:
            sharpe = float(returns.mean() / returns.std() * np.sqrt(252 / REBAL_DAYS))
        else:
            sharpe = 0.0
        total_return = float((np.prod(1 + returns) - 1) * 100) if len(returns) else 0.0
        profitable = bool(total_return > 0 and sharpe > 0)
        windows.append(
            {
                "window": window_idx + 1,
                "period": f"{start.date()}..{end.date()}",
                "n": int(len(subset)),
                "sharpe": round(sharpe, 2),
                "total_return_pct": round(total_return, 2),
                "profitable": profitable,
            }
        )
    return windows


def build_payload() -> dict:
    _, common = load_universe()
    years = (common[-1] - common[0]).days / 365.25
    variants = {}
    for mode in ["intended_20d", "runtime_48h", "runtime_48h_with_bracket"]:
        trades = run_variant(mode)
        variants[mode] = {
            "summary": summarise(trades, years),
            "sample_trades": [asdict(t) for t in trades[:5]],
        }
        if mode == "runtime_48h_with_bracket":
            variants[mode]["windows"] = window_summaries(trades, common)
    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "period": {
            "start": str(common[0].date()),
            "end": str(common[-1].date()),
            "years": round(years, 2),
        },
        "params": {
            "universe": UNIVERSE,
            "lookback_days": LOOKBACK_DAYS,
            "min_momentum": MIN_MOMENTUM,
            "rebal_days": REBAL_DAYS,
            "stop_loss_pct": SL_PCT,
            "take_profit_pct": TP_PCT,
            "cost_bps_round_trip": COST_BPS_ROUND_TRIP,
            "entry_model": "same_day_close_proxy_for_cycle_fill",
        },
        "variants": variants,
    }


def build_runtime_manifest(payload: dict) -> dict:
    runtime_variant = payload["variants"]["runtime_48h_with_bracket"]
    windows = runtime_variant["windows"]
    sharpe_values = sorted(w["sharpe"] for w in windows)
    median_sharpe = sharpe_values[len(sharpe_values) // 2] if sharpe_values else 0.0
    pass_count = sum(1 for w in windows if w["profitable"])
    total_windows = len(windows)
    pass_rate = (pass_count / total_windows) if total_windows else 0.0
    grade = classify_grade(pass_rate=pass_rate, median_sharpe=median_sharpe, dsr_pvalue=None)

    return {
        "schema_version": 2,
        "run_id": "cam_runtime48h_2026-04-27",
        "strategy_id": "cross_asset_momentum",
        "variant": "runtime_48h_with_bracket",
        "source": "scripts/research/cam_runtime_reality_2026_04_27.py",
        "source_data": "data/futures/{MES,MNQ,M2K,MGC,MCL}_1D.parquet",
        "finished_at": payload["generated_at"],
        "params": {
            **payload["params"],
            "assumed_runtime_exit_cap_hours": 48,
            "classification_windows": total_windows,
        },
        "windows": windows,
        "summary": {
            "windows_pass": pass_count,
            "windows_total": total_windows,
            "pass_rate": round(pass_rate, 2),
            "median_sharpe": round(float(median_sharpe), 2),
            "grade": grade,
            "verdict": "VALIDATED" if grade != "REJECTED" else "REJECTED",
            **runtime_variant["summary"],
        },
    }


def main() -> int:
    payload = build_payload()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(build_runtime_manifest(payload), indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(f"Wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
