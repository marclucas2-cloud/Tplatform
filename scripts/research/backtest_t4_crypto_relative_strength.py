#!/usr/bin/env python3
"""T4-A2 - Crypto cross-sectional long/short research batch.

Research-only batch focused on market-neutral crypto sleeves that can work in
both bull and bear environments:
  - simple alts-vs-BTC cross-sectional alpha
  - beta-adjusted relative-strength rotation closer to STRAT-002

Outputs:
  - docs/research/wf_reports/T4A-02_crypto_relative_strength.md
  - output/research/wf_reports/T4A-02_crypto_relative_strength.json
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
MD_OUT = ROOT / "docs" / "research" / "wf_reports" / "T4A-02_crypto_relative_strength.md"
JSON_OUT = ROOT / "output" / "research" / "wf_reports" / "T4A-02_crypto_relative_strength.json"

BASE = "BTC"
SIMPLE_UNIVERSE = ["ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT", "NEAR", "SUI"]
BETA_UNIVERSE = ["ETH", "SOL", "BNB", "XRP", "ADA", "LINK", "AVAX", "DOT", "NEAR", "SUI"]
CAPITAL_PER_LEG = 1_000.0
BINANCE_SIDE_COST = 0.0013
SHORT_BORROW_DAILY = 0.00005


def load_close_series(symbol: str) -> pd.Series:
    path = ROOT / "data" / "crypto" / "candles" / f"{symbol}USDT_1d.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
    df = df.set_index("timestamp").sort_index()
    return df["close"].rename(symbol)


def load_panel(universe: list[str]) -> pd.DataFrame:
    series = [load_close_series(sym) for sym in [BASE] + universe]
    df = pd.concat(series, axis=1).sort_index().ffill()
    return df.dropna(how="any")


def beta_adjusted_scores(
    hist_returns: pd.DataFrame,
    alts: list[str],
    alpha_window: int,
    beta_window: int,
) -> pd.Series:
    btc_window = hist_returns[BASE].tail(beta_window)
    btc_var = btc_window.var()
    if btc_var == 0 or pd.isna(btc_var):
        return pd.Series(dtype=float)

    btc_cum = (1.0 + hist_returns[BASE].tail(alpha_window)).prod() - 1.0
    scores: dict[str, float] = {}
    for sym in alts:
        alt_window = hist_returns[sym].tail(beta_window)
        beta = alt_window.cov(btc_window) / btc_var
        alt_cum = (1.0 + hist_returns[sym].tail(alpha_window)).prod() - 1.0
        scores[sym] = alt_cum - beta * btc_cum
    return pd.Series(scores).dropna().sort_values(ascending=False)


def simple_scores(hist_returns: pd.DataFrame, alts: list[str], alpha_window: int) -> pd.Series:
    alpha = hist_returns[alts].tail(alpha_window).sum().sub(hist_returns[BASE].tail(alpha_window).sum())
    return alpha.dropna().sort_values(ascending=False)


def run_cross_sectional(
    prices: pd.DataFrame,
    scoring: str,
    alpha_window: int,
    rebalance_days: int,
    top_n: int,
    label: str,
    beta_window: int | None = None,
) -> pd.Series:
    alts = [c for c in prices.columns if c != BASE]
    returns = prices.pct_change().fillna(0.0)
    warmup = max(alpha_window + 1, (beta_window or 0) + 1)

    pnl_per_day: list[float] = []
    positions = {sym: 0.0 for sym in alts}
    last_rebalance: pd.Timestamp | None = None

    for i, dt in enumerate(prices.index):
        if i < warmup:
            pnl_per_day.append(0.0)
            continue

        do_rebalance = last_rebalance is None or (dt - last_rebalance).days >= rebalance_days
        if do_rebalance:
            hist_returns = returns.iloc[:i]
            if scoring == "beta":
                assert beta_window is not None
                scores = beta_adjusted_scores(hist_returns, alts, alpha_window, beta_window)
            else:
                scores = simple_scores(hist_returns, alts, alpha_window)

            if len(scores) >= top_n * 2:
                longs = list(scores.head(top_n).index)
                shorts = list(scores.tail(top_n).index)
                new_positions = {sym: 0.0 for sym in alts}
                for sym in longs:
                    new_positions[sym] = 1.0
                for sym in shorts:
                    new_positions[sym] = -1.0
            else:
                new_positions = {sym: 0.0 for sym in alts}

            turnover_units = sum(abs(new_positions[sym] - positions[sym]) for sym in alts)
            rebal_cost = turnover_units * CAPITAL_PER_LEG * BINANCE_SIDE_COST
            positions = new_positions
            last_rebalance = dt
        else:
            rebal_cost = 0.0

        day_ret = sum(positions[sym] * returns.loc[dt, sym] for sym in alts)
        short_borrow = (
            sum(1 for sym in alts if positions[sym] < 0.0) * CAPITAL_PER_LEG * SHORT_BORROW_DAILY
        )
        pnl_per_day.append(day_ret * CAPITAL_PER_LEG - rebal_cost - short_borrow)

    pnl = pd.Series(pnl_per_day, index=prices.index, dtype=float)
    pnl.name = label
    return pnl


def build_variants() -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]:
    panels = {
        "simple": load_panel(SIMPLE_UNIVERSE),
        "beta": load_panel(BETA_UNIVERSE),
    }
    variants = {
        "crypto_ls_20_7_3": run_cross_sectional(
            panels["simple"],
            scoring="simple",
            alpha_window=20,
            rebalance_days=7,
            top_n=3,
            label="crypto_ls_20_7_3",
        ),
        "crypto_ls_20_7_2": run_cross_sectional(
            panels["simple"],
            scoring="simple",
            alpha_window=20,
            rebalance_days=7,
            top_n=2,
            label="crypto_ls_20_7_2",
        ),
        "alt_rel_strength_14_60_7": run_cross_sectional(
            panels["beta"],
            scoring="beta",
            alpha_window=14,
            beta_window=60,
            rebalance_days=7,
            top_n=3,
            label="alt_rel_strength_14_60_7",
        ),
        "alt_rel_strength_14_90_7": run_cross_sectional(
            panels["beta"],
            scoring="beta",
            alpha_window=14,
            beta_window=90,
            rebalance_days=7,
            top_n=3,
            label="alt_rel_strength_14_90_7",
        ),
        "alt_rel_strength_20_90_7": run_cross_sectional(
            panels["beta"],
            scoring="beta",
            alpha_window=20,
            beta_window=90,
            rebalance_days=7,
            top_n=3,
            label="alt_rel_strength_20_90_7",
        ),
    }
    return variants, panels


def standalone_stats(pnl: pd.Series, initial: float = 10_000.0) -> dict[str, float]:
    active = int((pnl != 0).sum())
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() != 0 else 0.0
    eq = initial + pnl.cumsum()
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    return {
        "active_days": active,
        "total_pnl": float(pnl.sum()),
        "sharpe": sharpe,
        "max_dd_pct": dd * 100.0,
    }


def main() -> int:
    print("=== T4-A2 : Crypto relative strength ===")
    print(f"Cost model: {BINANCE_SIDE_COST * 200:.2f}% RT + {SHORT_BORROW_DAILY * 100:.3f}%/day short borrow proxy")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    variants, panels = build_variants()

    rows = []
    scorecards = []
    for name, pnl in variants.items():
        stats = standalone_stats(pnl)
        sc = score_candidate(name, pnl, baseline, 10_000.0, 1.0)
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
        "# T4-A2 - Crypto relative strength",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Simple universe** : {', '.join(SIMPLE_UNIVERSE)}",
        f"**Beta-adjusted universe** : {', '.join(BETA_UNIVERSE)}",
        f"**Data range simple** : {panels['simple'].index.min().date()} -> {panels['simple'].index.max().date()} ({len(panels['simple'])} days)",
        f"**Data range beta** : {panels['beta'].index.min().date()} -> {panels['beta'].index.max().date()} ({len(panels['beta'])} days)",
        f"**Cost model** : {BINANCE_SIDE_COST * 200:.2f}% round trip + {SHORT_BORROW_DAILY * 100:.3f}%/day short borrow proxy",
        "",
        "## Thesis",
        "",
        "- a bull/bear-robust crypto sleeve should monetize dispersion, not market direction alone",
        "- relative strength vs BTC and beta-adjusted alpha are natural candidates for that job",
        "- weekly rebalancing keeps turnover manageable while preserving cross-sectional signal",
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
        "## Notes",
        "",
        "- `crypto_ls_*` is the simple benchmark family: rank on raw alpha vs BTC",
        "- `alt_rel_strength_*` is the closer production candidate: beta-adjusted and more aligned with STRAT-002 philosophy",
        "- this batch is research-only and does not change live crypto config or strategy code",
    ]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Saved -> {MD_OUT}")
    print(f"Saved -> {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
