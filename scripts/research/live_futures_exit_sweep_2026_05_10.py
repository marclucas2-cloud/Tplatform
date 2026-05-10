#!/usr/bin/env python3
"""Parameter and exit-policy sweep for live IBKR futures strategies.

Live futures strategies covered:
- cross_asset_momentum (CAM)
- gold_oil_rotation (GOR)

The sweep keeps each strategy's entry logic recognizable and compares:
- relative-gain thresholds (CAM min momentum / GOR min edge)
- native max hold vs 48h cap
- Friday close exit
- trailing SL
- trailing TP
- trailing SL + trailing TP

Research-only. No broker or runtime state is touched.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "reports" / "research"
JSON_DIR = ROOT / "data" / "research"

UNIVERSE_CAM = ["MES", "MNQ", "M2K", "MGC", "MCL"]
UNIVERSE_GOR = ["MGC", "MCL"]

SPECS = {
    "MES": {"mult": 5.0, "cost": 2.49},
    "MNQ": {"mult": 2.0, "cost": 2.49},
    "M2K": {"mult": 5.0, "cost": 2.49},
    "MGC": {"mult": 10.0, "cost": 2.49},
    "MCL": {"mult": 100.0, "cost": 2.49},
}

CAM_LOOKBACK = 20
CAM_REBAL_DAYS = 20
CAM_SL_PCT = 0.03
CAM_TP_PCT = 0.08

GOR_LOOKBACK = 20
GOR_COOLDOWN_DAYS = 10
GOR_SL_PCT = 0.02
GOR_TP_PCT = 0.04

REL_GAIN_GRID = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05]

EXIT_POLICIES = [
    {
        "label": "native_fixed",
        "cap_48h": False,
        "friday_close": False,
        "trail_sl": False,
        "trail_tp": False,
    },
    {
        "label": "fixed_48h",
        "cap_48h": True,
        "friday_close": False,
        "trail_sl": False,
        "trail_tp": False,
    },
    {
        "label": "fixed_48h_friday",
        "cap_48h": True,
        "friday_close": True,
        "trail_sl": False,
        "trail_tp": False,
    },
    {
        "label": "trail_sl_48h_friday",
        "cap_48h": True,
        "friday_close": True,
        "trail_sl": True,
        "trail_tp": False,
    },
    {
        "label": "trail_tp_48h_friday",
        "cap_48h": True,
        "friday_close": True,
        "trail_sl": False,
        "trail_tp": True,
    },
    {
        "label": "trail_sl_tp_48h_friday",
        "cap_48h": True,
        "friday_close": True,
        "trail_sl": True,
        "trail_tp": True,
    },
]


@dataclass
class Trade:
    strategy: str
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str
    rel_gain_threshold: float
    exit_policy: str


def load_daily(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "futures" / f"{symbol}_1D.parquet"
    df = pd.read_parquet(path)
    df.columns = [str(c).lower() for c in df.columns]
    if "datetime" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        idx = pd.to_datetime(df["datetime"])
    else:
        idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def load_symbols(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    data = {sym: load_daily(sym) for sym in symbols}
    common: pd.DatetimeIndex | None = None
    for df in data.values():
        common = df.index if common is None else common.intersection(df.index)
    if common is None:
        raise RuntimeError("No data loaded")
    common = common.sort_values()
    return {sym: df.loc[common].copy() for sym, df in data.items()}, common


def first_48h_exit_idx(index: pd.DatetimeIndex, entry_idx: int) -> int:
    deadline = index[entry_idx] + pd.Timedelta(hours=48)
    for idx in range(entry_idx + 1, len(index)):
        if index[idx] >= deadline:
            return idx
    return len(index) - 1


def exit_limit_idx(
    index: pd.DatetimeIndex,
    entry_idx: int,
    native_hold_bars: int,
    policy: dict[str, Any],
) -> int:
    native_idx = min(entry_idx + native_hold_bars - 1, len(index) - 1)
    if not bool(policy["cap_48h"]):
        return native_idx
    return min(native_idx, first_48h_exit_idx(index, entry_idx))


def simulate_long_exit(
    df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    sl_pct: float,
    tp_pct: float,
    native_hold_bars: int,
    policy: dict[str, Any],
) -> tuple[int, float, str]:
    current_sl = entry_price * (1.0 - sl_pct)
    current_tp = entry_price * (1.0 + tp_pct)
    high_watermark = entry_price
    max_idx = exit_limit_idx(df.index, entry_idx, native_hold_bars, policy)

    for idx in range(entry_idx, max_idx + 1):
        row = df.iloc[idx]
        opn = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if idx > entry_idx:
            if opn <= current_sl:
                return idx, opn, "gap_sl"
            if opn >= current_tp:
                return idx, opn, "gap_tp"

        # Conservative same-bar ordering for long trades: stop before target.
        if low <= current_sl:
            return idx, current_sl, "sl"
        if high >= current_tp:
            return idx, current_tp, "tp"

        if bool(policy["friday_close"]) and df.index[idx].weekday() == 4:
            return idx, close, "friday_close"

        high_watermark = max(high_watermark, high)
        if bool(policy["trail_sl"]):
            current_sl = max(current_sl, high_watermark * (1.0 - sl_pct))
        if bool(policy["trail_tp"]):
            current_tp = max(current_tp, high_watermark * (1.0 + tp_pct))

        if idx == max_idx:
            return idx, close, "time_exit_48h" if bool(policy["cap_48h"]) else "time_exit_native"

    return max_idx, float(df["close"].iloc[max_idx]), "time_exit"


def pnl_for(symbol: str, entry_price: float, exit_price: float) -> tuple[float, float]:
    spec = SPECS[symbol]
    pnl = (exit_price - entry_price) * float(spec["mult"]) - float(spec["cost"])
    ret_pct = exit_price / entry_price - 1.0
    return pnl, ret_pct


def run_gor(rel_gain: float, policy: dict[str, Any]) -> list[Trade]:
    data, common = load_symbols(UNIVERSE_GOR)
    mgc_ret = data["MGC"]["close"].pct_change(GOR_LOOKBACK)
    mcl_ret = data["MCL"]["close"].pct_change(GOR_LOOKBACK)
    trades: list[Trade] = []
    idx = GOR_LOOKBACK
    last_signal_idx = -10_000

    while idx < len(common) - GOR_COOLDOWN_DAYS:
        if idx - last_signal_idx < GOR_COOLDOWN_DAYS:
            idx += 1
            continue
        spread = float(mgc_ret.iloc[idx] - mcl_ret.iloc[idx])
        if not np.isfinite(spread) or abs(spread) < rel_gain:
            idx += 1
            continue
        symbol = "MGC" if spread > 0 else "MCL"
        df = data[symbol]
        signal_date = common[idx]
        entry_idx = df.index.get_loc(signal_date) + 1
        if entry_idx + GOR_COOLDOWN_DAYS >= len(df):
            break
        entry_price = float(df["open"].iloc[entry_idx])
        exit_idx, exit_price, reason = simulate_long_exit(
            df=df,
            entry_idx=entry_idx,
            entry_price=entry_price,
            sl_pct=GOR_SL_PCT,
            tp_pct=GOR_TP_PCT,
            native_hold_bars=GOR_COOLDOWN_DAYS,
            policy=policy,
        )
        pnl, ret_pct = pnl_for(symbol, entry_price, exit_price)
        trades.append(
            Trade(
                strategy="gold_oil_rotation",
                symbol=symbol,
                entry_date=str(common[entry_idx].date()),
                exit_date=str(common[exit_idx].date()),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                pnl=round(pnl, 4),
                return_pct=round(ret_pct * 100, 4),
                bars_held=exit_idx - entry_idx,
                exit_reason=reason,
                rel_gain_threshold=rel_gain,
                exit_policy=str(policy["label"]),
            )
        )
        last_signal_idx = idx
        idx += 1
    return trades


def run_cam(rel_gain: float, policy: dict[str, Any]) -> list[Trade]:
    data, common = load_symbols(UNIVERSE_CAM)
    closes = pd.DataFrame({sym: data[sym]["close"] for sym in UNIVERSE_CAM})
    returns = closes.pct_change(CAM_LOOKBACK)
    trades: list[Trade] = []
    idx = CAM_LOOKBACK

    while idx < len(common) - CAM_REBAL_DAYS - 2:
        row = returns.iloc[idx]
        if row.notna().sum() == 0:
            idx += 1
            continue
        symbol = str(row.idxmax())
        momentum = float(row[symbol])
        if not np.isfinite(momentum) or momentum < rel_gain:
            idx += 1
            continue

        df = data[symbol]
        entry_idx = idx
        entry_price = float(df["close"].iloc[entry_idx])
        exit_idx, exit_price, reason = simulate_long_exit(
            df=df,
            entry_idx=entry_idx,
            entry_price=entry_price,
            sl_pct=CAM_SL_PCT,
            tp_pct=CAM_TP_PCT,
            native_hold_bars=CAM_REBAL_DAYS,
            policy=policy,
        )
        pnl, ret_pct = pnl_for(symbol, entry_price, exit_price)
        trades.append(
            Trade(
                strategy="cross_asset_momentum",
                symbol=symbol,
                entry_date=str(common[entry_idx].date()),
                exit_date=str(common[exit_idx].date()),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                pnl=round(pnl, 4),
                return_pct=round(ret_pct * 100, 4),
                bars_held=exit_idx - entry_idx,
                exit_reason=reason,
                rel_gain_threshold=rel_gain,
                exit_policy=str(policy["label"]),
            )
        )
        idx = max(idx + CAM_REBAL_DAYS, exit_idx + 1)
    return trades


def compute_stats(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "sharpe_trade": 0.0,
            "profit_factor": 0.0,
            "max_dd": 0.0,
            "avg_bars_held": 0.0,
            "exit_counts": {},
            "symbol_counts": {},
        }
    pnl = np.array([t.pnl for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    cum = np.cumsum(pnl)
    peaks = np.maximum.accumulate(cum)
    drawdowns = cum - peaks
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252 / 10)) if pnl.std() > 0 else 0.0
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.size else float("inf")
    return {
        "n": int(len(trades)),
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "sharpe_trade": sharpe,
        "profit_factor": profit_factor,
        "max_dd": float(drawdowns.min()),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        "exit_counts": pd.Series([t.exit_reason for t in trades]).value_counts().to_dict(),
        "symbol_counts": pd.Series([t.symbol for t in trades]).value_counts().to_dict(),
    }


def wf_stats(trades: list[Trade], all_dates: pd.DatetimeIndex, n_windows: int = 5) -> dict[str, Any]:
    if not trades:
        return {"profitable_windows": 0, "total_pnl": 0.0, "mean_sharpe": 0.0, "windows": []}
    window_days = len(all_dates) // n_windows
    windows = []
    for w in range(n_windows):
        start = all_dates[w * window_days]
        end = all_dates[min((w + 1) * window_days - 1, len(all_dates) - 1)]
        subset = [t for t in trades if start <= pd.Timestamp(t.exit_date) <= end]
        st = compute_stats(subset)
        windows.append(
            {
                "window": w + 1,
                "period": f"{start.date()}..{end.date()}",
                "n": st["n"],
                "total_pnl": round(st["total_pnl"], 2),
                "sharpe": round(st["sharpe_trade"], 2),
                "profitable": bool(st["total_pnl"] > 0),
            }
        )
    sharpe_values = [float(w["sharpe"]) for w in windows if int(w["n"]) > 0]
    return {
        "profitable_windows": sum(1 for w in windows if w["profitable"]),
        "total_pnl": round(sum(float(w["total_pnl"]) for w in windows), 2),
        "mean_sharpe": round(float(np.mean(sharpe_values)), 2) if sharpe_values else 0.0,
        "windows": windows,
    }


def md_table(df: pd.DataFrame) -> str:
    headers = [str(c) for c in df.columns]
    rows = [[str(v) for v in row] for row in df.to_numpy().tolist()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def run_sweep() -> tuple[list[dict[str, Any]], dict[str, list[Trade]]]:
    all_results: list[dict[str, Any]] = []
    trade_blobs: dict[str, list[Trade]] = {}
    cam_data, cam_dates = load_symbols(UNIVERSE_CAM)
    gor_data, gor_dates = load_symbols(UNIVERSE_GOR)
    del cam_data, gor_data

    for strategy in ["cross_asset_momentum", "gold_oil_rotation"]:
        for rel_gain in REL_GAIN_GRID:
            for policy in EXIT_POLICIES:
                if strategy == "cross_asset_momentum":
                    trades = run_cam(rel_gain, policy)
                    dates = cam_dates
                else:
                    trades = run_gor(rel_gain, policy)
                    dates = gor_dates
                stats = compute_stats(trades)
                wf = wf_stats(trades, dates)
                key = f"{strategy}|{rel_gain:.2f}|{policy['label']}"
                trade_blobs[key] = trades
                all_results.append(
                    {
                        "strategy": strategy,
                        "rel_gain_threshold": rel_gain,
                        "exit_policy": policy["label"],
                        "n": stats["n"],
                        "total_pnl": round(stats["total_pnl"], 2),
                        "avg_pnl": round(stats["avg_pnl"], 2),
                        "win_rate_pct": round(stats["win_rate"] * 100, 1),
                        "sharpe": round(stats["sharpe_trade"], 2),
                        "profit_factor": round(stats["profit_factor"], 2)
                        if np.isfinite(stats["profit_factor"])
                        else "inf",
                        "max_dd": round(stats["max_dd"], 2),
                        "avg_bars_held": round(stats["avg_bars_held"], 2),
                        "wf_profitable_windows": wf["profitable_windows"],
                        "wf_total_pnl": wf["total_pnl"],
                        "wf_mean_sharpe": wf["mean_sharpe"],
                        "exit_counts": stats["exit_counts"],
                        "symbol_counts": stats["symbol_counts"],
                    }
                )
    return all_results, trade_blobs


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    results, trade_blobs = run_sweep()
    df = pd.DataFrame(results)
    report_path = REPORT_DIR / "live_futures_exit_sweep_2026-05-10.md"
    json_path = JSON_DIR / "live_futures_exit_sweep_2026-05-10.json"

    sort_cols = ["wf_profitable_windows", "sharpe", "total_pnl"]
    top_cam = (
        df[df["strategy"] == "cross_asset_momentum"]
        .sort_values(sort_cols, ascending=[False, False, False])
        .head(12)
        .reset_index(drop=True)
    )
    top_gor = (
        df[df["strategy"] == "gold_oil_rotation"]
        .sort_values(sort_cols, ascending=[False, False, False])
        .head(12)
        .reset_index(drop=True)
    )
    baseline_rows = df[
        (
            (df["strategy"] == "cross_asset_momentum")
            & (df["rel_gain_threshold"] == 0.02)
            & (df["exit_policy"] == "fixed_48h")
        )
        | (
            (df["strategy"] == "gold_oil_rotation")
            & (df["rel_gain_threshold"] == 0.02)
            & (df["exit_policy"] == "native_fixed")
        )
    ].reset_index(drop=True)

    serializable_trades = {
        key: [asdict(t) for t in trades[:20]]
        for key, trades in trade_blobs.items()
    }
    json_payload = {
        "metadata": {
            "created_at": "2026-05-10",
            "strategies": ["cross_asset_momentum", "gold_oil_rotation"],
            "rel_gain_grid": REL_GAIN_GRID,
            "exit_policies": EXIT_POLICIES,
            "notes": [
                "CAM entry uses same-day close, aligned with prior runtime reality research.",
                "GOR entry uses next-open, aligned with existing wf_gold_oil_rotation.py.",
                "Friday close exits at the daily close of any Friday while a trade is open.",
                "Trailing SL/TP ratchets from the high watermark and is only active after the current bar.",
            ],
        },
        "results": results,
        "sample_trades": serializable_trades,
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

    lines = [
        "# Live Futures Exit Sweep - 2026-05-10",
        "",
        "## Scope",
        "- Strategies: `cross_asset_momentum` and `gold_oil_rotation`.",
        "- Parameter swept: relative-gain threshold (`min_momentum` for CAM, `min_edge` for GOR).",
        "- Exit policies: native fixed, 48h cap, 48h + Friday close, trailing SL, trailing TP, trailing SL+TP.",
        "- Research only: no runtime, broker, state, or config changes.",
        "",
        "## Baselines",
        md_table(
            baseline_rows[
                [
                    "strategy",
                    "rel_gain_threshold",
                    "exit_policy",
                    "n",
                    "total_pnl",
                    "win_rate_pct",
                    "sharpe",
                    "max_dd",
                    "wf_profitable_windows",
                    "wf_mean_sharpe",
                ]
            ]
        ),
        "",
        "## Top CAM Configs",
        md_table(
            top_cam[
                [
                    "rel_gain_threshold",
                    "exit_policy",
                    "n",
                    "total_pnl",
                    "win_rate_pct",
                    "sharpe",
                    "max_dd",
                    "avg_bars_held",
                    "wf_profitable_windows",
                    "wf_mean_sharpe",
                ]
            ]
        ),
        "",
        "## Top GOR Configs",
        md_table(
            top_gor[
                [
                    "rel_gain_threshold",
                    "exit_policy",
                    "n",
                    "total_pnl",
                    "win_rate_pct",
                    "sharpe",
                    "max_dd",
                    "avg_bars_held",
                    "wf_profitable_windows",
                    "wf_mean_sharpe",
                ]
            ]
        ),
        "",
        "## Interpretation Guardrails",
        "- Same-bar ordering is conservative for long trades: SL is assumed hit before TP if both are inside the daily range.",
        "- CAM dollar PnL is one contract per trade across heterogeneous micro futures; compare policies within CAM, not absolute dollars vs GOR.",
        "- GOR next-open entry and CAM same-close entry intentionally match their existing research harnesses.",
        "- Any trailing rule that wins here still needs live-runner implementation review before production.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"REPORT {report_path}")
    print("BASELINES")
    print(baseline_rows[["strategy", "rel_gain_threshold", "exit_policy", "n", "total_pnl", "sharpe", "max_dd"]].to_string(index=False))
    print("TOP CAM")
    print(top_cam[["rel_gain_threshold", "exit_policy", "n", "total_pnl", "sharpe", "max_dd", "wf_profitable_windows"]].head(5).to_string(index=False))
    print("TOP GOR")
    print(top_gor[["rel_gain_threshold", "exit_policy", "n", "total_pnl", "sharpe", "max_dd", "wf_profitable_windows"]].head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
