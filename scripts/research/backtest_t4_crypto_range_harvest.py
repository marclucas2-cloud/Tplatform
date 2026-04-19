#!/usr/bin/env python3
"""T4-A1 - Crypto range harvesting research batch.

Research-only rebuild of the BTC 4h Bollinger mean-reversion sleeve.

Goal:
  - validate whether a low-trend BTC range harvester can add value in both
    bullish and bearish tapes without relying on directional beta
  - produce a standalone daily PnL series that can be scored against the
    existing baseline portfolio

Outputs:
  - docs/research/wf_reports/T4A-01_crypto_range_harvest.md
  - output/research/wf_reports/T4A-01_crypto_range_harvest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.research.portfolio_marginal_score import score_candidate  # noqa: E402

BTC_4H_PATH = ROOT / "data" / "crypto" / "candles" / "BTCUSDT_4h.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T4A-01_crypto_range_harvest.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T4A-01_crypto_range_harvest.json"

CAPITAL = 10_000.0
BINANCE_SIDE_COST = 0.0013  # 10 bps fee + 3 bps slippage


def load_btc_4h() -> pd.DataFrame:
    df = pd.read_parquet(BTC_4H_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index()
    return df[["open", "high", "low", "close", "volume", "quote_volume"]].copy()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    plus_dm = df["high"].diff().clip(lower=0.0)
    minus_dm = (-df["low"].diff()).clip(lower=0.0)
    tr = pd.DataFrame(
        {
            "hl": df["high"] - df["low"],
            "hc": (df["high"] - df["close"].shift(1)).abs(),
            "lc": (df["low"] - df["close"].shift(1)).abs(),
        }
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def run_range_harvest(
    df: pd.DataFrame,
    bb_period: int,
    adx_max: float,
    sl_mult: float,
    max_hold_bars: int,
    label: str,
) -> pd.Series:
    work = df.copy()
    work["sma"] = work["close"].rolling(bb_period).mean()
    work["std"] = work["close"].rolling(bb_period).std()
    work["bb_upper"] = work["sma"] + 2.0 * work["std"]
    work["bb_lower"] = work["sma"] - 2.0 * work["std"]
    work["adx"] = compute_adx(work, 14)

    daily_index = pd.Index(sorted(work.index.normalize().unique()))
    daily_pnl = pd.Series(0.0, index=daily_index, dtype=float)
    position = None

    for i in range(1, len(work)):
        now = work.index[i]
        row = work.iloc[i]
        prev = work.iloc[i - 1]

        if position is not None:
            position["bars_held"] += 1
            exit_price = None

            if position["direction"] == 1:
                stop_hit = row["low"] <= position["stop"]
                target_hit = row["high"] >= position["target"]
                if stop_hit and target_hit:
                    exit_price = position["stop"]
                elif stop_hit:
                    exit_price = position["stop"]
                elif target_hit:
                    exit_price = position["target"]
            else:
                stop_hit = row["high"] >= position["stop"]
                target_hit = row["low"] <= position["target"]
                if stop_hit and target_hit:
                    exit_price = position["stop"]
                elif stop_hit:
                    exit_price = position["stop"]
                elif target_hit:
                    exit_price = position["target"]

            if exit_price is None and position["bars_held"] >= max_hold_bars:
                exit_price = float(row["close"])

            if exit_price is not None:
                gross = (
                    position["direction"]
                    * (float(exit_price) - position["entry"])
                    * position["qty"]
                )
                cost = CAPITAL * 2.0 * BINANCE_SIDE_COST
                daily_pnl.loc[now.normalize()] += gross - cost
                position = None

        if position is not None:
            continue

        vals = [prev["bb_upper"], prev["bb_lower"], prev["sma"], prev["adx"], row["open"]]
        if any(pd.isna(v) for v in vals):
            continue
        if prev["adx"] >= adx_max:
            continue

        entry = float(row["open"])
        qty = CAPITAL / entry if entry > 0 else 0.0
        if qty <= 0:
            continue

        if prev["close"] < prev["bb_lower"]:
            target = float(prev["sma"])
            edge = max(target - float(prev["close"]), 0.0)
            if edge <= 0:
                continue
            position = {
                "direction": 1,
                "entry": entry,
                "qty": qty,
                "target": target,
                "stop": entry - sl_mult * edge,
                "bars_held": 0,
            }
        elif prev["close"] > prev["bb_upper"]:
            target = float(prev["sma"])
            edge = max(float(prev["close"]) - target, 0.0)
            if edge <= 0:
                continue
            position = {
                "direction": -1,
                "entry": entry,
                "qty": qty,
                "target": target,
                "stop": entry + sl_mult * edge,
                "bars_held": 0,
            }

    if position is not None:
        last_ts = work.index[-1].normalize()
        last_close = float(work["close"].iloc[-1])
        gross = position["direction"] * (last_close - position["entry"]) * position["qty"]
        cost = CAPITAL * 2.0 * BINANCE_SIDE_COST
        daily_pnl.loc[last_ts] += gross - cost

    daily_pnl = daily_pnl[daily_pnl.index.notna()].sort_index()
    daily_pnl.name = label
    return daily_pnl


def build_variants(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "range_bb_harvest_rebuild": run_range_harvest(
            df,
            bb_period=20,
            adx_max=20,
            sl_mult=1.5,
            max_hold_bars=18,
            label="range_bb_harvest_rebuild",
        ),
        "range_bb_harvest_adx18": run_range_harvest(
            df,
            bb_period=20,
            adx_max=18,
            sl_mult=1.5,
            max_hold_bars=18,
            label="range_bb_harvest_adx18",
        ),
        "range_bb_harvest_bb30": run_range_harvest(
            df,
            bb_period=30,
            adx_max=20,
            sl_mult=1.5,
            max_hold_bars=18,
            label="range_bb_harvest_bb30",
        ),
    }


def standalone_stats(pnl: pd.Series) -> dict[str, float]:
    active = int((pnl != 0).sum())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() != 0 else 0.0
    eq = CAPITAL + pnl.cumsum()
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    return {
        "active_days": active,
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe,
        "max_dd_pct": dd * 100.0,
    }


def main() -> int:
    print("=== T4-A1 : Crypto range harvest ===")
    print(f"Cost model: {BINANCE_SIDE_COST * 200:.2f}% round trip")

    df = load_btc_4h()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(df)
    rows = []
    scorecards = []
    for name, pnl in variants.items():
        stats = standalone_stats(pnl)
        sc = score_candidate(name, pnl, baseline, CAPITAL, 1.0)
        rows.append((name, stats, sc))
        scorecards.append(sc.to_dict())
        print(
            f"{name}: total=${stats['total_pnl']:+,.0f} sharpe={stats['sharpe']:+.2f} "
            f"[{sc.verdict}] score={sc.marginal_score:+.3f}"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(scorecards, indent=2, default=str), encoding="utf-8")

    md = [
        "# T4-A1 - Crypto range harvest",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Data** : {df.index.min()} -> {df.index.max()} ({len(df)} 4h bars)",
        "**Instrument** : BTCUSDT 4h",
        f"**Cost model** : {BINANCE_SIDE_COST * 200:.2f}% round trip",
        "",
        "## Thesis",
        "",
        "- a crypto sleeve that survives both bull and bear should not rely only on trend beta",
        "- BTC spends long stretches in chop even inside large bull or bear regimes",
        "- low-ADX Bollinger fades are a candidate for a regime-agnostic harvest sleeve",
        "",
        "## Variants",
        "",
        "| Variant | Active Days | Total PnL | Sharpe | MaxDD | Verdict | Score | dSharpe | dMaxDD | Corr |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, stats, sc in rows:
        md.append(
            f"| `{name}` | {stats['active_days']} | ${stats['total_pnl']:+,.0f} | "
            f"{stats['sharpe']:+.2f} | {stats['max_dd_pct']:.1f}% | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | {sc.delta_maxdd:+.2f}pp | "
            f"{sc.corr_to_portfolio:+.2f} |"
        )

    best = max(rows, key=lambda row: row[2].marginal_score)
    md += [
        "",
        "## Best candidate",
        "",
        f"- `{best[0]}`",
        f"- Verdict : **{best[2].verdict}**",
        f"- Marginal score : {best[2].marginal_score:+.3f}",
        f"- Delta Sharpe : {best[2].delta_sharpe:+.3f}",
        f"- Delta MaxDD : {best[2].delta_maxdd:+.2f}pp",
        f"- Corr to portfolio : {best[2].corr_to_portfolio:+.3f}",
        "",
        "## Note",
        "",
        "- this is an independent rebuild of the existing `range_bb_harvest` idea, scored against the current portfolio baseline",
        "- the goal here is not to overwrite production logic, only to confirm whether the sleeve still looks additive in 2026 research conditions",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
