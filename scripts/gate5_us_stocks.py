#!/usr/bin/env python3
"""Gate 5 — Portfolio combine V15.3 + 3 US stock candidats (tom, rs_spy, sector_rot).

Question: est-ce qu'ajouter chaque candidat au portfolio V15.3 existant ameliore
le Sharpe OU reduit le MaxDD combine? Si degradation, REJECT meme si le backtest
isole est bon.

Methodologie:
  - Baseline V15.3 = 4 LIVE IBKR + EU-06 MacroECB, $10K, slot manager 3 pos max.
  - Allocation US: $25K total, split 3 ways = $8333 par strat.
  - Combined capital: $35K.
  - Daily PnL series: baseline + chaque combinaison de US strats.
  - Metrics: annualized Sharpe (sqrt(252)), MaxDD en %, total return.
  - Gate 5 PASS: Sharpe_combined > Sharpe_baseline OR MaxDD_combined > MaxDD_baseline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.backtest_portfolio_v153 import (
    run_4_live_strats,
    run_eu06_strats,
    combine_portfolio,
)

TRADES_DIR = ROOT / "reports" / "us_research"
OUT = TRADES_DIR / "gate5_report.md"

V153_CAPITAL = 10_000
US_CAPITAL_PER_STRAT = 8_333   # $25K / 3 strats
COMBINED_CAPITAL = V153_CAPITAL + 3 * US_CAPITAL_PER_STRAT   # $35K

CANDIDATES = ["tom", "rs_spy", "sector_rot"]

STRAT_MAX_CONCURRENT = {
    "tom": 10,          # top 10 momentum stocks
    "rs_spy": 10,       # 5 long + 5 short
    "sector_rot": 2,    # 1 long + 1 short per sector
    "high_52w": 5,      # arbitrary cap on simultaneous breakouts
    "pead": 3,          # arbitrary cap
}


def trades_to_daily_usd(trades, get_entry, get_exit, get_pnl_usd, freq="B") -> pd.Series:
    """Distribute each trade's PnL across its holding days (business days)."""
    daily = {}
    for t in trades:
        entry = pd.Timestamp(get_entry(t)).tz_localize(None) if hasattr(get_entry(t), "tz_convert") else pd.Timestamp(get_entry(t))
        exit_d = pd.Timestamp(get_exit(t)).tz_localize(None) if hasattr(get_exit(t), "tz_convert") else pd.Timestamp(get_exit(t))
        if entry.tz is not None:
            entry = entry.tz_localize(None)
        if exit_d.tz is not None:
            exit_d = exit_d.tz_localize(None)
        if exit_d <= entry:
            exit_d = entry + pd.Timedelta(days=1)
        rng = pd.bdate_range(entry, exit_d)
        if len(rng) == 0:
            continue
        per_day = get_pnl_usd(t) / len(rng)
        for d in rng:
            daily[d] = daily.get(d, 0.0) + per_day
    return pd.Series(daily).sort_index() if daily else pd.Series(dtype=float)


def load_us_strat_daily_usd(name: str, notional: float) -> pd.Series:
    """Load trades CSV and convert to daily USD PnL at given notional.

    Sizes each position as notional / max_concurrent so a strat running 10
    concurrent positions doesn't multiply its exposure 10x.
    """
    f = TRADES_DIR / f"trades_{name}.csv"
    df = pd.read_csv(f)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    concurrent = STRAT_MAX_CONCURRENT.get(name, 1)
    pos_size = notional / concurrent
    trades = df.to_dict("records")
    return trades_to_daily_usd(
        trades,
        get_entry=lambda t: t["entry_date"],
        get_exit=lambda t: t["exit_date"],
        get_pnl_usd=lambda t: t["pnl_net"] * pos_size,
    )


