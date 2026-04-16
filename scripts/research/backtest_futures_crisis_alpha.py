#!/usr/bin/env python3
"""T2-A — Futures crisis alpha / vol overlay.

Logique: long vol proxy (short MES on VIX contrarian breakouts ou long VIX
futures quand VIX < 15). Hedge convexe contre crashs.

Proxies testees (pas d'options data => approximation via VIX spot):
  - vix_contrarian_long_mes_puts : short MES quand VIX < 15 (regime calme ->
    positionner pour expansion vol). Approximation rough.
  - vix_trend_short_mes : short MES quand VIX breakout >120% moyenne 20d (expansion)
  - vix_calm_hedge : short MES quand VIX < 13 (ultra-calme, premium faible)

Gate special (cf. execution plan) : standalone peut etre negatif, mais
**delta portfolio MaxDD doit etre > +2pp** pour PROMOTE_LIVE_SMALL.
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

BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
VIX_PATH = ROOT / "data" / "futures" / "VIX_LONG.parquet"
MES_PATH = ROOT / "data" / "futures" / "MES_LONG.parquet"
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"

MES_POINT_VALUE = 5.0
MES_RT_COST = 6.70


def load_daily(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def variant_short_mes_vix_low(mes: pd.DataFrame, vix: pd.DataFrame, threshold: float = 15) -> pd.Series:
    """Short MES (long vol proxy) when VIX < threshold."""
    common = mes.index.intersection(vix.index)
    mes_c = mes.loc[common]
    vix_c = vix.loc[common]
    signal = (vix_c["close"] < threshold).shift(1).fillna(False)
    # Short open-to-close: PnL = (open - close) * point_value
    oc_ret = (mes_c["open"] - mes_c["close"]) * MES_POINT_VALUE
    pnl = oc_ret.where(signal, 0.0)
    cost = np.where(signal, MES_RT_COST, 0.0)
    pnl = (pnl - cost).astype(float)
    pnl.name = f"short_mes_vix_lt_{int(threshold)}"
    return pnl


def variant_vix_breakout_short(mes: pd.DataFrame, vix: pd.DataFrame, ratio: float = 1.2) -> pd.Series:
    """Short MES when VIX > 1.2x 20d mean (expansion event)."""
    common = mes.index.intersection(vix.index)
    mes_c = mes.loc[common]
    vix_c = vix.loc[common]
    vix_ma20 = vix_c["close"].rolling(20).mean()
    signal = (vix_c["close"] > ratio * vix_ma20).shift(1).fillna(False)
    oc_ret = (mes_c["open"] - mes_c["close"]) * MES_POINT_VALUE
    pnl = oc_ret.where(signal, 0.0)
    cost = np.where(signal, MES_RT_COST, 0.0)
    pnl = (pnl - cost).astype(float)
    pnl.name = f"short_mes_vix_breakout_{int(ratio*100)}"
    return pnl


def main():
    print("=== T2-A : Futures crisis alpha / vol overlay ===\n")
    mes = load_daily(MES_PATH)
    vix = load_daily(VIX_PATH)
    print(f"MES: {mes.index.min().date()} -> {mes.index.max().date()} ({len(mes)}d)")
    print(f"VIX: {vix.index.min().date()} -> {vix.index.max().date()} ({len(vix)}d)")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    variants = [
        variant_short_mes_vix_low(mes, vix, 13),
        variant_short_mes_vix_low(mes, vix, 15),
        variant_short_mes_vix_low(mes, vix, 18),
        variant_vix_breakout_short(mes, vix, 1.2),
        variant_vix_breakout_short(mes, vix, 1.3),
        variant_vix_breakout_short(mes, vix, 1.5),
    ]

    print(f"\n{'Variant':<40s} {'Trades':>8s} {'TotPnL$':>10s}")
    for v in variants:
        t = int((v != 0).sum())
        p = float(v.sum())
        print(f"{v.name:<40s} {t:>8d} {p:>+10.0f}")

    print("\n--- Scoring ---")
    scorecards = []
    for v in variants:
        try:
            sc = score_candidate(v.name, v, baseline, 10_000.0, 1.0)
            scorecards.append(sc)
            print(f"  [{sc.verdict:<20s}] {sc.candidate_id:<40s} "
                  f"score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
                  f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")
        except Exception as e:
            print(f"  [SKIP] {v.name}: {e}")

    # Custom gate for crisis alpha: even if DROP, keep those with dMaxDD > +2pp as crisis hedge candidates
    crisis_keep = [sc for sc in scorecards if sc.delta_maxdd >= 2.0]

    scorecards.sort(key=lambda r: r.marginal_score, reverse=True)

    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    (JSON_OUT_DIR / "T2-01_scorecards.json").write_text(
        json.dumps([sc.to_dict() for sc in scorecards], indent=2, default=str))

    md = [
        "# T2-A — Futures crisis alpha / vol overlay",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Methodologie** : short MES a proxy long vol (VIX calm ou VIX breakout).",
        "**Gate special** : PROMOTE_LIVE_SMALL possible meme si dSharpe negatif, SI dMaxDD >= +2pp",
        "(crisis hedge convexe).",
        "",
        "## Scorecards",
        "",
        "| Variant | Verdict | Score | dSharpe | dMaxDD | Corr | Crisis hedge? |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for sc in scorecards:
        ch = "YES (dMaxDD>=+2pp)" if sc.delta_maxdd >= 2.0 else "-"
        md.append(
            f"| `{sc.candidate_id}` | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | "
            f"{sc.delta_maxdd:+.2f}pp | {sc.corr_to_portfolio:+.2f} | {ch} |"
        )

    md += ["", "## Crisis hedge candidates (dMaxDD >= +2pp)", ""]
    if crisis_keep:
        for sc in crisis_keep:
            md.append(f"- `{sc.candidate_id}` : dMaxDD {sc.delta_maxdd:+.2f}pp, standalone may be negative")
    else:
        md.append("(none)")
    md.append("")

    (MD_OUT_DIR / "T2-01_crisis_alpha.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nCrisis hedge keepers: {len(crisis_keep)}")
    for sc in crisis_keep:
        print(f"  - {sc.candidate_id}: dMaxDD={sc.delta_maxdd:+.2f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
