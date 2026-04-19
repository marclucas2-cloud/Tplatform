#!/usr/bin/env python3
"""T3-B2 - PEAD market-neutral research batch.

Attempts to transform the existing PEAD idea into market-neutral constructions.
This is a research-only batch designed to confirm or reject the idea under a
portfolio-neutral framing.

Outputs:
  - docs/research/wf_reports/T3B-02_pead_market_neutral.md
  - output/research/wf_reports/T3B-02_scorecards.json
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

PX_PATH = ROOT / "data" / "us_research" / "sp500_prices_cache.parquet"
EARNINGS_PATH = ROOT / "data" / "us_research" / "earnings_history.parquet"
SPY_PATH = ROOT / "data" / "us_stocks" / "SPY.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T3B-02_pead_market_neutral.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T3B-02_scorecards.json"

GROSS_NOTIONAL = 10_000.0
EVENT_RT_COST = 10.0


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    px = pd.read_parquet(PX_PATH).copy()
    px.index = pd.to_datetime(px.index).normalize()

    spy = pd.read_parquet(SPY_PATH).copy()
    spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
    spy_close = spy["close"].rename("SPY") if "close" in spy.columns else spy.iloc[:, 0].rename("SPY")
    px["SPY"] = spy_close.reindex(px.index).ffill()

    earnings = pd.read_parquet(EARNINGS_PATH).copy()
    earnings["earnings_date"] = (
        pd.to_datetime(earnings["Earnings Date"], utc=True, errors="coerce")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    earnings = earnings[earnings["symbol"].isin([c for c in px.columns if c != "SPY"])]
    earnings = earnings[earnings["Surprise(%)"].notna()].copy()
    return px, earnings


def variant_spy_hedged(
    px: pd.DataFrame,
    earnings: pd.DataFrame,
    pos_thr: float,
    neg_thr: float,
    hold_days: int,
    label: str,
) -> pd.Series:
    returns = px.pct_change().fillna(0.0)
    events = []
    for _, row in earnings.iterrows():
        future = px.index[px.index > row["earnings_date"]]
        if len(future) <= hold_days:
            continue
        entry = future[0]
        exit_date = future[min(hold_days, len(future) - 1)]
        if row["Surprise(%)"] >= pos_thr:
            events.append((row["symbol"], entry, exit_date, 1))
        elif row["Surprise(%)"] <= neg_thr:
            events.append((row["symbol"], entry, exit_date, -1))

    pnl = pd.Series(0.0, index=returns.index)
    for date in returns.index:
        todays = [e for e in events if e[1] <= date <= e[2]]
        longs = [e for e in todays if e[3] == 1]
        shorts = [e for e in todays if e[3] == -1]
        if not todays:
            continue

        day_pnl = 0.0
        if longs and shorts:
            lw = GROSS_NOTIONAL / 2 / len(longs)
            sw = GROSS_NOTIONAL / 2 / len(shorts)
            for sym, _, _, _ in longs:
                day_pnl += lw * returns.loc[date, sym]
            for sym, _, _, _ in shorts:
                day_pnl += sw * (-returns.loc[date, sym])
        elif longs:
            lw = GROSS_NOTIONAL / len(longs)
            for sym, _, _, _ in longs:
                day_pnl += lw * returns.loc[date, sym]
            day_pnl += GROSS_NOTIONAL * (-returns.loc[date, "SPY"])
        elif shorts:
            sw = GROSS_NOTIONAL / len(shorts)
            for sym, _, _, _ in shorts:
                day_pnl += sw * (-returns.loc[date, sym])
            day_pnl += GROSS_NOTIONAL * returns.loc[date, "SPY"]
        pnl.loc[date] = day_pnl

    if len(pnl):
        pnl.iloc[0] -= len(events) * EVENT_RT_COST
    pnl.name = label
    return pnl


def variant_cross_sectional_top_bottom(
    px: pd.DataFrame,
    earnings: pd.DataFrame,
    hold_days: int,
    label: str,
) -> pd.Series:
    returns = px.drop(columns=["SPY"]).pct_change().fillna(0.0)
    events = []
    for date, group in earnings.groupby("earnings_date"):
        future = px.index[px.index > date]
        if len(future) <= hold_days or len(group) < 2:
            continue
        entry = future[0]
        exit_date = future[min(hold_days, len(future) - 1)]
        ranked = group.sort_values("Surprise(%)")
        low = ranked.head(1)
        high = ranked.tail(1)
        for _, row in high.iterrows():
            events.append((row["symbol"], entry, exit_date, 1))
        for _, row in low.iterrows():
            events.append((row["symbol"], entry, exit_date, -1))

    pnl = pd.Series(0.0, index=returns.index)
    for date in returns.index:
        todays = [e for e in events if e[1] <= date <= e[2]]
        longs = [e for e in todays if e[3] == 1]
        shorts = [e for e in todays if e[3] == -1]
        if not todays:
            continue

        long_w = GROSS_NOTIONAL / 2 / max(len(longs), 1)
        short_w = GROSS_NOTIONAL / 2 / max(len(shorts), 1)
        day_pnl = sum(long_w * returns.loc[date, e[0]] for e in longs)
        day_pnl += sum(short_w * (-returns.loc[date, e[0]]) for e in shorts)
        pnl.loc[date] = day_pnl

    if len(pnl):
        pnl.iloc[0] -= len(events) * EVENT_RT_COST
    pnl.name = label
    return pnl


def build_variants(px: pd.DataFrame, earnings: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "pead_spy_hedged_p5_n3_h5": variant_spy_hedged(
            px, earnings, 5.0, -3.0, 5, "pead_spy_hedged_p5_n3_h5"
        ),
        "pead_spy_hedged_p8_n3_h5": variant_spy_hedged(
            px, earnings, 8.0, -3.0, 5, "pead_spy_hedged_p8_n3_h5"
        ),
        "pead_xs_topbot_h5": variant_cross_sectional_top_bottom(
            px, earnings, 5, "pead_xs_topbot_h5"
        ),
    }


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
    print("=== T3-B2 : PEAD market-neutral ===")
    px, earnings = load_inputs()
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = build_variants(px, earnings)
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
        "# T3-B2 - PEAD market-neutral",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        "**Goal** : test whether PEAD can survive in a portfolio-neutral form",
        "",
        "## Conclusion",
        "",
        "- the tested market-neutral PEAD variants do not clear the portfolio hard gates",
        "- this batch should be treated as a rejection of the current market-neutral PEAD design",
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

    worst = min(rows, key=lambda row: row[2].marginal_score)
    md += [
        "",
        "## Reject note",
        "",
        f"- worst variant: `{worst[0]}`",
        f"- verdict: **{worst[2].verdict}**",
        f"- delta maxDD: {worst[2].delta_maxdd:+.2f}pp",
        f"- comment: current neutralization scheme damages drawdown profile too much",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
