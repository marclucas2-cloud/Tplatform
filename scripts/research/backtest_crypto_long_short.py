#!/usr/bin/env python3
"""T1-E — Crypto long/short cross-sectional (alts vs BTC).

Strategie market-neutral:
  - Univers : 10 alts majors (ADA, AVAX, BNB, DOGE, DOT, LINK, NEAR, SOL, SUI, XRP)
  - Signal : relatif 20d performance vs BTC
  - Long top 3 alts, short bottom 3 alts, weekly rebalance
  - Beta-neutral par construction (L-S)

Couts Binance spot : 25 bps RT per trade.

Output: daily PnL aggregate, scorecard marginal.
Caveat : data alts disponible depuis 2024 seulement (2Y), resultat preliminaire.
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
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"

UNIVERSE = ["ADA", "AVAX", "BNB", "DOGE", "DOT", "LINK", "NEAR", "SOL", "SUI", "XRP"]
BASE = "BTC"
CAPITAL_PER_LEG = 1_000.0
SPOT_RT = 0.0025  # 25 bps
REBALANCE_DAYS = 7
MOMENTUM_WINDOW = 20
TOP_N = 3


def load_close_series(symbol: str) -> pd.Series:
    """Return close price time series for a symbol (USDT pair)."""
    df = pd.read_parquet(ROOT / "data" / "crypto" / "candles" / f"{symbol}USDT_1d.parquet")
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
    df = df.set_index("timestamp").sort_index()
    return df["close"]


def run_long_short():
    closes = {}
    for sym in UNIVERSE + [BASE]:
        try:
            s = load_close_series(sym)
            closes[sym] = s
        except Exception as e:
            print(f"  {sym}: ERR {e}")

    df = pd.DataFrame(closes).ffill()
    # Common range
    df = df.dropna(how="any")
    print(f"  Common range: {df.index.min().date()} -> {df.index.max().date()} ({len(df)} days)")

    # Compute returns vs BTC (alpha vs base)
    returns = df.pct_change()
    alts_only = returns[UNIVERSE]
    btc_ret = returns[BASE]
    alpha_vs_btc = alts_only.sub(btc_ret, axis=0)

    # 20d cumulative alpha vs BTC per symbol
    mom20 = alpha_vs_btc.rolling(MOMENTUM_WINDOW).sum()

    # Weekly rebalance: on each rebalance day, take top 3 (long) and bottom 3 (short)
    pnl_per_day = []
    positions = {s: 0.0 for s in UNIVERSE}  # +1 long, -1 short, 0 flat
    last_rebalance = None

    for i, dt in enumerate(df.index):
        if i < MOMENTUM_WINDOW + 1:
            pnl_per_day.append(0.0)
            continue
        # Rebalance every REBALANCE_DAYS
        do_rebalance = (last_rebalance is None) or ((dt - last_rebalance).days >= REBALANCE_DAYS)
        if do_rebalance:
            ranks = mom20.loc[dt].sort_values(ascending=False)
            top = list(ranks.head(TOP_N).index)
            bot = list(ranks.tail(TOP_N).index)
            new_positions = {s: 0.0 for s in UNIVERSE}
            for s in top:
                new_positions[s] = 1.0
            for s in bot:
                new_positions[s] = -1.0
            # Transaction cost = sum of abs changes
            rebal_cost = sum(abs(new_positions[s] - positions[s]) for s in UNIVERSE) * CAPITAL_PER_LEG * SPOT_RT
            positions = new_positions
            last_rebalance = dt
        else:
            rebal_cost = 0.0
        # PnL this day = sum(pos[s] * alts_ret[s]) * CAPITAL
        day_pnl = sum(positions[s] * alts_only.loc[dt, s] for s in UNIVERSE) * CAPITAL_PER_LEG
        # Subtract rebalance cost (one-time per rebalance day)
        pnl_per_day.append(day_pnl - rebal_cost)

    pnl = pd.Series(pnl_per_day, index=df.index)
    pnl.name = "crypto_long_short"
    return pnl, df


def main():
    print("=== T1-E : Crypto long/short cross-sectional ===\n")
    pnl, df = run_long_short()

    total = pnl.sum()
    active = (pnl != 0).sum()
    sharpe = pnl.mean() / pnl.std() * np.sqrt(252) if pnl.std() > 0 else 0
    print(f"\nStandalone:")
    print(f"  Active days : {active}")
    print(f"  Total PnL : ${total:+,.0f}")
    print(f"  Sharpe : {sharpe:+.2f}")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    print(f"\nScoring...")
    try:
        sc = score_candidate("crypto_long_short", pnl, baseline, 10_000.0, 1.0)
        print(f"  [{sc.verdict}] score={sc.marginal_score:+.3f} "
              f"dSharpe={sc.delta_sharpe:+.3f} dMaxDD={sc.delta_maxdd:+.2f}pp "
              f"corr={sc.corr_to_portfolio:+.2f}")
    except Exception as e:
        print(f"  SCORE ERR: {e}")
        sc = None

    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    if sc:
        (JSON_OUT_DIR / "T1-05_scorecards.json").write_text(
            json.dumps([sc.to_dict()], indent=2, default=str))

    md = [
        "# T1-E — Crypto long/short cross-sectional (alts vs BTC)",
        "",
        f"**Run** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Univers** : {len(UNIVERSE)} alts ({', '.join(UNIVERSE)})",
        f"**Base** : {BASE} (benchmark)",
        f"**Methodologie** : top {TOP_N} long, bottom {TOP_N} short sur alpha vs BTC {MOMENTUM_WINDOW}d, rebalance {REBALANCE_DAYS}d",
        f"**Data range** : {df.index.min().date()} -> {df.index.max().date()} ({len(df)} days)",
        f"**Sizing** : ${CAPITAL_PER_LEG}/leg, cost {SPOT_RT*100} bps RT",
        "",
        "**Caveat** : data alts disponible depuis 2024 seulement, 2Y pas suffisants pour",
        "WF 5 windows classique. Resultat preliminaire, PROMOTE = necessite data 5Y complete",
        "avant live (plan Tier 1-E note 'KEEP_FOR_RESEARCH likely').",
        "",
        "## Results",
        "",
        f"- Active days : {active}",
        f"- Total PnL : ${total:+,.0f}",
        f"- Sharpe standalone : {sharpe:+.2f}",
        "",
    ]
    if sc:
        md += [
            "## Scorecard",
            "",
            f"- Verdict : **{sc.verdict}**",
            f"- Marginal score : {sc.marginal_score:+.3f}",
            f"- dSharpe : {sc.delta_sharpe:+.3f}",
            f"- dCAGR : {sc.delta_cagr:+.2f}%",
            f"- dMaxDD : {sc.delta_maxdd:+.2f}pp",
            f"- Corr to portfolio : {sc.corr_to_portfolio:+.2f}",
            f"- Max corr to strat : {sc.max_corr_to_strat:+.2f}",
            f"- Tail overlap : {sc.tail_overlap:.0%}",
            f"- Penalties : {', '.join(sc.penalties) if sc.penalties else '-'}",
        ]
    (MD_OUT_DIR / "T1-05_crypto_long_short.md").write_text("\n".join(md), encoding="utf-8")
    print("\nReports OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
