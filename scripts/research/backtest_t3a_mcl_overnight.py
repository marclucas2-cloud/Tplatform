#!/usr/bin/env python3
"""T3-A1 - MCL overnight drift research batch.

Research-only backtest for micro crude oil overnight drift variants.

Idea:
  - Trade the gap from previous close to current open on MCL
  - Restrict entry with simple medium-term trend and weekday filters
  - Score daily PnL against the existing portfolio baseline

Outputs:
  - docs/research/wf_reports/T3A-01_mcl_overnight.md
  - output/research/wf_reports/T3A-01_scorecards.json
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

MCL_PATH = ROOT / "data" / "futures" / "MCL_LONG.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T3A-01_mcl_overnight.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T3A-01_scorecards.json"

MCL_POINT_VALUE = 100.0
IBKR_COMMISSION_PER_SIDE = 0.85
SLIPPAGE_TICKS_PER_SIDE = 1.0
MCL_TICK_VALUE = 1.0
MCL_RT_COST = 2 * (IBKR_COMMISSION_PER_SIDE + SLIPPAGE_TICKS_PER_SIDE * MCL_TICK_VALUE)


def load_mcl() -> pd.DataFrame:
    df = pd.read_parquet(MCL_PATH)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index()[["open", "high", "low", "close", "volume"]].copy()
    df["prev_close"] = df["close"].shift(1)
    df["dow"] = df.index.dayofweek
    return df


def _overnight_pnl(df: pd.DataFrame, signal: pd.Series, direction: int = 1) -> pd.Series:
    gap_points = (df["open"] - df["prev_close"]) * MCL_POINT_VALUE
    gross = (gap_points * direction).where(signal, 0.0)
    cost = np.where(signal, MCL_RT_COST, 0.0)
    return (gross - cost).fillna(0.0).astype(float)


def variant_mon_trend(df: pd.DataFrame, lookback: int = 10, label: str | None = None) -> pd.Series:
    trend = df["close"].pct_change(lookback)
    signal = (trend > 0) & (df["dow"] == 0)
    pnl = _overnight_pnl(df, signal, direction=1)
    pnl.name = label or f"mcl_overnight_mon_trend{lookback}"
    return pnl


def variant_mon_wed_trend(df: pd.DataFrame, lookback: int = 10, label: str | None = None) -> pd.Series:
    trend = df["close"].pct_change(lookback)
    signal = (trend > 0) & (df["dow"].isin([0, 2]))
    pnl = _overnight_pnl(df, signal, direction=1)
    pnl.name = label or f"mcl_overnight_mon_wed_trend{lookback}"
    return pnl


def variant_mon_trend_longer(df: pd.DataFrame, lookback: int = 40, label: str | None = None) -> pd.Series:
    trend = df["close"].pct_change(lookback)
    signal = (trend > 0) & (df["dow"] == 0)
    pnl = _overnight_pnl(df, signal, direction=1)
    pnl.name = label or f"mcl_overnight_mon_trend{lookback}"
    return pnl


def build_variants(df: pd.DataFrame) -> dict[str, pd.Series]:
    variants = {
        "mcl_overnight_mon_trend10": variant_mon_trend(df, 10, "mcl_overnight_mon_trend10"),
        "mcl_overnight_mon_wed_trend10": variant_mon_wed_trend(
            df, 10, "mcl_overnight_mon_wed_trend10"
        ),
        "mcl_overnight_mon_trend40": variant_mon_trend_longer(
            df, 40, "mcl_overnight_mon_trend40"
        ),
    }
    return variants


def _standalone_stats(pnl: pd.Series) -> dict:
    active = int((pnl != 0).sum())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() != 0 else 0.0
    eq = 10_000.0 + pnl.cumsum()
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    return {
        "active_days": active,
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe,
        "max_dd_pct": dd * 100,
    }


def main() -> int:
    print("=== T3-A1 : MCL overnight drift ===")
    print(f"MCL round-trip cost: ${MCL_RT_COST:.2f}")

    df = load_mcl()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(df)
    scorecards = []
    rows = []
    for name, pnl in variants.items():
        stats = _standalone_stats(pnl)
        sc = score_candidate(name, pnl, baseline, 10_000.0, 1.0)
        scorecards.append(sc.to_dict())
        rows.append((name, stats, sc))
        print(
            f"{name}: total=${stats['total_pnl']:+,.0f} sharpe={stats['sharpe']:+.2f} "
            f"[{sc.verdict}] score={sc.marginal_score:+.3f}"
        )

    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(scorecards, indent=2, default=str), encoding="utf-8")

    md = [
        "# T3-A1 - MCL overnight drift",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Data** : {df.index.min().date()} -> {df.index.max().date()} ({len(df)} days)",
        f"**Instrument** : MCL micro crude oil",
        f"**Cost model** : ${MCL_RT_COST:.2f} round trip "
        "(IBKR commission + 1 tick slippage per side)",
        "",
        "## Thesis",
        "",
        "- crude oil reprices overnight on macro, OPEC and geopolitics more than during the US day session",
        "- a weekday + trend filter may isolate the cleaner part of the drift",
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
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
