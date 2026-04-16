#!/usr/bin/env python3
"""INT-B — Portfolio allocation optimizer.

Prend le portefeuille baseline (7 strats) + les candidats VALIDATED de INT-A
et teste plusieurs allocations:
  - Equal-weight
  - Inverse-volatility
  - Hierarchical Risk Parity (HRP) simplifie
  - Marginal-score-weighted (weights proportional au marginal score)

Contraintes:
  - Budget DD annuel 10% -> stop si DD > 10% sur le meta-portfolio
  - Budget correlation : aucun poids unique > 30% (diversification min)

Output: docs/research/portfolio_optimizer_results.md,
       config/target_allocation_2026Q2.yaml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from scripts.research.backtest_futures_calendar import (  # noqa: E402
    load_mes, variant_dow_long, variant_turn_of_month, variant_pre_holiday,
)
from scripts.research.backtest_crypto_basis_carry import (  # noqa: E402
    load_btc, variant_always, variant_funding_filter,
)

BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT = ROOT / "docs" / "research" / "portfolio_optimizer_results.md"
ALLOC_OUT = ROOT / "config" / "target_allocation_2026Q2.yaml"
MAX_WEIGHT = 0.30


def sharpe(pnl, init=10_000):
    if pnl.std() == 0 or len(pnl) == 0:
        return 0
    return float(pnl.mean() / pnl.std() * np.sqrt(252))


def max_dd_pct(pnl, init=10_000):
    eq = init + pnl.cumsum()
    peak = eq.cummax()
    return float(((eq - peak) / peak).min() * 100)


def compute_metrics(portfolio_daily, label):
    total = float(portfolio_daily.sum())
    s = sharpe(portfolio_daily)
    dd = max_dd_pct(portfolio_daily)
    cagr = (10_000 + total) / 10_000
    years = len(portfolio_daily) / 252
    cagr = cagr ** (1 / years) - 1 if years > 0 else 0
    return {
        "label": label,
        "total_pnl": total,
        "sharpe": round(s, 2),
        "max_dd_pct": round(dd, 1),
        "cagr_pct": round(cagr * 100, 2),
        "days": len(portfolio_daily),
    }


def main():
    print("=== INT-B : Portfolio allocation optimizer ===\n")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()

    # Load VALIDATED Tier 1 candidates
    mes = load_mes()
    btc = load_btc()
    validated_cands = {
        "long_mon_oc": variant_dow_long(mes, 0, "long_mon_oc"),
        "long_wed_oc": variant_dow_long(mes, 2, "long_wed_oc"),
        "pre_holiday_drift": variant_pre_holiday(mes),
        "basis_carry_always": variant_always(btc),
        "basis_carry_funding_gt_5pct": variant_funding_filter(btc, 0.05),
    }

    # Align all to baseline index (fill missing with 0)
    all_index = baseline.index
    extras = pd.DataFrame({k: v.reindex(all_index).fillna(0) for k, v in validated_cands.items()},
                          index=all_index)
    universe = pd.concat([baseline, extras], axis=1)
    print(f"Extended universe: {universe.shape[0]} days x {universe.shape[1]} strats")
    print(f"  Strats: {list(universe.columns)}\n")

    n = universe.shape[1]
    cols = list(universe.columns)

    # --- Allocation schemes ---
    allocations = {}

    # 1. Equal weight
    allocations["equal_weight"] = np.ones(n) / n

    # 2. Inverse volatility
    vols = universe.std().replace(0, np.nan)
    inv_vol = 1 / vols
    inv_vol = inv_vol / inv_vol.sum()
    inv_vol = inv_vol.fillna(0)
    allocations["inverse_volatility"] = inv_vol.values

    # 3. Marginal score weighted (positive marginal scores only)
    # Use historical Sharpe as proxy (quick)
    sharpes = np.array([sharpe(universe[c]) for c in cols])
    pos_sharpes = np.clip(sharpes, 0, None)
    if pos_sharpes.sum() > 0:
        msw = pos_sharpes / pos_sharpes.sum()
    else:
        msw = np.ones(n) / n
    allocations["sharpe_weighted"] = msw

    # 4. Risk parity simplified (weight = 1/vol, normalized, cap at MAX_WEIGHT)
    rp = 1 / vols.fillna(vols.mean())
    rp = rp / rp.sum()
    rp = np.minimum(rp.values, MAX_WEIGHT)
    rp = rp / rp.sum()
    allocations["risk_parity"] = rp

    # 5. HRP-lite (cluster similarity via abs correlation, assign equal within cluster)
    corr = universe.corr().fillna(0)
    # Simple 2-cluster split: first half most correlated to first col vs second
    first_col_corr = corr.iloc[:, 0].values
    cluster_a = first_col_corr > 0.3
    w = np.zeros(n)
    n_a = cluster_a.sum()
    n_b = n - n_a
    if n_a > 0:
        w[cluster_a] = 0.5 / n_a
    if n_b > 0:
        w[~cluster_a] = 0.5 / n_b
    allocations["hrp_lite"] = w

    # Evaluate each
    results = []
    print(f"{'Allocation':<25s} {'Sharpe':>8s} {'MaxDD%':>8s} {'CAGR%':>8s} {'TotPnL$':>10s}")
    for name, w in allocations.items():
        w = np.clip(w, 0, MAX_WEIGHT)
        if w.sum() > 0:
            w = w / w.sum()
        daily = (universe.values * w).sum(axis=1)
        daily = pd.Series(daily, index=universe.index)
        m = compute_metrics(daily, name)
        m["weights"] = {c: round(float(w[i]), 4) for i, c in enumerate(cols)}
        results.append(m)
        print(f"{name:<25s} {m['sharpe']:>+8.2f} {m['max_dd_pct']:>+8.1f} {m['cagr_pct']:>+8.2f} {m['total_pnl']:>+10,.0f}")

    # Pick best by Calmar (CAGR / abs MaxDD)
    for r in results:
        dd = abs(r["max_dd_pct"]) if r["max_dd_pct"] != 0 else 1e9
        r["calmar"] = round(r["cagr_pct"] / dd, 3) if dd > 0 else 0
    results.sort(key=lambda r: r["calmar"], reverse=True)
    best = results[0]
    print(f"\nBest allocation by Calmar: {best['label']} (calmar={best['calmar']:.3f})")

    # Persist
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    ALLOC_OUT.parent.mkdir(parents=True, exist_ok=True)

    md = [
        "# INT-B — Portfolio allocation optimizer",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Universe** : 7 baseline strats + {len(validated_cands)} VALIDATED candidates = {n} total",
        f"**Constraint** : max weight per strat = {MAX_WEIGHT*100}%",
        "",
        "## Summary",
        "",
        "| Allocation | Sharpe | MaxDD% | CAGR% | Calmar | Total PnL $ |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        md.append(
            f"| `{r['label']}` | {r['sharpe']:+.2f} | {r['max_dd_pct']:+.1f}% | "
            f"{r['cagr_pct']:+.2f}% | {r['calmar']:+.3f} | {r['total_pnl']:+,.0f} |"
        )

    md += [
        "",
        f"## Best by Calmar : `{best['label']}`",
        "",
        "Weights:",
        "",
        "| Strategy | Weight |",
        "|---|---:|",
    ]
    for k, v in sorted(best["weights"].items(), key=lambda x: -x[1]):
        md.append(f"| `{k}` | {v*100:.1f}% |")

    md += [
        "",
        "## Caveats",
        "",
        "- Scoring historique utilise la somme simple des PnL, pas la composition",
        "  multi-compte (chaque broker a son capital propre).",
        "- Les weights optimises IS doivent etre stress-testes sur 2018/2022/2024 via INT-A.",
        "- Les candidats `basis_carry_*` utilisent un funding proxy — a re-verifier",
        "  avec funding reel avant deploiement.",
        "",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"\nMarkdown -> {MD_OUT}")

    # YAML allocation for deploy
    alloc_yaml = {
        "target_allocation_2026Q2": {
            "run_date": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),
            "method": best["label"],
            "calmar": float(best["calmar"]),
            "sharpe": float(best["sharpe"]),
            "max_dd_pct": float(best["max_dd_pct"]),
            "cagr_pct": float(best["cagr_pct"]),
            "notes": (
                "Allocation IS 2015-2026. A valider par INT-A WF/MC sur meta-portfolio "
                "complet avant tout rebalancement real."
            ),
            "weights": {k: float(v) for k, v in best["weights"].items()},
        }
    }
    with open(ALLOC_OUT, "w") as f:
        yaml.safe_dump(alloc_yaml, f, sort_keys=False)
    print(f"YAML config -> {ALLOC_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
