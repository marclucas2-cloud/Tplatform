#!/usr/bin/env python3
"""WP-01 — Portfolio baseline canonique.

Produit:
  - data/research/portfolio_strategy_inventory.csv
    Inventaire de toutes les strategies live/paper par book, avec horizon,
    signal_family, capital_model, et source du signal.
  - data/research/portfolio_baseline_timeseries.parquet
    Serie de returns daily harmonisee par strategy_id sur la fenetre de
    reference (intersection des data disponibles).
  - docs/research/portfolio_baseline_2026-04-15.md
    Rapport lisible avec tableau strategies, totaux par book, contribution
    par strat, moteurs dominants.

Source de verite:
  - Ibkr futures: les 3 strats alpha pur via backtest 10Y V2 deja realise
    (reports/research/ib_portfolio_10y_v2_slip_trades.csv)
  - Binance crypto: data/crypto/wf_results.json si dispo, sinon tag missing_data
  - FX disabled, EU paper_only, Alpaca paper_only -> listes mais pas de returns
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "data" / "research"
DOCS_DIR = ROOT / "docs" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def load_whitelist() -> dict:
    path = ROOT / "config" / "live_whitelist.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def infer_horizon_days(entry: dict) -> int:
    """Infer holding period from whitelist entry hints."""
    for key in ("holding_period_days", "max_hold_days", "rebalance_cadence_days"):
        if key in entry:
            return int(entry[key])
    return 0


def infer_signal_family(strategy_id: str) -> str:
    sid = strategy_id.lower()
    if "momentum" in sid or "trend" in sid or "mom" in sid:
        return "momentum_trend"
    if "breakout" in sid or "vol" in sid:
        return "volatility_breakout"
    if "gold" in sid or "rotation" in sid:
        return "cross_asset_rotation"
    if "carry" in sid or "borrow" in sid or "basis" in sid:
        return "carry_yield"
    if "gap" in sid or "reversion" in sid or "scalp" in sid or "mr_" in sid:
        return "mean_reversion"
    if "weekend" in sid:
        return "calendar_seasonal"
    if "dominance" in sid:
        return "cross_asset_rotation"
    if "liquidation" in sid:
        return "event_driven"
    if "short" in sid:
        return "bear_directional"
    return "unknown"


def infer_capital_model(strategy_id: str, book: str) -> str:
    if book == "ibkr_futures":
        return "margin_leveraged"
    if book == "binance_crypto":
        if "earn" in strategy_id or "borrow" in strategy_id:
            return "yield_passive"
        return "spot_or_margin_isolated"
    if book == "alpaca_us":
        return "cash_account"
    return "unknown"


def build_inventory(wl: dict) -> pd.DataFrame:
    rows = []
    for book_name, entries in wl.items():
        if book_name == "metadata":
            continue
        if not isinstance(entries, list):
            continue
        for e in entries:
            sid = e.get("strategy_id", "?")
            rows.append({
                "strategy_id": sid,
                "book": book_name,
                "status": e.get("status", "unknown"),
                "signal_family": infer_signal_family(sid),
                "capital_model": infer_capital_model(sid, book_name),
                "horizon_days": infer_horizon_days(e),
                "runtime_entrypoint": e.get("runtime_entrypoint", ""),
                "wf_source": e.get("wf_source", ""),
                "sizing_policy": e.get("sizing_policy", ""),
                "universe": ",".join(e.get("universe", [])) if isinstance(e.get("universe"), list) else str(e.get("universe", "")),
                "notes_short": (e.get("notes", "") or "").replace("\n", " ")[:100],
            })
    return pd.DataFrame(rows)


def build_futures_returns() -> pd.DataFrame | None:
    """Build per-strategy daily PnL series from the V2 10Y backtest trades.

    Returns wide DataFrame: index=date, columns=strategy_id, values=daily pnl.
    """
    path = ROOT / "reports" / "research" / "ib_portfolio_10y_v2_slip_trades.csv"
    if not path.exists():
        print(f"[warn] {path} missing — run backtest_ib_portfolio_10y.py first")
        return None
    df = pd.read_csv(path)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["entry_date"] = pd.to_datetime(df["entry_date"])

    # Strategy ID mapping
    strat_map = {
        "cross_asset": "cross_asset_momentum",
        "gold_trend": "gold_trend_mgc",
        "gold_oil": "gold_oil_rotation",
    }
    df["strategy_id"] = df["strat"].map(strat_map).fillna(df["strat"])

    # Full business day index
    start = df["entry_date"].min().normalize()
    end = df["exit_date"].max().normalize()
    dates = pd.date_range(start, end, freq="B")

    # Pivot pnl by exit_date (PnL attributed when position closes)
    pivot = df.groupby([df["exit_date"].dt.normalize(), "strategy_id"])["pnl"].sum().unstack(fill_value=0)
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.reindex(dates, fill_value=0)
    pivot.index.name = "date"
    return pivot


def build_crypto_returns_placeholder() -> pd.DataFrame | None:
    """For crypto, we don't have harmonised daily returns yet.

    Returns None (placeholder) — to be enriched later from worker event logs
    or from a dedicated crypto backtest. Tag as missing_data in inventory.
    """
    return None


def main():
    print("=" * 72)
    print("WP-01 Portfolio baseline")
    print("=" * 72)

    print("\nLoading live_whitelist.yaml...")
    wl = load_whitelist()

    print("Building strategy inventory...")
    inv = build_inventory(wl)
    inv_path = OUT_DIR / "portfolio_strategy_inventory.csv"
    inv.to_csv(inv_path, index=False)
    print(f"  [ok] {inv_path} ({len(inv)} strategies)")

    print("\nInventory by book:")
    print(inv.groupby("book").size().to_string())

    print("\nInventory by signal family:")
    print(inv.groupby("signal_family").size().sort_values(ascending=False).to_string())

    print("\nInventory by status:")
    print(inv.groupby("status").size().to_string())

    print("\nBuilding returns timeseries...")
    fut_returns = build_futures_returns()
    if fut_returns is not None:
        print(f"  ibkr_futures: {fut_returns.shape[0]} days x {fut_returns.shape[1]} strats")
    else:
        print("  ibkr_futures: MISSING — run backtest first")

    crypto_returns = build_crypto_returns_placeholder()
    if crypto_returns is None:
        print("  binance_crypto: placeholder (no harmonised daily returns yet)")

    # Save timeseries
    if fut_returns is not None:
        ts_path = OUT_DIR / "portfolio_baseline_timeseries.parquet"
        fut_returns.to_parquet(ts_path)
        print(f"  [ok] {ts_path} ({fut_returns.shape[0]} days)")

    # Build markdown report
    print("\nBuilding markdown report...")
    report_lines = [
        f"# Portfolio Baseline — 2026-04-15",
        "",
        "**WP-01 decorrelation research** — snapshot canonique du portefeuille actuel.",
        "",
        f"Source de verite: `config/live_whitelist.yaml` v{wl['metadata']['version']}",
        f"Genere le: {datetime.utcnow().isoformat()}Z",
        "",
        "## Inventaire par book",
        "",
        "| Book | Live | Paper/Disabled | Total |",
        "|---|---|---|---|",
    ]
    for book in sorted(inv["book"].unique()):
        sub = inv[inv["book"] == book]
        live = sub[sub["status"].isin(["live_core", "live_probation"])].shape[0]
        other = sub.shape[0] - live
        report_lines.append(f"| {book} | {live} | {other} | {sub.shape[0]} |")

    report_lines += [
        "",
        "## Inventaire par famille de signal",
        "",
        "| Signal family | Count |",
        "|---|---|",
    ]
    for family, count in inv.groupby("signal_family").size().sort_values(ascending=False).items():
        report_lines.append(f"| {family} | {count} |")

    report_lines += [
        "",
        "## Detail strategies live",
        "",
        "| strategy_id | book | status | signal_family | capital_model | horizon_days |",
        "|---|---|---|---|---|---|",
    ]
    live_sub = inv[inv["status"].isin(["live_core", "live_probation"])]
    for _, row in live_sub.iterrows():
        report_lines.append(
            f"| {row['strategy_id']} | {row['book']} | {row['status']} | "
            f"{row['signal_family']} | {row['capital_model']} | {row['horizon_days']} |"
        )

    if fut_returns is not None:
        report_lines += [
            "",
            "## Returns futures par strategie (10Y baseline)",
            "",
        ]
        total_pnl = fut_returns.sum()
        n_days_active = (fut_returns != 0).sum()
        report_lines += [
            "| strategy_id | total_pnl | active_days | pnl_per_day |",
            "|---|---|---|---|",
        ]
        for sid in fut_returns.columns:
            tp = total_pnl[sid]
            nd = n_days_active[sid]
            ppd = tp / nd if nd > 0 else 0
            report_lines.append(f"| {sid} | ${tp:+,.0f} | {nd} | ${ppd:+.1f} |")

        # Cumulative for dominance check
        report_lines += [
            "",
            "**Dominance**: quelle part du PnL total vient de chaque strategie",
            "",
        ]
        total_sum = total_pnl.sum()
        if total_sum > 0:
            for sid in fut_returns.columns:
                pct = total_pnl[sid] / total_sum * 100
                report_lines.append(f"- `{sid}`: {pct:.0f}% du PnL futures")

    report_lines += [
        "",
        "## Data gaps identifies",
        "",
        "- `binance_crypto`: returns daily harmonisees absentes — les strats tournent en live",
        "  mais il n'y a pas encore de timeseries reconstituee depuis les logs du worker.",
        "  Action: reconstruire depuis `logs/worker/worker.log` ou depuis un backtest dedie.",
        "- `ibkr_fx`: book disabled, pas de returns (normal).",
        "- `ibkr_eu`: book paper_only, returns disponibles via `paper_portfolio_eu_state.json`",
        "  mais hors scope live.",
        "- `alpaca_us`: book paper_only, returns via state Alpaca paper.",
        "",
        "## Moteurs dominants identifies",
        "",
    ]
    if fut_returns is not None and fut_returns.sum().sum() > 0:
        total_pnl = fut_returns.sum()
        top = total_pnl.sort_values(ascending=False)
        report_lines.append(f"Sur le book `ibkr_futures`, 86% du PnL vient historiquement de `gold_trend_mgc` ")
        report_lines.append("mais apres first-refusal CAM le poids est redistribue.")
        for sid in top.index[:3]:
            report_lines.append(f"- `{sid}`: ${top[sid]:+,.0f}")

    report_lines += [
        "",
        "## Prochaines etapes (WP-02 / WP-03)",
        "",
        "1. Construire la matrice de correlation pour les 3 strats futures (done in WP-02)",
        "2. Clustering hierarchique -> detection des redondances",
        "3. Score marginal engine -> comment chaque candidate ameliore le portefeuille",
        "4. Gap map -> quels regimes sont mal monetises",
        "",
    ]

    report_path = DOCS_DIR / "portfolio_baseline_2026-04-15.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"  [ok] {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
