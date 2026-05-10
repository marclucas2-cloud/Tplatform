#!/usr/bin/env python3
"""Backtest Gold-Oil Rotation trailing-stop overlays.

Research-only comparator for the production Gold-Oil Rotation strategy:
- Entry logic unchanged (20d momentum spread, 2% edge, next-open entry)
- Baseline exit: fixed SL 2%, fixed TP 4%, max hold 10d
- Trailing variants: same fixed TP 4% and initial SL 2%, but SL ratchets to
  `highest_since_entry * (1 - trail_pct)`.

Important: the trailing ratchet is applied conservatively. A new stop derived
from today's high becomes active from the NEXT bar, not intra-bar, to avoid
path assumptions inside a daily candle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "reports" / "research"
JSON_DIR = ROOT / "data" / "research"

SPECS = {
    "MGC": {"mult": 10.0, "cost": 2.49},
    "MCL": {"mult": 100.0, "cost": 2.49},
}

LOOKBACK = 20
MIN_EDGE = 0.02
SL_PCT = 0.02
TP_PCT = 0.04
HOLD_DAYS = 10
TRAIL_GRID = [0.004, 0.006, 0.008, 0.010, 0.0125, 0.015, 0.02]


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str
    trail_pct: float | None


def load(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "data" / "futures" / f"{sym}_1D.parquet")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def compute_stats(trades: list[Trade]) -> dict[str, float | int]:
    if not trades:
        return {
            "n": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "sharpe_trade": 0.0,
            "profit_factor": 0.0,
            "max_dd": 0.0,
        }
    pnl = np.array([t.pnl for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    cum = np.cumsum(pnl)
    peaks = np.maximum.accumulate(cum)
    drawdowns = cum - peaks
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.size else float("inf")
    sharpe_trade = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() > 0 else 0.0
    return {
        "n": int(len(trades)),
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "sharpe_trade": sharpe_trade,
        "profit_factor": profit_factor,
        "max_dd": float(drawdowns.min()),
    }


def walk_forward_stats(
    mgc: pd.DataFrame,
    mcl: pd.DataFrame,
    trail_pct: float | None,
    n_windows: int = 5,
    is_frac: float = 0.6,
) -> dict[str, object]:
    trades = run_strategy(mgc, mcl, trail_pct=trail_pct)
    common = mgc.index.intersection(mcl.index)
    total_days = len(common)
    window_days = total_days // n_windows
    is_days = int(window_days * is_frac)
    windows = []
    for w in range(n_windows):
        is_start = common[w * window_days]
        is_end = common[w * window_days + is_days - 1]
        oos_start = common[w * window_days + is_days]
        oos_end_idx = min(w * window_days + window_days - 1, total_days - 1)
        oos_end = common[oos_end_idx]
        oos_trades = [
            t for t in trades
            if oos_start <= pd.Timestamp(t.exit_date) <= oos_end
        ]
        st = compute_stats(oos_trades)
        windows.append({
            "window": w + 1,
            "oos_period": f"{oos_start.date()}..{oos_end.date()}",
            "oos_n": st["n"],
            "oos_total_pnl": round(st["total_pnl"], 2),
            "oos_sharpe": round(float(st["sharpe_trade"]), 2),
            "oos_profitable": bool(st["total_pnl"] > 0),
        })
    prof = sum(1 for w in windows if w["oos_profitable"])
    sharpe_vals = [float(w["oos_sharpe"]) for w in windows if int(w["oos_n"]) > 0]
    return {
        "windows": windows,
        "oos_profitable_windows": prof,
        "oos_total_pnl": round(sum(float(w["oos_total_pnl"]) for w in windows), 2),
        "oos_mean_sharpe": round(float(np.mean(sharpe_vals)), 2) if sharpe_vals else 0.0,
    }


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    trail_pct: float | None,
) -> tuple[int, float, str]:
    current_sl = entry_price * (1 - SL_PCT)
    current_tp = entry_price * (1 + TP_PCT)
    highest = entry_price

    for j in range(entry_idx, min(entry_idx + HOLD_DAYS, len(df))):
        opn = float(df["open"].iloc[j])
        high = float(df["high"].iloc[j])
        low = float(df["low"].iloc[j])
        close = float(df["close"].iloc[j])

        if j > entry_idx:
            if opn <= current_sl:
                return j, opn, "gap_sl"
            if opn >= current_tp:
                return j, opn, "gap_tp"

        if low <= current_sl:
            return j, current_sl, "sl"
        if high >= current_tp:
            return j, current_tp, "tp"

        highest = max(highest, high)
        if trail_pct is not None:
            proposed_sl = highest * (1 - trail_pct)
            current_sl = max(current_sl, proposed_sl)

        if j == min(entry_idx + HOLD_DAYS, len(df)) - 1:
            return j, close, "time_exit"

    end_idx = min(entry_idx + HOLD_DAYS - 1, len(df) - 1)
    return end_idx, float(df["close"].iloc[end_idx]), "time_exit"


def run_strategy(
    mgc: pd.DataFrame,
    mcl: pd.DataFrame,
    trail_pct: float | None,
) -> list[Trade]:
    common = mgc.index.intersection(mcl.index)
    mgc_c = mgc["close"].reindex(common)
    mcl_c = mcl["close"].reindex(common)
    mgc_ret = mgc_c.pct_change(LOOKBACK)
    mcl_ret = mcl_c.pct_change(LOOKBACK)
    trades: list[Trade] = []
    last_signal_idx = -100

    for i in range(LOOKBACK, len(common) - HOLD_DAYS - 1):
        if i - last_signal_idx < HOLD_DAYS:
            continue
        mgc_r = float(mgc_ret.iloc[i])
        mcl_r = float(mcl_ret.iloc[i])
        if not (np.isfinite(mgc_r) and np.isfinite(mcl_r)):
            continue
        spread = mgc_r - mcl_r
        if abs(spread) < MIN_EDGE:
            continue

        if spread > 0:
            sym = "MGC"
            df = mgc
        else:
            sym = "MCL"
            df = mcl

        signal_date = common[i]
        entry_idx = df.index.get_loc(signal_date) + 1
        if entry_idx >= len(df):
            break
        entry_price = float(df["open"].iloc[entry_idx])
        exit_idx, exit_price, exit_reason = simulate_trade(
            df=df,
            entry_idx=entry_idx,
            entry_price=entry_price,
            trail_pct=trail_pct,
        )
        spec = SPECS[sym]
        pnl = (exit_price - entry_price) * spec["mult"] - spec["cost"]
        trades.append(
            Trade(
                symbol=sym,
                entry_date=str(df.index[entry_idx].date()),
                exit_date=str(df.index[exit_idx].date()),
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                pnl=round(pnl, 4),
                exit_reason=exit_reason if trail_pct is None else (
                    "trail_" + exit_reason if exit_reason in {"sl", "gap_sl"} else exit_reason
                ),
                trail_pct=trail_pct,
            )
        )
        last_signal_idx = i

    return trades


def summarize_exit_reasons(trades: list[Trade]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in trades:
        counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
    return counts


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    headers = [str(col) for col in df.columns]
    rows = [[str(v) for v in row] for row in df.to_numpy().tolist()]
    sep = ["---"] * len(headers)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    mgc = load("MGC")
    mcl = load("MCL")

    variants = []
    for trail_pct in [None] + TRAIL_GRID:
        trades = run_strategy(mgc, mcl, trail_pct=trail_pct)
        st = compute_stats(trades)
        wf = walk_forward_stats(mgc, mcl, trail_pct)
        label = "baseline_fixed_2_4" if trail_pct is None else f"trail_{trail_pct*100:.2f}pct"
        variants.append({
            "label": label,
            "trail_pct": trail_pct,
            "stats": st,
            "wf": wf,
            "exit_reasons": summarize_exit_reasons(trades),
            "sample_trades": [asdict(t) for t in trades[:5]],
        })

    baseline = variants[0]
    trailing_only = variants[1:]
    best = max(
        trailing_only,
        key=lambda v: (v["stats"]["total_pnl"], v["stats"]["sharpe_trade"]),
    )

    rows = []
    for v in variants:
        st = v["stats"]
        wf = v["wf"]
        rows.append({
            "label": v["label"],
            "trail_pct": None if v["trail_pct"] is None else round(v["trail_pct"] * 100, 3),
            "n": st["n"],
            "total_pnl": round(st["total_pnl"], 2),
            "avg_pnl": round(st["avg_pnl"], 2),
            "win_rate": round(st["win_rate"] * 100, 1),
            "sharpe": round(st["sharpe_trade"], 2),
            "profit_factor": round(st["profit_factor"], 2) if np.isfinite(st["profit_factor"]) else "inf",
            "max_dd": round(st["max_dd"], 2),
            "wf_profitable_windows": wf["oos_profitable_windows"],
            "wf_oos_total_pnl": wf["oos_total_pnl"],
            "wf_oos_mean_sharpe": wf["oos_mean_sharpe"],
        })
    table = pd.DataFrame(rows)

    report_path = REPORT_DIR / "gold_oil_rotation_trailing_backtest_2026-05-06.md"
    json_path = JSON_DIR / "gold_oil_rotation_trailing_backtest_2026-05-06.json"

    lines = [
        "# Gold-Oil Rotation — Trailing Stop Backtest — 2026-05-06",
        "",
        "## Setup",
        f"- Data: `{mgc.index.min().date()} -> {mgc.index.max().date()}` on `MGC_1D.parquet` / `MCL_1D.parquet`",
        f"- Entry logic unchanged: lookback {LOOKBACK}, min_edge {MIN_EDGE:.0%}, next-open entry",
        f"- Baseline exits: fixed SL {SL_PCT:.0%}, fixed TP {TP_PCT:.0%}, max hold {HOLD_DAYS} bars",
        "- Trailing overlay: same initial SL/TP, but SL ratchets off the highest high and becomes active from the next bar only",
        "- Conservative assumption: no intrabar hindsight; a same-day high does not tighten the stop for the same candle",
        "",
        "## Summary Table",
        dataframe_to_markdown(table),
        "",
        "## Baseline vs Best Trailing",
        f"- Baseline: `{baseline['label']}` total PnL `${baseline['stats']['total_pnl']:.2f}`, Sharpe `{baseline['stats']['sharpe_trade']:.2f}`, max DD `${baseline['stats']['max_dd']:.2f}`, WF `{baseline['wf']['oos_profitable_windows']}/5` profitable windows",
        f"- Best trailing by total PnL: `{best['label']}` total PnL `${best['stats']['total_pnl']:.2f}`, Sharpe `{best['stats']['sharpe_trade']:.2f}`, max DD `${best['stats']['max_dd']:.2f}`, WF `{best['wf']['oos_profitable_windows']}/5` profitable windows",
        "",
        "## Exit Mix",
        f"- Baseline exits: `{baseline['exit_reasons']}`",
        f"- Best trailing exits: `{best['exit_reasons']}`",
        "",
        "## Interpretation",
    ]

    delta_pnl = best["stats"]["total_pnl"] - baseline["stats"]["total_pnl"]
    delta_sharpe = best["stats"]["sharpe_trade"] - baseline["stats"]["sharpe_trade"]
    delta_dd = best["stats"]["max_dd"] - baseline["stats"]["max_dd"]
    lines.extend([
        f"- PnL delta best trailing vs baseline: `${delta_pnl:.2f}`",
        f"- Sharpe delta best trailing vs baseline: `{delta_sharpe:.2f}`",
        f"- Max DD delta best trailing vs baseline: `${delta_dd:.2f}` (less negative is better)",
        "- If all trailing variants underperform the baseline, that is a strong argument against wiring a generic trailing stop into `gold_oil_rotation` live without a strategy-specific redesign.",
    ])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps({"variants": variants}, indent=2), encoding="utf-8")

    print(f"REPORT {report_path}")
    print(table.to_string(index=False))
    print(f"BEST {best['label']} pnl={best['stats']['total_pnl']:.2f} sharpe={best['stats']['sharpe_trade']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
