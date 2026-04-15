#!/usr/bin/env python3
"""WP-02 — Portfolio correlation lab + clustering + overlap.

Extends `scripts/correlation_matrix_strats.py` with:
  - Full correlation matrix (Pearson + Spearman)
  - Rolling 60d correlation
  - Downside correlation (when both strats in loss)
  - Drawdown overlap (pires jours en commun)
  - Hierarchical clustering des strategies
  - Output CSV + markdown report lisible

Input: data/research/portfolio_baseline_timeseries.parquet (WP-01 output)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
IN_TS = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
OUT_DIR = ROOT / "output" / "research"
DOCS_DIR = ROOT / "docs" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def load_returns() -> pd.DataFrame:
    if not IN_TS.exists():
        raise FileNotFoundError(f"{IN_TS} missing — run build_portfolio_baseline.py first")
    df = pd.read_parquet(IN_TS)
    df.index = pd.to_datetime(df.index)
    return df


def pearson_corr(df: pd.DataFrame) -> pd.DataFrame:
    return df.corr(method="pearson").round(3)


def spearman_corr(df: pd.DataFrame) -> pd.DataFrame:
    return df.corr(method="spearman").round(3)


def rolling_corr(df: pd.DataFrame, window: int = 60) -> dict:
    """Return rolling 60d Pearson corr for each pair of strats."""
    results = {}
    cols = list(df.columns)
    for i, a in enumerate(cols):
        for b in cols[i+1:]:
            rc = df[a].rolling(window).corr(df[b])
            results[f"{a}_vs_{b}"] = {
                "mean": float(rc.mean()),
                "median": float(rc.median()),
                "min": float(rc.min()),
                "max": float(rc.max()),
                "std": float(rc.std()),
            }
    return results


def downside_corr(df: pd.DataFrame, threshold: float = 0.0) -> pd.DataFrame:
    """Correlation conditional on BOTH strategies being in loss (< threshold)."""
    cols = list(df.columns)
    out = pd.DataFrame(index=cols, columns=cols, dtype=float)
    for a in cols:
        for b in cols:
            if a == b:
                out.loc[a, b] = 1.0
                continue
            mask = (df[a] < threshold) & (df[b] < threshold)
            if mask.sum() < 5:
                out.loc[a, b] = np.nan
                continue
            sub = df.loc[mask, [a, b]]
            c = sub[a].corr(sub[b])
            out.loc[a, b] = round(c, 3) if np.isfinite(c) else np.nan
    return out


def worst_days_overlap(df: pd.DataFrame, top_n: int = 30) -> dict:
    """For each strategy, find its top N worst days and compute overlap with others."""
    results = {}
    for sid in df.columns:
        worst = df[sid].nsmallest(top_n).index
        overlaps = {}
        for other in df.columns:
            if other == sid:
                continue
            other_worst = df[other].nsmallest(top_n).index
            overlap = len(set(worst) & set(other_worst))
            overlaps[other] = overlap
        results[sid] = overlaps
    return results


def hierarchical_clustering(corr: pd.DataFrame) -> list:
    """Simple hierarchical clustering from correlation matrix.

    Uses (1 - corr) as distance. Returns clusters as groups of strat ids.
    """
    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
    except ImportError:
        return [[c] for c in corr.columns]

    dist = 1 - corr.abs()
    # Clip negative values (numerical errors), ensure diag is 0
    dist_values = dist.values.copy()
    np.fill_diagonal(dist_values, 0.0)
    dist_values = np.clip(dist_values, 0, 2)
    # Force symmetry
    dist_values = (dist_values + dist_values.T) / 2
    try:
        condensed = squareform(dist_values, checks=False)
        Z = linkage(condensed, method="average")
        # Cut at 0.5 distance = 50% corr
        labels = fcluster(Z, t=0.5, criterion="distance")
    except Exception as e:
        print(f"[warn] clustering failed: {e}")
        return [[c] for c in corr.columns]

    clusters = {}
    for sid, lbl in zip(corr.columns, labels):
        clusters.setdefault(int(lbl), []).append(sid)
    return list(clusters.values())


def main():
    print("=" * 72)
    print("WP-02 Portfolio correlation lab")
    print("=" * 72)

    returns = load_returns()
    print(f"\nLoaded: {returns.shape[0]} days x {returns.shape[1]} strategies")
    print(f"Columns: {list(returns.columns)}")

    # 1. Pearson correlation
    print("\nComputing Pearson correlation...")
    pc = pearson_corr(returns)
    print(pc.to_string())
    pc_path = OUT_DIR / "portfolio_correlation_matrix.csv"
    pc.to_csv(pc_path)
    print(f"  [ok] {pc_path}")

    # 2. Spearman (rank-based, robust to outliers)
    print("\nComputing Spearman correlation...")
    sc = spearman_corr(returns)
    print(sc.to_string())

    # 3. Rolling 60d correlation stats
    print("\nComputing rolling 60d correlation stats...")
    rc_stats = rolling_corr(returns, window=60)
    for pair, stats in rc_stats.items():
        print(f"  {pair}: mean={stats['mean']:+.3f} min={stats['min']:+.3f} "
              f"max={stats['max']:+.3f}")

    # 4. Downside correlation
    print("\nComputing downside correlation (both strats in loss)...")
    dc = downside_corr(returns)
    print(dc.to_string())
    dc_path = OUT_DIR / "portfolio_downside_corr.csv"
    dc.to_csv(dc_path)
    print(f"  [ok] {dc_path}")

    # 5. Worst days overlap
    print("\nComputing worst days overlap (top 30 worst days per strat)...")
    wd = worst_days_overlap(returns, top_n=30)
    for sid, ovs in wd.items():
        print(f"  {sid}: {ovs}")

    # 6. Clustering
    print("\nComputing hierarchical clustering (distance = 1 - |corr|)...")
    clusters = hierarchical_clustering(pc)
    for i, cl in enumerate(clusters, 1):
        print(f"  Cluster {i}: {cl}")

    # === Markdown report ===
    lines = [
        "# Portfolio Correlation Report — 2026-04-15",
        "",
        "**WP-02 decorrelation research** — analyse redondance portefeuille.",
        "",
        f"Scope: {returns.shape[1]} strategies sur {returns.shape[0]} jours",
        f"Source: `data/research/portfolio_baseline_timeseries.parquet`",
        f"Genere le: {datetime.utcnow().isoformat()}Z",
        "",
        "## Matrice de correlation Pearson",
        "",
        "```",
        pc.to_string(),
        "```",
        "",
        "## Interpretation",
        "",
    ]

    # Interpret the 3 pairs
    pairs = [
        ("cross_asset_momentum", "gold_trend_mgc"),
        ("cross_asset_momentum", "gold_oil_rotation"),
        ("gold_trend_mgc", "gold_oil_rotation"),
    ]
    for a, b in pairs:
        if a in pc.columns and b in pc.columns:
            corr = pc.loc[a, b]
            label = "FORTE" if abs(corr) > 0.5 else "MOYENNE" if abs(corr) > 0.3 else "FAIBLE"
            lines.append(f"- `{a}` vs `{b}`: {corr:+.3f} ({label})")

    lines += [
        "",
        "## Correlation descendante (both in loss)",
        "",
        "Capture si les strats perdent ensemble pendant les mauvaises periodes.",
        "Une correlation downside elevee = pas de diversification en cas de stress.",
        "",
        "```",
        dc.to_string(),
        "```",
        "",
        "## Overlap des 30 pires jours",
        "",
        "Combien des 30 pires jours de chaque strat sont communs avec les autres.",
        "",
        "| Strategy | Overlap avec autres strats |",
        "|---|---|",
    ]
    for sid, ovs in wd.items():
        ov_str = ", ".join(f"{k}={v}" for k, v in ovs.items())
        lines.append(f"| `{sid}` | {ov_str} |")

    lines += [
        "",
        "## Clusters hierarchiques",
        "",
        "Distance = 1 - |correlation|. Seuil de coupe: 0.5.",
        "Des strategies dans le meme cluster ont >=50% de corr absolue.",
        "",
    ]
    for i, cl in enumerate(clusters, 1):
        lines.append(f"- **Cluster {i}**: {', '.join(cl)}")

    lines += [
        "",
        "## Rolling 60d correlation stats",
        "",
        "| Pair | Mean | Min | Max | Std |",
        "|---|---|---|---|---|",
    ]
    for pair, stats in rc_stats.items():
        lines.append(
            f"| `{pair}` | {stats['mean']:+.3f} | {stats['min']:+.3f} | "
            f"{stats['max']:+.3f} | {stats['std']:.3f} |"
        )

    lines += [
        "",
        "## Verdict de redondance",
        "",
    ]

    # Automated verdict
    max_abs_corr = 0
    worst_pair = None
    for i, a in enumerate(pc.columns):
        for b in pc.columns[i+1:]:
            v = abs(pc.loc[a, b])
            if v > max_abs_corr:
                max_abs_corr = v
                worst_pair = (a, b)

    if max_abs_corr < 0.3:
        verdict = "EXCELLENT — tous les moteurs futures sont decorreles (<0.3)"
    elif max_abs_corr < 0.5:
        verdict = "BON — corr max moyenne, diversification acceptable"
    elif max_abs_corr < 0.7:
        verdict = "MOYEN — redondance partielle, consider reduce one"
    else:
        verdict = "MAUVAIS — redondance forte, suppression envisageable"

    lines.append(f"Correlation max observee: **{max_abs_corr:.3f}** entre {worst_pair}")
    lines.append("")
    lines.append(f"**{verdict}**")
    lines.append("")

    lines += [
        "## Data gaps",
        "",
        "- Les strats `binance_crypto` ne sont pas dans cette matrice (pas de timeseries",
        "  harmonisees reconstruites depuis les logs worker).",
        "- Les strats paper `alpaca_us` et `ibkr_eu` ne sont pas incluses non plus.",
        "- Next step: reconstruire les returns crypto depuis `data/crypto/wf_results.json`",
        "  ou un backtest dedie pour enrichir la matrice.",
        "",
    ]

    report_path = DOCS_DIR / "portfolio_overlap_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  [ok] {report_path}")

    # Export clustering as JSON for downstream tools
    clusters_path = OUT_DIR / "portfolio_clusters.json"
    with open(clusters_path, "w") as f:
        json.dump({"clusters": clusters, "max_corr": float(max_abs_corr),
                   "verdict": verdict}, f, indent=2)
    print(f"  [ok] {clusters_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