def compute_metrics(daily_pnl: pd.Series, capital: float, label: str) -> dict:
    if len(daily_pnl) == 0:
        return {"label": label, "n_days": 0}
    daily_ret = daily_pnl / capital
    cum = daily_pnl.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    mdd_usd = float(dd.min())
    mdd_pct = mdd_usd / capital * 100
    total_pnl = float(daily_pnl.sum())
    total_ret_pct = total_pnl / capital * 100
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    n_days = len(daily_pnl)
    years = n_days / 252
    roc_ann = total_ret_pct / years if years > 0 else 0
    return {
        "label": label,
        "n_days": n_days,
        "total_pnl_usd": round(total_pnl, 0),
        "total_ret_pct": round(total_ret_pct, 1),
        "roc_ann_pct": round(roc_ann, 1),
        "sharpe": round(sharpe, 2),
        "max_dd_usd": round(mdd_usd, 0),
        "max_dd_pct": round(mdd_pct, 1),
    }


def run_baseline_daily() -> pd.Series:
    """Run V15.3 baseline and extract daily PnL series in USD."""
    print("[baseline] running V15.3 4 LIVE strats…")
    live = run_4_live_strats()
    print(f"  -> {len(live)} trades")
    print("[baseline] running EU-06 MacroECB…")
    eu06 = run_eu06_strats()
    print(f"  -> {len(eu06)} trades")
    print("[baseline] slot manager combining…")
    accepted, rej, preempted = combine_portfolio(live + eu06, max_pos=3, allow_preempt=True)
    print(f"  -> {len(accepted)} accepted, {rej} rejected, {preempted} preempted")

    daily = trades_to_daily_usd(
        accepted,
        get_entry=lambda t: t.entry_dt,
        get_exit=lambda t: t.exit_dt,
        get_pnl_usd=lambda t: t.pnl,
    )
    return daily


def format_row(m: dict) -> str:
    return (
        f"| {m['label']} | {m.get('n_days', 0)} | "
        f"${m.get('total_pnl_usd', 0):+,.0f} | "
        f"{m.get('total_ret_pct', 0):+.1f}% | "
        f"{m.get('roc_ann_pct', 0):+.1f}% | "
        f"**{m.get('sharpe', 0):.2f}** | "
        f"${m.get('max_dd_usd', 0):,.0f} | "
        f"{m.get('max_dd_pct', 0):.1f}% |"
    )


