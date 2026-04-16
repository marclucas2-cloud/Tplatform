#!/usr/bin/env python3
"""T1-A — Futures calendar / session effects backtest.

Teste plusieurs variantes calendrier sur MES (10Y daily):
  - Day-of-week bias (lundi, mardi, ..., vendredi long open-to-close)
  - Monday reversal (short si vendredi up, long si down)
  - Turn-of-month (long les 3 derniers + 3 premiers jours du mois)
  - FOMC day effect (long sur jours d'annonces FOMC)
  - Friday weakness (short Friday open-to-close)
  - Pre-holiday drift (long veille de jour ferie US)

Chaque variante produit une serie PnL quotidienne ($), scoree via
`portfolio_marginal_score.score_candidate()` contre la baseline
(cross_asset_momentum + gold_oil_rotation + gold_trend_mgc).

Couts inclus : MES commission IBKR $0.85/side + 2 ticks slippage ($1.25 tick).
Position: 1 contrat par signal.

Usage:
    python scripts/research/backtest_futures_calendar.py
Output:
    docs/research/wf_reports/T1-04_futures_calendar.md    (versionne)
    output/research/wf_reports/T1-04_scorecards.json      (local, gitignored)
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

MES_PATH = ROOT / "data" / "futures" / "MES_LONG.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"       # versionne (markdown)
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"   # local (scorecards JSON)
MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)

# MES contract specs
MES_POINT_VALUE = 5.0          # $5 per point per contract
MES_TICK_SIZE = 0.25
MES_TICK_VALUE = MES_POINT_VALUE * MES_TICK_SIZE   # $1.25 / tick
IBKR_COMMISSION_PER_SIDE = 0.85
SLIPPAGE_TICKS_PER_SIDE = 1.0
ROUND_TRIP_COST = 2 * (IBKR_COMMISSION_PER_SIDE + SLIPPAGE_TICKS_PER_SIDE * MES_TICK_VALUE)
# = 2 * (0.85 + 1 * 1.25) = 2 * 2.10 = $4.20 round trip per contract

# FOMC announcement dates 2015-2026 (public record, see federalreserve.gov)
# Dates in YYYY-MM-DD (second day of 2-day meetings)
FOMC_DATES = [
    # 2015
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020 (incl. emergency cuts)
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18",
]

# US holidays 2015-2026 (NYSE closures) for pre-holiday drift variant
US_HOLIDAYS = [
    # A selection of major NYSE holidays; pre-holiday = trading day before
    "2015-01-01", "2015-01-19", "2015-02-16", "2015-04-03", "2015-05-25",
    "2015-07-03", "2015-09-07", "2015-11-26", "2015-12-25",
    "2016-01-01", "2016-01-18", "2016-02-15", "2016-03-25", "2016-05-30",
    "2016-07-04", "2016-09-05", "2016-11-24", "2016-12-26",
    "2017-01-02", "2017-01-16", "2017-02-20", "2017-04-14", "2017-05-29",
    "2017-07-04", "2017-09-04", "2017-11-23", "2017-12-25",
    "2018-01-01", "2018-01-15", "2018-02-19", "2018-03-30", "2018-05-28",
    "2018-07-04", "2018-09-03", "2018-11-22", "2018-12-25",
    "2019-01-01", "2019-01-21", "2019-02-18", "2019-04-19", "2019-05-27",
    "2019-07-04", "2019-09-02", "2019-11-28", "2019-12-25",
    "2020-01-01", "2020-01-20", "2020-02-17", "2020-04-10", "2020-05-25",
    "2020-07-03", "2020-09-07", "2020-11-26", "2020-12-25",
    "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31",
    "2021-07-05", "2021-09-06", "2021-11-25", "2021-12-24",
    "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20",
    "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29",
    "2023-06-19", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
]


def load_mes() -> pd.DataFrame:
    df = pd.read_parquet(MES_PATH)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index()
    # Rename for clarity
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df["dow"] = df.index.dayofweek  # 0=Mon .. 4=Fri
    df["day_of_month"] = df.index.day
    # Next trading day close (for Thursday drift etc.)
    df["next_open"] = df["open"].shift(-1)
    df["prev_close"] = df["close"].shift(1)
    return df


def _pnl_long_oc(df: pd.DataFrame, signal: pd.Series) -> pd.Series:
    """PnL of long 1 MES contract open-to-close on signal=True days, net of RT cost."""
    oc_return = (df["close"] - df["open"]) * MES_POINT_VALUE
    gross = oc_return.where(signal, 0.0)
    # Cost only when trading (signal True)
    cost = np.where(signal, ROUND_TRIP_COST, 0.0)
    return (gross - cost).astype(float)


def _pnl_short_oc(df: pd.DataFrame, signal: pd.Series) -> pd.Series:
    """PnL of short 1 MES contract open-to-close on signal=True days."""
    oc_return = (df["open"] - df["close"]) * MES_POINT_VALUE
    gross = oc_return.where(signal, 0.0)
    cost = np.where(signal, ROUND_TRIP_COST, 0.0)
    return (gross - cost).astype(float)


def variant_dow_long(df: pd.DataFrame, dow: int, label: str) -> pd.Series:
    """Long MES open-to-close every day matching `dow` (0=Mon..4=Fri)."""
    signal = df["dow"] == dow
    pnl = _pnl_long_oc(df, signal)
    pnl.name = label
    return pnl


def variant_dow_short(df: pd.DataFrame, dow: int, label: str) -> pd.Series:
    signal = df["dow"] == dow
    pnl = _pnl_short_oc(df, signal)
    pnl.name = label
    return pnl


def variant_monday_reversal(df: pd.DataFrame) -> pd.Series:
    """Monday: short if Friday close > Friday open (up day), else long."""
    # previous Friday = previous trading day for a Monday (most cases)
    prev_close = df["close"].shift(1)
    prev_open = df["open"].shift(1)
    prev_up = prev_close > prev_open
    monday = df["dow"] == 0
    # Long monday when prev up = False, Short when prev up = True
    long_sig = monday & (~prev_up.fillna(False))
    short_sig = monday & (prev_up.fillna(False))
    long_pnl = _pnl_long_oc(df, long_sig)
    short_pnl = _pnl_short_oc(df, short_sig)
    pnl = (long_pnl + short_pnl).astype(float)
    pnl.name = "monday_reversal"
    return pnl


def variant_turn_of_month(df: pd.DataFrame) -> pd.Series:
    """Long MES last 3 trading days + first 3 trading days of each month."""
    # Need trading-day-based distance to month end/start
    # Simpler proxy: using month-number transitions
    month = df.index.to_period("M")
    # Days from end of month = countdown of trading days per month
    df_ = df.copy()
    df_["month"] = month
    df_["tdx_forward"] = df_.groupby("month").cumcount()                 # 0 = first TD
    df_["tdx_backward"] = df_.groupby("month").cumcount(ascending=False)  # 0 = last TD
    signal = (df_["tdx_forward"] <= 2) | (df_["tdx_backward"] <= 2)
    pnl = _pnl_long_oc(df, signal)
    pnl.name = "turn_of_month"
    return pnl


def variant_fomc_long(df: pd.DataFrame) -> pd.Series:
    fomc_set = set(pd.to_datetime(FOMC_DATES).normalize())
    signal = pd.Series(df.index.isin(fomc_set), index=df.index)
    pnl = _pnl_long_oc(df, signal)
    pnl.name = "fomc_day_long"
    return pnl


def variant_fomc_overnight_drift(df: pd.DataFrame) -> pd.Series:
    """Long MES close of day T-1 -> open of day T (FOMC day).

    Captures the "pre-FOMC drift" documented by Lucca & Moench (2015) —
    strong equity returns in the 24h before the announcement.
    """
    fomc_set = set(pd.to_datetime(FOMC_DATES).normalize())
    # For each FOMC date, PnL = (open_fomc - close_prev) * point_value - cost
    # This is "close yesterday, open today" overnight gap.
    is_fomc = df.index.isin(fomc_set)
    overnight = (df["open"] - df["prev_close"]) * MES_POINT_VALUE
    gross = overnight.where(is_fomc, 0.0)
    cost = np.where(is_fomc, ROUND_TRIP_COST, 0.0)
    pnl = (gross - cost).astype(float)
    pnl.name = "fomc_overnight_drift"
    return pnl


def variant_pre_holiday(df: pd.DataFrame) -> pd.Series:
    """Long MES open-to-close on the last trading day before a US holiday."""
    # Pre-holiday = max trading day index <= holiday-1
    hols = pd.to_datetime(US_HOLIDAYS).normalize()
    pre_holiday_dates = set()
    for h in hols:
        # Find the last trading day strictly before h
        mask = df.index < h
        if mask.any():
            pre_holiday_dates.add(df.index[mask][-1])
    signal = pd.Series(df.index.isin(pre_holiday_dates), index=df.index)
    pnl = _pnl_long_oc(df, signal)
    pnl.name = "pre_holiday_drift"
    return pnl


def main():
    print(f"=== T1-A : Futures calendar / session effects ===\n")
    print(f"MES round-trip cost: ${ROUND_TRIP_COST:.2f} per contract")
    print(f"Loading data from {MES_PATH}...")

    df = load_mes()
    print(f"  MES: {df.shape[0]} days, {df.index.min().date()} -> {df.index.max().date()}")

    print(f"Loading baseline portfolio from {BASELINE_PATH}...")
    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    print(f"  Baseline: {baseline.shape[0]} days, {baseline.shape[1]} strats")

    variants = []
    print("\n--- Generating variants ---")

    # Day-of-week long bias
    for dow, name in [(0, "mon"), (1, "tue"), (2, "wed"), (3, "thu"), (4, "fri")]:
        v = variant_dow_long(df, dow, f"long_{name}_oc")
        variants.append(v)

    # Monday reversal
    variants.append(variant_monday_reversal(df))
    # Friday weakness (short open-to-close)
    variants.append(variant_dow_short(df, 4, "short_fri_oc"))
    # Turn-of-month long
    variants.append(variant_turn_of_month(df))
    # FOMC day long open-to-close
    variants.append(variant_fomc_long(df))
    # FOMC pre-announcement overnight drift
    variants.append(variant_fomc_overnight_drift(df))
    # Pre-holiday drift
    variants.append(variant_pre_holiday(df))

    # Print standalone stats
    print(f"\n{'Variant':<28s} {'Trades':>8s} {'TotPnL$':>10s} {'WinRate':>8s}")
    for v in variants:
        trades = int((v != 0).sum())
        total = float(v.sum())
        wins = int((v > 0).sum())
        win_rate = wins / trades if trades > 0 else 0
        print(f"{v.name:<28s} {trades:>8d} {total:>10.0f} {win_rate:>7.1%}")

    # Score each variant against baseline
    print("\n--- Scoring via portfolio_marginal_score engine ---")
    scorecards = []
    for v in variants:
        try:
            sc = score_candidate(
                candidate_id=v.name,
                candidate_returns=v,
                portfolio_returns=baseline,
                initial_equity=10_000.0,
                candidate_weight=1.0,
            )
            scorecards.append(sc)
            print(f"  [{sc.verdict:<20s}] {sc.candidate_id:<28s} "
                  f"score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
                  f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")
        except Exception as e:
            print(f"  [SKIP] {v.name}: {e}")

    # Sort by marginal score
    scorecards.sort(key=lambda r: r.marginal_score, reverse=True)

    # Persist JSON (local artifact)
    json_out = JSON_OUT_DIR / "T1-04_scorecards.json"
    json_out.write_text(json.dumps(
        [sc.to_dict() for sc in scorecards], indent=2, default=str,
    ))
    print(f"\nScorecards JSON -> {json_out}")

    # Markdown report
    md_lines = [
        "# T1-A — Futures calendar / session effects",
        "",
        f"**Run date** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Instrument** : MES (S&P 500 micro futures)",
        f"**Historique** : {df.index.min().date()} -> {df.index.max().date()} "
        f"({len(df)} trading days)",
        f"**Baseline portfolio** : {list(baseline.columns)}",
        f"**RT cost par contrat** : ${ROUND_TRIP_COST:.2f} "
        f"(IBKR $0.85/side + 2 ticks slippage)",
        "",
        "## Standalone stats",
        "",
        "| Variant | Trades | Total PnL $ | Win rate |",
        "|---|---:|---:|---:|",
    ]
    for v in variants:
        trades = int((v != 0).sum())
        total = float(v.sum())
        wins = int((v > 0).sum())
        win_rate = wins / trades if trades > 0 else 0
        md_lines.append(f"| `{v.name}` | {trades} | {total:+,.0f} | {win_rate:.1%} |")

    md_lines += [
        "",
        "## Scorecards (marginal vs baseline)",
        "",
        "| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr | Tail | Penalties |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for sc in scorecards:
        pen = ", ".join(sc.penalties) if sc.penalties else "-"
        md_lines.append(
            f"| `{sc.candidate_id}` | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | "
            f"{sc.delta_cagr:+.2f}% | {sc.delta_maxdd:+.2f}pp | "
            f"{sc.corr_to_portfolio:+.2f} | {sc.tail_overlap:.0%} | {pen} |"
        )

    md_lines += [
        "",
        "## Details par variante",
        "",
    ]
    for sc in scorecards:
        md_lines += [
            f"### `{sc.candidate_id}` — {sc.verdict}",
            "",
            f"- Marginal score : **{sc.marginal_score:+.3f}**",
            f"- Delta Sharpe : {sc.delta_sharpe:+.3f}",
            f"- Delta CAGR : {sc.delta_cagr:+.2f}%",
            f"- Delta MaxDD : {sc.delta_maxdd:+.2f}pp",
            f"- Delta Calmar : {sc.delta_calmar:+.3f}",
            f"- Corr to portfolio : {sc.corr_to_portfolio:+.3f}",
            f"- Max corr to individual strat : {sc.max_corr_to_strat:+.3f}",
            f"- Tail overlap (worst 30 days) : {sc.tail_overlap:.0%} "
            f"({sc.worst_day_overlap}/30)",
            f"- Diversification benefit : {sc.diversification_benefit:+.3f}",
            f"- Capital utilization benefit : {sc.capital_utilization_benefit:+.3f}",
            f"- Days aligned with baseline : {sc.details['n_days']}",
            f"- Baseline metrics : Sharpe={sc.details['baseline']['sharpe']}, "
            f"CAGR={sc.details['baseline']['cagr']}%, "
            f"MaxDD={sc.details['baseline']['max_dd']}%",
            f"- Combined metrics : Sharpe={sc.details['combined']['sharpe']}, "
            f"CAGR={sc.details['combined']['cagr']}%, "
            f"MaxDD={sc.details['combined']['max_dd']}%",
        ]
        if sc.penalties:
            md_lines.append(f"- Penalties : {', '.join(sc.penalties)}")
        md_lines.append("")

    # Verdict summary
    by_verdict = {}
    for sc in scorecards:
        by_verdict.setdefault(sc.verdict, []).append(sc.candidate_id)
    md_lines += [
        "## Verdict summary",
        "",
    ]
    for v in ["PROMOTE_LIVE", "PROMOTE_PAPER", "KEEP_FOR_RESEARCH", "DROP"]:
        if v in by_verdict:
            md_lines.append(f"- **{v}** : {', '.join(f'`{c}`' for c in by_verdict[v])}")
    md_lines.append("")

    md_out = MD_OUT_DIR / "T1-04_futures_calendar.md"
    md_out.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Markdown report  -> {md_out}")

    # Print final summary
    print(f"\n=== FINAL SUMMARY ===")
    for v, lst in by_verdict.items():
        print(f"  {v}: {len(lst)} variants")
        for cand in lst:
            print(f"    - {cand}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
