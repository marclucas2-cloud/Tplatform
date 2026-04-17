#!/usr/bin/env python3
"""WP-04 — Diversification gap map initial.

Analyse ce qui MANQUE au portefeuille actuel :
  - trous de regime: quand est-ce que le portefeuille perd collectivement ?
  - trous d'horizon: quels horizons de detention sont sous-represented ?
  - trous de style: quels moteurs economiques sont absents ?
  - trous de capital utilization: quand le capital dort alors que ca pourrait trader ?

Output: docs/research/diversification_gap_map.md
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
IN_TS = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
IN_INV = ROOT / "data" / "research" / "portfolio_strategy_inventory.csv"
DOCS_DIR = ROOT / "docs" / "research"
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def analyze_regime_gaps(returns: pd.DataFrame) -> dict:
    """Find periods where the full portfolio is in loss."""
    total = returns.sum(axis=1)
    cum_eq = 10000 + total.cumsum()
    peak = cum_eq.cummax()
    dd = (cum_eq - peak) / peak

    # Drawdowns > 5%
    in_dd = dd < -0.05
    dd_periods = []
    if in_dd.any():
        current_start = None
        for i in range(len(dd)):
            if in_dd.iloc[i] and current_start is None:
                current_start = i
            elif not in_dd.iloc[i] and current_start is not None:
                dd_periods.append({
                    "start": dd.index[current_start].date().isoformat(),
                    "end": dd.index[i - 1].date().isoformat(),
                    "days": i - current_start,
                    "depth": float(dd.iloc[current_start:i].min()),
                })
                current_start = None
        if current_start is not None:
            dd_periods.append({
                "start": dd.index[current_start].date().isoformat(),
                "end": dd.index[-1].date().isoformat(),
                "days": len(dd) - current_start,
                "depth": float(dd.iloc[current_start:].min()),
            })

    # Deepest drawdowns
    dd_periods.sort(key=lambda x: x["depth"])
    return {
        "n_dd_periods": len(dd_periods),
        "worst_dd": dd_periods[:5],  # 5 pires
        "longest_dd_days": max((p["days"] for p in dd_periods), default=0),
    }


def analyze_horizon_gaps(inventory: pd.DataFrame) -> dict:
    """Find horizon buckets under-represented."""
    horizons = inventory[inventory["status"].isin(["live_core", "live_probation"])]["horizon_days"]
    horizons = horizons[horizons > 0]
    buckets = {
        "intraday (<=1d)": ((horizons > 0) & (horizons <= 1)).sum(),
        "short (2-5d)":    ((horizons >= 2) & (horizons <= 5)).sum(),
        "swing (6-20d)":   ((horizons >= 6) & (horizons <= 20)).sum(),
        "position (>20d)": (horizons > 20).sum(),
    }
    total = sum(buckets.values())
    pcts = {k: (v / total * 100 if total > 0 else 0) for k, v in buckets.items()}
    return {
        "buckets": buckets,
        "pct": pcts,
        "dominant": max(buckets, key=buckets.get) if total > 0 else None,
    }


def analyze_signal_family_gaps(inventory: pd.DataFrame) -> dict:
    """Find signal families under-represented."""
    live = inventory[inventory["status"].isin(["live_core", "live_probation"])]
    families = live.groupby("signal_family").size().to_dict()

    # All known families
    all_families = {
        "momentum_trend": "trend following",
        "volatility_breakout": "volatility / breakout",
        "cross_asset_rotation": "cross-asset rotation",
        "carry_yield": "carry / yield (basis, funding, borrow)",
        "mean_reversion": "mean reversion",
        "calendar_seasonal": "calendar / seasonal / day-of-week",
        "event_driven": "event-driven (earnings, liquidations, news)",
        "bear_directional": "bear / short bias",
        "relative_value": "relative value / pairs / spreads",
        "dispersion": "dispersion / cross-sectional",
        "crisis_alpha": "crisis alpha / convexity / vol long",
    }
    missing = [name for name in all_families if name not in families]
    present = families

    return {
        "present": present,
        "missing": missing,
        "all_families_desc": all_families,
    }


def analyze_capital_occupancy(returns: pd.DataFrame) -> dict:
    """Rough capital occupancy: how many days does the portfolio actively PnL?"""
    total = returns.sum(axis=1)
    active_days = (total != 0).sum()
    total_days = len(total)
    occupancy = active_days / total_days if total_days > 0 else 0
    idle_days = total_days - active_days
    return {
        "active_days": int(active_days),
        "total_days": int(total_days),
        "occupancy_pct": round(occupancy * 100, 1),
        "idle_days": int(idle_days),
    }


def main():
    print("=" * 72)
    print("WP-04 Diversification gap map")
    print("=" * 72)

    if not IN_TS.exists() or not IN_INV.exists():
        print("ERROR: run build_portfolio_baseline.py first")
        return 1

    returns = pd.read_parquet(IN_TS)
    returns.index = pd.to_datetime(returns.index)
    inventory = pd.read_csv(IN_INV)

    print(f"\nLoaded: {returns.shape[0]} days x {returns.shape[1]} strats + {len(inventory)} inventory entries")

    regime_gaps = analyze_regime_gaps(returns)
    horizon_gaps = analyze_horizon_gaps(inventory)
    family_gaps = analyze_signal_family_gaps(inventory)
    cap_gaps = analyze_capital_occupancy(returns)

    print(f"\nRegime gaps: {regime_gaps['n_dd_periods']} drawdown periods >5%, worst 5:")
    for p in regime_gaps["worst_dd"]:
        print(f"  {p['start']} -> {p['end']} ({p['days']}d, depth {p['depth']*100:.1f}%)")

    print(f"\nHorizon buckets:")
    for k, v in horizon_gaps["buckets"].items():
        pct = horizon_gaps["pct"][k]
        print(f"  {k}: {v} ({pct:.0f}%)")

    print(f"\nSignal families present: {list(family_gaps['present'].keys())}")
    print(f"Signal families MISSING: {family_gaps['missing']}")

    print(f"\nCapital occupancy: {cap_gaps['occupancy_pct']}% "
          f"({cap_gaps['active_days']}/{cap_gaps['total_days']} days active)")

    # === Markdown report ===
    lines = [
        f"# Diversification Gap Map — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        "**WP-04 decorrelation research** — cartographie des trous du portefeuille.",
        "",
        "## Executive summary",
        "",
    ]

    # Dominant horizon
    if horizon_gaps["dominant"]:
        lines.append(f"- Horizon dominant: **{horizon_gaps['dominant']}** "
                     f"({horizon_gaps['pct'][horizon_gaps['dominant']]:.0f}% des strats live)")

    # Missing families
    if family_gaps["missing"]:
        lines.append(f"- Familles de signal **ABSENTES du live**: {', '.join(family_gaps['missing'])}")

    lines.append(f"- Capital occupancy: {cap_gaps['occupancy_pct']}% "
                 f"({cap_gaps['idle_days']}/{cap_gaps['total_days']} jours idle)")
    lines.append(f"- Nombre de drawdowns >5% historiques: **{regime_gaps['n_dd_periods']}**")
    lines.append(f"- Longest DD: **{regime_gaps['longest_dd_days']}** jours")
    lines.append("")

    lines += [
        "## Trous de regime — drawdowns historiques",
        "",
        "Les 5 pires periodes historiques ou le portefeuille global perd. Chercher",
        "des strategies qui auraient genere du PnL pendant ces periodes specifiques.",
        "",
        "| # | Start | End | Days | Depth |",
        "|---|---|---|---|---|",
    ]
    for i, p in enumerate(regime_gaps["worst_dd"], 1):
        lines.append(f"| {i} | {p['start']} | {p['end']} | {p['days']} | {p['depth']*100:.1f}% |")

    lines += [
        "",
        "### Interpretation regime",
        "",
        "Ces periodes DD correspondent typiquement a des regimes ou les 3 strats futures",
        "actuelles (momentum/trend et rotation commodity) perdent ensemble. Candidats",
        "prioritaires pour couvrir ces trous:",
        "- **Mean reversion short-horizon**: capture les rebounds post-fort drawdown",
        "- **Carry / basis / funding**: source de rendement independante du trend",
        "- **Crisis alpha / vol long**: convexite positive en stress equity",
        "- **Event-driven**: returns non directionnels",
        "",
        "## Trous d'horizon de detention",
        "",
        "| Bucket | Count | Pct |",
        "|---|---|---|",
    ]
    for k, v in horizon_gaps["buckets"].items():
        pct = horizon_gaps["pct"][k]
        lines.append(f"| {k} | {v} | {pct:.0f}% |")

    lines += [
        "",
        "### Interpretation horizon",
        "",
        "Le portefeuille futures actuel est concentre sur **swing (6-20j)**. Manque:",
        "- **Intraday / end-of-day** (<1j) pour diversifier par timing",
        "- **Position longue (>20j)** pour capturer les grandes tendances",
        "",
        "Un moteur intraday sur MES/MGC aux heures d'ouverture US apporterait de la",
        "diversification sans chevaucher les strats swing existantes.",
        "",
        "## Trous de famille de signal",
        "",
        "### Presents",
        "",
    ]
    for fam, count in family_gaps["present"].items():
        desc = family_gaps["all_families_desc"].get(fam, "")
        lines.append(f"- `{fam}` ({count}): {desc}")

    lines += [
        "",
        "### ABSENTS (candidats prioritaires)",
        "",
    ]
    for fam in family_gaps["missing"]:
        desc = family_gaps["all_families_desc"].get(fam, "")
        lines.append(f"- `{fam}`: {desc}")

    lines += [
        "",
        "## Capital occupancy",
        "",
        f"- Jours actifs (PnL non nul): **{cap_gaps['active_days']}** / {cap_gaps['total_days']} "
        f"= {cap_gaps['occupancy_pct']}%",
        f"- Jours idle: {cap_gaps['idle_days']}",
        "",
        "Si occupancy < 60%, il y a de la place pour un moteur haute-frequence qui",
        "travaille les jours ou les strats swing sont en attente.",
        "",
        "## Priorisation candidats (preliminaire)",
        "",
        "Sur la base des gaps identifies, les candidats Tier 1 a explorer en priorite:",
        "",
        "1. **Crypto basis / funding carry** — market-neutral, source de rendement",
        "   independante du momentum futures (carry_yield absent)",
        "2. **US post-earnings drift** — event-driven, horizon court (<5j),",
        "   travaille sur les heures US ou le book futures est calme (event_driven absent)",
        "3. **Futures mean reversion intraday (MES/MGC)** — monetise les excess moves",
        "   apres grandes journees (mean_reversion sous-represente)",
        "4. **FX cross-sectional carry** (si contournement ESMA possible) — carry sur",
        "   bloc devises, decoupage par regime de vol",
        "5. **Crypto long/short cross-sectional** — alts vs BTC dominance, market neutral",
        "",
        "## Prochaine etape",
        "",
        "WP-09 a WP-13 : batches de backtests par famille, chaque candidate passee par",
        "le scoring marginal `scripts/research/portfolio_marginal_score.py`.",
        "",
    ]

    report_path = DOCS_DIR / "diversification_gap_map.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[ok] {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