def main():
    print("=" * 80)
    print("  GATE 5 — Portfolio V15.3 + US Stock Candidates")
    print("=" * 80)

    # Baseline
    baseline_daily = run_baseline_daily()
    baseline_m = compute_metrics(baseline_daily, V153_CAPITAL, "V15.3 baseline ($10K)")
    print(f"\nBaseline V15.3:")
    print(f"  Sharpe  : {baseline_m['sharpe']}")
    print(f"  MaxDD   : ${baseline_m['max_dd_usd']:,.0f} ({baseline_m['max_dd_pct']:.1f}%)")
    print(f"  Total   : ${baseline_m['total_pnl_usd']:,.0f}")

    # Load each US strat daily PnL (at $8333 notional per strat)
    us_daily = {}
    for name in CANDIDATES:
        s = load_us_strat_daily_usd(name, US_CAPITAL_PER_STRAT)
        us_daily[name] = s
        m = compute_metrics(s, US_CAPITAL_PER_STRAT, f"US {name} (${US_CAPITAL_PER_STRAT:.0f})")
        print(f"\nUS {name}: Sharpe {m['sharpe']}, MaxDD ${m['max_dd_usd']:,.0f} ({m['max_dd_pct']:.1f}%), Total ${m['total_pnl_usd']:,.0f}")

    # Align on baseline date range (intersect)
    base_idx = baseline_daily.index
    if len(base_idx) == 0:
        print("ERROR: empty baseline")
        return 1

    def align_sum(series_list):
        combined_idx = base_idx
        for s in series_list:
            combined_idx = combined_idx.union(s.index)
        combined_idx = combined_idx[(combined_idx >= base_idx.min()) & (combined_idx <= base_idx.max())]
        total = pd.Series(0.0, index=combined_idx)
        for s in series_list:
            aligned = s.reindex(combined_idx).fillna(0)
            total = total + aligned
        return total

    # Test each combination
    combos = [
        ("baseline only", []),
        ("baseline + tom", ["tom"]),
        ("baseline + rs_spy", ["rs_spy"]),
        ("baseline + sector_rot", ["sector_rot"]),
        ("baseline + tom + rs_spy", ["tom", "rs_spy"]),
        ("baseline + tom + rs_spy + sector_rot (ALL)", ["tom", "rs_spy", "sector_rot"]),
    ]

    print("\n" + "=" * 80)
    print("  COMBINED PORTFOLIO METRICS")
    print("=" * 80)

    results = []
    for label, us_names in combos:
        if not us_names:
            capital = V153_CAPITAL
            combined = baseline_daily.copy()
        else:
            capital = V153_CAPITAL + len(us_names) * US_CAPITAL_PER_STRAT
            combined = align_sum([baseline_daily] + [us_daily[n] for n in us_names])
        m = compute_metrics(combined, capital, label)
        m["capital"] = capital
        results.append(m)
        print(f"\n{label} (capital ${capital:,.0f}):")
        print(f"  Sharpe    : {m['sharpe']}")
        print(f"  MaxDD     : ${m['max_dd_usd']:,.0f} ({m['max_dd_pct']:.1f}%)")
        print(f"  Total PnL : ${m['total_pnl_usd']:,.0f} ({m['total_ret_pct']:.1f}% = {m['roc_ann_pct']:.1f}%/an)")

    # Write report
    lines = [
        "# Gate 5 — Portfolio V15.3 + US Stock Candidates",
        "",
        f"Baseline: V15.3 (4 LIVE + EU-06 MacroECB), $10K capital, 3 ans (2023-04 → 2026-04).",
        f"US candidates: 3 strats ($8,333 chacune, total $25K).",
        f"Combined capital: $35K.",
        "",
        "## Gate 5 critère",
        "",
        "**PASS** si la combinaison ameliore Sharpe OU reduit MaxDD (%) vs baseline.",
        "",
        "## Résultats",
        "",
        "| Configuration | Days | Total PnL | Total Ret | ROC/an | Sharpe | MaxDD $ | MaxDD % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in results:
        lines.append(format_row(m))
    lines.append("")

    # Verdict per candidate
    base = results[0]
    lines.append("## Verdict par candidat")
    lines.append("")
    for i, name in enumerate(CANDIDATES, start=1):
        m = results[i]  # corresponds to "baseline + <name>"
        d_sharpe = m["sharpe"] - base["sharpe"]
        d_mdd_pct = m["max_dd_pct"] - base["max_dd_pct"]  # more negative = worse
        improved_sharpe = d_sharpe > 0
        improved_mdd = d_mdd_pct > base["max_dd_pct"]  # smaller magnitude
        # Simpler: compare absolute MDD %
        if m["max_dd_pct"] > base["max_dd_pct"]:  # less negative = better
            improved_mdd = True
        else:
            improved_mdd = False
        passed = improved_sharpe or improved_mdd
        verdict = "PASS" if passed else "FAIL"
        lines.append(f"### {name}")
        lines.append(f"- Sharpe: {base['sharpe']:.2f} → {m['sharpe']:.2f} ({d_sharpe:+.2f}) — {'✓ mieux' if improved_sharpe else '✗ pire'}")
        lines.append(f"- MaxDD%: {base['max_dd_pct']:.1f}% → {m['max_dd_pct']:.1f}% — {'✓ mieux' if improved_mdd else '✗ pire'}")
        lines.append(f"- **Verdict: {verdict}**")
        lines.append("")

    # ALL combined
    all_m = results[-1]
    lines.append("## Combinaison des 3 candidats")
    lines.append("")
    lines.append(f"- Sharpe: {base['sharpe']:.2f} → **{all_m['sharpe']:.2f}**")
    lines.append(f"- MaxDD%: {base['max_dd_pct']:.1f}% → **{all_m['max_dd_pct']:.1f}%**")
    lines.append(f"- Total PnL: ${base['total_pnl_usd']:,.0f} → **${all_m['total_pnl_usd']:,.0f}**")
    lines.append(f"- ROC/an: {base['roc_ann_pct']:.1f}%/an → **{all_m['roc_ann_pct']:.1f}%/an**")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nRapport: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
